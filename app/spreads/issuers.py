"""Emissores: criação/casamento, taxonomia e recálculo do rating vigente.

Camada entre os dados crus (planilha de taxonomia, scrapers de rating,
API da Anbima) e as tabelas `issuers` / `issuer_ratings` /
`issuer_rating_atual`.

Princípio que atravessa o módulo: **casar por chave normalizada, nunca
por aproximação**. `issuer_key` resolve grafia (acento, pontuação, forma
societária); o que sobra vai pra revisão manual e vira `IssuerAlias`.
Fuzzy matching entre nomes de empresa erra silenciosamente e para os dois
lados -- juntar "ÁGUAS DO RIO 1" com "ÁGUAS DO RIO 4" atribuiria rating e
setor errados a uma emissão inteira.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Debenture,
    Issuer,
    IssuerAlias,
    IssuerRating,
    IssuerRatingAtual,
    IssuerRatingPeriodo,
)
from .issuer_key import issuer_key
from .ratings import calcular_rating_medio

AGENCIAS = ("FITCH", "SP", "MOODYS")

# Origem da taxonomia, em ordem de precedência: uma carga automática nunca
# sobrescreve o que o Allan editou à mão na Administração.
ORIGEM_MANUAL = "MANUAL"
ORIGEM_SNAPSHOT = "SNAPSHOT"
ORIGEM_ANBIMA = "ANBIMA"
_PRECEDENCIA = {ORIGEM_MANUAL: 3, ORIGEM_SNAPSHOT: 2, ORIGEM_ANBIMA: 1, None: 0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Resolução de emissor
# ---------------------------------------------------------------------------

def resolver_issuer(db: Session, nome: str | None) -> Issuer | None:
    """Emissor correspondente a um nome, ou `None` se não houver.

    Ordem de tentativa:
    1. `issuers.key` == chave normalizada do nome;
    2. `issuer_aliases.alias_key` == a mesma chave (casamento manual);

    Não cria nada — quem quer criar chama `obter_ou_criar_issuer`. Separado
    de propósito: o job diário da Anbima PODE criar emissor novo, mas a
    carga de ratings NÃO deve (rating de emissor que não tem papel nosso
    é ruído, e criar o registro esconderia um erro de casamento).
    """
    chave = issuer_key(nome)
    if not chave:
        return None
    issuer = db.scalar(select(Issuer).where(Issuer.key == chave))
    if issuer is not None:
        return issuer
    alias = db.scalar(select(IssuerAlias).where(IssuerAlias.alias_key == chave))
    return db.get(Issuer, alias.issuer_id) if alias else None


def obter_ou_criar_issuer(
    db: Session,
    nome: str | None,
    *,
    nome_anbima: str | None = None,
    cnpj: str | None = None,
) -> Issuer | None:
    """Como `resolver_issuer`, mas cria o emissor se não existir.

    Emissor recém-criado nasce SEM taxonomia (`setor=None`,
    `taxonomia_origem='ANBIMA'`) — é exatamente o que a tela de
    Administração lista como "a classificar". Preencher com um palpite
    aqui seria pior: o dado errado não se distingue do certo depois.
    """
    chave = issuer_key(nome)
    if not chave:
        return None
    issuer = resolver_issuer(db, nome)
    if issuer is not None:
        issuer.last_seen_at = _now()
        if nome_anbima and not issuer.nome_anbima:
            issuer.nome_anbima = nome_anbima
        if cnpj and not issuer.cnpj:
            issuer.cnpj = cnpj
        return issuer
    issuer = Issuer(
        key=chave,
        nome=(nome or "").strip(),
        nome_anbima=nome_anbima or (nome or "").strip(),
        cnpj=cnpj,
        taxonomia_origem=ORIGEM_ANBIMA,
    )
    db.add(issuer)
    db.flush()
    return issuer


def aplicar_taxonomia(
    issuer: Issuer,
    *,
    setor: str | None,
    sub_setor: str | None,
    grupo_economico: str | None,
    origem: str,
) -> bool:
    """Grava setor/subsetor/grupo respeitando precedência de origem.

    Devolve `True` se mudou alguma coisa. Uma carga `SNAPSHOT` não
    sobrescreve `MANUAL`: se o Allan corrigiu o setor de um emissor na
    Administração, rodar o seed de novo não pode desfazer a correção —
    esse é o caminho clássico pro trabalho manual evaporar sem ninguém
    notar.
    """
    if _PRECEDENCIA.get(origem, 0) < _PRECEDENCIA.get(issuer.taxonomia_origem, 0):
        return False
    mudou = False
    for campo, valor in (
        ("setor", setor),
        ("sub_setor", sub_setor),
        ("grupo_economico", grupo_economico),
    ):
        valor = (valor or "").strip() or None
        if valor is not None and getattr(issuer, campo) != valor:
            setattr(issuer, campo, valor)
            mudou = True
    if mudou:
        issuer.taxonomia_origem = origem
        issuer.last_seen_at = _now()
    return mudou


def vincular_debentures(db: Session, ticker_para_nome: dict[str, str] | None = None) -> dict:
    """Preenche `debentures.issuer_id` para todo papel que casar.

    Dois caminhos, nesta ordem:

    1. **Ticker** (`ticker_para_nome`) — casamento EXATO. O ticker é um
       identificador de verdade, não texto livre: se a planilha diz que
       `SBSP15` é da SABESP, é da SABESP, ponto. É o caminho preferencial.
    2. **Nome normalizado** (`issuer_key`) — para o papel que não está na
       planilha (emissão nova, posterior à carga).

    QUANDO O TICKER CASA E O NOME NÃO, registra um `IssuerAlias`
    automaticamente. Isso é seguro justamente porque a evidência é o
    ticker, não uma semelhança de string: o banco chama a empresa de
    "CIA. DE SANEAMENTO BÁSICO DO ESTADO DE SP - SABESP" e a planilha de
    "COMPANHIA DE SANEAMENTO BASICO DO ESTADO DE SAO PAULO - SABESP" —
    "SP" e "SAO PAULO" nunca vão colapsar por normalização, mas o ticker
    prova que é a mesma empresa. O alias faz a próxima emissão dessa
    empresa casar sozinha, sem passar de novo pela revisão manual.

    Idempotente: repassa tudo e só grava o que mudou.
    """
    ticker_para_nome = ticker_para_nome or {}
    por_ticker = por_nome = 0
    aliases_criados: list[tuple[str, str]] = []
    revisar: list[tuple[str, str, str]] = []
    sem_match: dict[str, int] = {}

    for deb in db.scalars(select(Debenture)).all():
        issuer = None
        via_ticker = False

        nome_planilha = ticker_para_nome.get((deb.codigo or "").strip().upper())
        if nome_planilha:
            issuer = resolver_issuer(db, nome_planilha)
            via_ticker = issuer is not None
        if issuer is None:
            issuer = resolver_issuer(db, deb.nome)

        if issuer is None:
            nome = (deb.nome or "").strip() or "(sem nome)"
            sem_match[nome] = sem_match.get(nome, 0) + 1
            continue

        if deb.issuer_id != issuer.id:
            deb.issuer_id = issuer.id

        if via_ticker:
            por_ticker += 1
            chave_banco = issuer_key(deb.nome)
            # Só considera alias se a grafia do banco não resolve sozinha
            # -- evita encher a tabela de alias redundante.
            if chave_banco and chave_banco != issuer.key and resolver_issuer(db, deb.nome) is None:
                if _mesmo_emissor_provavel(chave_banco, issuer.key):
                    db.add(IssuerAlias(
                        issuer_id=issuer.id,
                        alias_key=chave_banco,
                        alias_nome=(deb.nome or "").strip(),
                    ))
                    db.flush()
                    aliases_criados.append(((deb.nome or "").strip(), issuer.nome))
                else:
                    revisar.append((deb.codigo, (deb.nome or "").strip(), issuer.nome))
        else:
            por_nome += 1

    db.commit()
    return {
        "ligadas": por_ticker + por_nome,
        "por_ticker": por_ticker,
        "por_nome": por_nome,
        "aliases_criados": aliases_criados,
        "revisar": revisar,
        "sem_match": sem_match,
    }


# Palavras genéricas demais pra servir de evidência de que dois nomes são
# a mesma empresa -- metade do mercado de debênture tem "ENERGIA" ou
# "TRANSMISSORA" no nome.
_TOKENS_GENERICOS = {
    "ENERGIA", "ENERGIAS", "ELETRICA", "ELETRICAS", "ENERGETICA",
    "TRANSMISSORA", "TRANSMISSAO", "DISTRIBUIDORA", "DISTRIBUICAO",
    "GERACAO", "CONCESSIONARIA", "CONCESSOES", "INVESTIMENTOS",
    "SANEAMENTO", "TRANSPORTES", "LOGISTICA", "SERVICOS", "COMERCIO",
    "INDUSTRIA", "BRASIL", "BRASILEIRA", "NACIONAL", "SUL", "NORTE",
    "NORDESTE", "SUDESTE", "CENTRO", "OESTE", "SPE", "EMPREENDIMENTOS",
}


def _mesmo_emissor_provavel(chave_a: str, chave_b: str) -> bool:
    """Há evidência DE NOME de que duas chaves são o mesmo emissor?

    Usado só para decidir se um casamento por ticker vira `IssuerAlias`
    (que passa a valer pra toda emissão futura daquele nome) ou fica
    marcado pra revisão manual.

    A distinção importa: o ticker prova que AQUELE papel é daquele
    emissor, e isso basta pra ligar o papel. Mas generalizar "todo papel
    cujo emissor o banco chama de X é do emissor Y" a partir de um caso
    só é um salto -- se o nome no banco tiver sido atualizado por
    renomeação societária, ok; se for outra empresa do mesmo grupo, o
    alias passa a atribuir setor e rating errados a emissões futuras.

    Critério: pelo menos um token NÃO-GENÉRICO em comum. "CACHOEIRA
    PAULISTA TRANS" e "CACHOEIRA PAULISTA TRANSMISSORA" compartilham
    CACHOEIRA e PAULISTA -> alias automático. "EDP TRANSMISSAO" e
    "HORIZON TRANSMISSAO ES" só compartilham TRANSMISSAO, que é genérico
    -> vai pra revisão, mesmo que a renomeação seja real.
    """
    a = {t for t in chave_a.split() if t not in _TOKENS_GENERICOS and len(t) > 2}
    b = {t for t in chave_b.split() if t not in _TOKENS_GENERICOS and len(t) > 2}
    return bool(a & b)


# ---------------------------------------------------------------------------
# Rating vigente
# ---------------------------------------------------------------------------

def _rating_vigente(db: Session, issuer_id: int, agencia: str,
                    codigo: str | None = None) -> IssuerRating | None:
    """Ação de rating mais recente da agência para o emissor.

    Desempate por `id` decrescente além da data: duas ações no MESMO dia
    (acontece quando o scraper reprocessa e a agência publicou revisão e
    confirmação juntas) precisam de ordem determinística, senão o rating
    vigente oscila entre execuções sem nada ter mudado.
    """
    return db.scalar(
        select(IssuerRating)
        .where(
            IssuerRating.issuer_id == issuer_id,
            IssuerRating.codigo == codigo,
            IssuerRating.agencia == agencia,
        )
        .order_by(IssuerRating.data_acao.desc(), IssuerRating.id.desc())
        .limit(1)
    )


def recalcular_rating_atual(db: Session, issuer_id: int) -> IssuerRatingAtual:
    """Recalcula `issuer_rating_atual` de UM emissor a partir do histórico."""
    vigentes = {ag: _rating_vigente(db, issuer_id, ag) for ag in AGENCIAS}
    fitch = vigentes["FITCH"].rating if vigentes["FITCH"] else None
    sp = vigentes["SP"].rating if vigentes["SP"] else None
    moodys = vigentes["MOODYS"].rating if vigentes["MOODYS"] else None

    calc = calcular_rating_medio(fitch, sp, moodys)

    atual = db.get(IssuerRatingAtual, issuer_id)
    if atual is None:
        atual = IssuerRatingAtual(issuer_id=issuer_id)
        db.add(atual)

    atual.fitch, atual.sp, atual.moodys = fitch, sp, moodys
    atual.fitch_data = vigentes["FITCH"].data_acao if vigentes["FITCH"] else None
    atual.sp_data = vigentes["SP"].data_acao if vigentes["SP"] else None
    atual.moodys_data = vigentes["MOODYS"].data_acao if vigentes["MOODYS"] else None
    atual.rating_medio = calc["rating"]
    atual.notch_medio = calc["peso"]
    atual.n_agencias = calc["n_agencias"]
    atual.desconhecidos_json = (
        json.dumps(calc["desconhecidos"], ensure_ascii=False) if calc["desconhecidos"] else None
    )
    atual.atualizado_em = _now()
    return atual


def recalcular_todos_ratings(db: Session) -> dict:
    """Recalcula o rating vigente de todos os emissores.

    Reconstrução completa, não incremental: é barato (algumas centenas de
    emissores) e elimina a classe de bug em que o vigente fica defasado
    porque alguém esqueceu de invalidar. Chamado no fim de toda carga de
    ratings.
    """
    total = com_rating = 0
    distrib: dict[str, int] = {}
    desconhecidos: dict[str, int] = {}
    for issuer in db.scalars(select(Issuer)).all():
        atual = recalcular_rating_atual(db, issuer.id)
        total += 1
        if atual.n_agencias:
            com_rating += 1
        distrib[atual.rating_medio or "N.A."] = distrib.get(atual.rating_medio or "N.A.", 0) + 1
        if atual.desconhecidos_json:
            for ag, val in json.loads(atual.desconhecidos_json).items():
                desconhecidos[f"{ag}:{val}"] = desconhecidos.get(f"{ag}:{val}", 0) + 1
    db.commit()
    # Os períodos históricos derivam das mesmas ações, então recalcular um
    # sem o outro deixa a tela "hoje" e a tela "histórico" discordando --
    # divergência silenciosa e difícil de rastrear depois. Andam juntos.
    periodos = reconstruir_periodos_rating(db)
    return {
        "emissores": total,
        "com_rating": com_rating,
        "distribuicao": distrib,
        "desconhecidos": desconhecidos,
        "periodos": periodos["periodos"],
    }


def reconstruir_periodos_rating(db: Session, issuer_id: int | None = None) -> dict:
    """(Re)constrói `issuer_rating_periodo` a partir do histórico de ações.

    Uma linha por MUDANÇA do conjunto de ratings do emissor. Percorre as
    ações em ordem de data mantendo o "estado" das três agências: cada
    data em que o estado muda fecha o período anterior e abre um novo.

    Reconstrução completa (não incremental) do emissor inteiro: é barato e
    elimina a classe de bug em que um período fica com `data_fim` errada
    porque chegou uma ação retroativa (a agência publica com atraso, e o
    scraper pega a data do FATO, não a da captura -- então ação com data
    anterior à última já gravada é normal, não exceção).

    O rating médio de cada período usa a mesma `calcular_rating_medio` do
    rating atual -- a regra de negócio é uma só, aqui e lá.
    """
    alvos = (
        [issuer_id]
        if issuer_id is not None
        else [i for (i,) in db.query(Issuer.id).all()]
    )
    periodos_criados = 0

    for iid in alvos:
        # SÓ os DERIVADO. Os HISTORICO vêm da view final do Allan e são
        # congelados por decisão dele (ver models.IssuerRatingPeriodo.origem)
        # -- apagá-los aqui alteraria curvas de 2025 já analisadas.
        db.query(IssuerRatingPeriodo).filter(
            IssuerRatingPeriodo.issuer_id == iid,
            IssuerRatingPeriodo.origem == "DERIVADO",
        ).delete(synchronize_session=False)

        todas = db.scalars(
            select(IssuerRating)
            .where(IssuerRating.issuer_id == iid)
            .order_by(IssuerRating.data_acao, IssuerRating.id)
        ).all()
        if not todas:
            continue

        # Uma linha do tempo INDEPENDENTE por escopo: `codigo=None` é a do
        # emissor, cada `codigo` preenchido é a daquela emissão. Misturar
        # as duas faria a mudança de um papel específico "vazar" pro
        # rating do emissor (e vice-versa) -- ver o caso COSAN no
        # docstring de IssuerRating.codigo.
        por_escopo: dict[str | None, list] = {}
        for a in todas:
            por_escopo.setdefault(a.codigo, []).append(a)

        # Fim da janela observada por escopo: ação de rating anterior a
        # isso já está representada na série da view (que é o que o Allan
        # analisou) e não deve gerar período concorrente.
        observado_ate: dict[str | None, date] = {}
        for p_ in db.scalars(select(IssuerRatingPeriodo).where(
                IssuerRatingPeriodo.issuer_id == iid,
                IssuerRatingPeriodo.origem == "HISTORICO")).all():
            atual = observado_ate.get(p_.codigo)
            if atual is None or p_.data_inicio > atual:
                observado_ate[p_.codigo] = p_.data_inicio

        for codigo, acoes in por_escopo.items():
            corte = observado_ate.get(codigo)
            if corte is not None:
                acoes = [a for a in acoes if a.data_acao > corte]
                if not acoes:
                    continue
            # Agrupa por data: várias agências podem publicar no mesmo dia,
            # e o período tem que refletir o estado DEPOIS de todas elas.
            estado: dict[str, str] = {}
            linhas: list[tuple] = []
            for acao in acoes:
                if linhas and linhas[-1][0] == acao.data_acao:
                    linhas.pop()  # mesma data -- recalcula com o estado atualizado
                # rating None = RETIRADA de cobertura: a agência deixa de
                # contar na média, não fica com o valor antigo pendurado.
                # O Allan definiu isso explicitamente (04/08/2026): "onde
                # está preenchido com N.R. é pq n tem rating para essas
                # agências, para o rating médio existe a classificação
                # N.A." -- então carregar o valor anterior adiante
                # produziria rating onde ele não existe mais.
                if acao.rating is None:
                    estado.pop(acao.agencia, None)
                else:
                    estado[acao.agencia] = acao.rating
                calc = calcular_rating_medio(
                    estado.get("FITCH"), estado.get("SP"), estado.get("MOODYS")
                )
                linhas.append((
                    acao.data_acao,
                    estado.get("FITCH"), estado.get("SP"), estado.get("MOODYS"),
                    calc["rating"], calc["peso"], calc["n_agencias"],
                ))

            # Descarta período que não mudou nada em relação ao anterior --
            # reafirmação de rating não é mudança de balde, e uma linha a
            # mais aqui só encarece a junção.
            limpas: list[tuple] = []
            for linha in linhas:
                if limpas and limpas[-1][1:] == linha[1:]:
                    continue
                limpas.append(linha)

            # A primeira ação nova FECHA o período histórico que estava
            # aberto -- é assim que "presente vivo" convive com "passado
            # imutável" sem salto e sem sobreposição.
            if limpas:
                aberto = db.scalar(select(IssuerRatingPeriodo).where(
                    IssuerRatingPeriodo.issuer_id == iid,
                    IssuerRatingPeriodo.codigo == codigo,
                    IssuerRatingPeriodo.origem == "HISTORICO",
                    IssuerRatingPeriodo.data_fim.is_(None)))
                if aberto is not None:
                    aberto.data_fim = limpas[0][0]

            for i, linha in enumerate(limpas):
                data_fim = limpas[i + 1][0] if i + 1 < len(limpas) else None
                db.add(IssuerRatingPeriodo(
                    issuer_id=iid,
                    codigo=codigo,
                    origem="DERIVADO",
                    data_inicio=linha[0],
                    data_fim=data_fim,
                    fitch=linha[1], sp=linha[2], moodys=linha[3],
                    rating_medio=linha[4], notch_medio=linha[5], n_agencias=linha[6],
                ))
                periodos_criados += 1

    db.commit()
    return {"emissores": len(alvos), "periodos": periodos_criados}


def gravar_periodos_historicos(
    db: Session,
    issuer_id: int,
    codigo: str | None,
    serie: list[tuple[date, str | None, int | None, dict]],
) -> int:
    """Grava períodos CONGELADOS (origem='HISTORICO') a partir da série
    observada de rating médio de um papel/emissor.

    `serie` é uma lista de `(data, rating_medio, notch, agencias)` já
    ordenada e sem repetição consecutiva. O rating médio vem COPIADO da
    view final do Allan, não recalculado -- ver
    models.IssuerRatingPeriodo.origem pro porquê.

    Substitui os HISTORICO existentes do mesmo escopo (recarregar o mesmo
    arquivo não duplica), mas nunca toca nos DERIVADO.
    """
    db.query(IssuerRatingPeriodo).filter(
        IssuerRatingPeriodo.issuer_id == issuer_id,
        IssuerRatingPeriodo.codigo == codigo,
        IssuerRatingPeriodo.origem == "HISTORICO",
    ).delete(synchronize_session=False)

    for i, (data_inicio, rating_medio, notch, agencias) in enumerate(serie):
        # O último período fica ABERTO (`data_fim=None`): o rating
        # continua valendo até chegar uma ação NOVA.
        #
        # BUG REAL (04/08/2026): a primeira versão fechava a janela na
        # última data observada e deixava o cálculo derivado assumir dali
        # em diante. Resultado medido: 23% dos escopos davam um SALTO
        # artificial na fronteira (185 casos de "N.A. -> AAA"), porque o
        # derivado recalculava a partir de ações que discordavam da view.
        # Isso viola a regra do Allan -- "quando eu atualizo os spreads na
        # data x, se tiver alguma rating action na data x aí vai receber
        # esse rating, e os de datas anteriores permanecerão inalteráveis".
        # Sem ação nova, nada muda; com ação nova, ela fecha este período.
        data_fim = serie[i + 1][0] if i + 1 < len(serie) else None
        db.add(IssuerRatingPeriodo(
            issuer_id=issuer_id,
            codigo=codigo,
            origem="HISTORICO",
            data_inicio=data_inicio,
            data_fim=data_fim,
            fitch=agencias.get("FITCH"),
            sp=agencias.get("SP"),
            moodys=agencias.get("MOODYS"),
            rating_medio=rating_medio,
            notch_medio=notch,
            n_agencias=len([v for v in agencias.values() if v]),
        ))
    return len(serie)


def rating_em(db: Session, issuer_id: int, quando: date,
              codigo: str | None = None) -> IssuerRatingPeriodo | None:
    """Rating vigente de um emissor NUMA data (junção as-of).

    Devolve `None` se a data for anterior ao primeiro rating conhecido --
    de propósito, em vez de estender o rating mais antigo pra trás.

    Testado contra o snapshot (04/08/2026): estender pra trás o rating
    mais antigo derruba a aderência de 100% pra 84,6%, porque o histórico
    de spread do Allan (jan/2025) começa bem antes da base de ratings dele
    (abr/2026) e os ratings daquele período eram outros. Inventar
    cobertura onde não há produz um gráfico que parece completo e está
    errado; devolver `None` deixa o buraco visível.
    """
    def _busca(cod: str | None, origem: str):
        return db.scalar(
            select(IssuerRatingPeriodo)
            .where(
                IssuerRatingPeriodo.issuer_id == issuer_id,
                IssuerRatingPeriodo.codigo == cod,
                IssuerRatingPeriodo.origem == origem,
                IssuerRatingPeriodo.data_inicio <= quando,
                (IssuerRatingPeriodo.data_fim.is_(None))
                | (IssuerRatingPeriodo.data_fim > quando),
            )
            .limit(1)
        )

    # Precedência, do mais forte pro mais fraco:
    #
    # 1. HISTORICO do próprio papel   -- o que a view do Allan mostrava
    # 2. DERIVADO do próprio papel    -- rating específico da emissão
    # 3. HISTORICO do emissor
    # 4. DERIVADO do emissor
    #
    # Escopo mais específico primeiro (o caso COSAN: tranches com rating
    # próprio, ver models.IssuerRating.codigo), e dentro do escopo o
    # HISTORICO congelado vence -- é o que garante que curva de 2025 não
    # muda quando o scraper trouxer uma ação nova com data retroativa.
    candidatos = []
    if codigo is not None:
        candidatos += [(codigo, "HISTORICO"), (codigo, "DERIVADO")]
    candidatos += [(None, "HISTORICO"), (None, "DERIVADO")]
    for cod, origem in candidatos:
        achado = _busca(cod, origem)
        if achado is not None:
            return achado
    return None


def registrar_acao_rating(
    db: Session,
    issuer: Issuer,
    *,
    agencia: str,
    rating: str | None,
    data_acao: date,
    codigo: str | None = None,
    rating_anterior: str | None = None,
    perspectiva: str | None = None,
    perspectiva_anterior: str | None = None,
    acao: str | None = None,
    link: str | None = None,
    origem: str = "MANUAL",
) -> IssuerRating | None:
    """Grava uma ação de rating, sem duplicar.

    Devolve `None` se a ação já existia (mesma agência+data+rating) — o
    scraper reprocessa janelas que se sobrepõem, então isso acontece o
    tempo todo e não é erro.
    """
    if agencia not in AGENCIAS:
        raise ValueError(f"agência inválida: {agencia!r} -- use uma de {AGENCIAS}")
    ja_existe = db.scalar(
        select(IssuerRating).where(
            IssuerRating.issuer_id == issuer.id,
            IssuerRating.codigo == codigo,
            IssuerRating.agencia == agencia,
            IssuerRating.data_acao == data_acao,
            IssuerRating.rating == rating,
        )
    )
    if ja_existe is not None:
        return None
    acao_obj = IssuerRating(
        issuer_id=issuer.id,
        codigo=codigo,
        agencia=agencia,
        rating=rating,
        rating_anterior=rating_anterior,
        perspectiva=perspectiva,
        perspectiva_anterior=perspectiva_anterior,
        acao=acao,
        data_acao=data_acao,
        link=link,
        origem=origem,
    )
    db.add(acao_obj)
    # FLUSH OBRIGATÓRIO. Sem ele o `select` acima não enxerga o que já foi
    # adicionado nesta mesma sessão e ainda não foi para o banco, e a
    # verificação de duplicata só funciona entre execuções -- não dentro
    # do mesmo lote.
    #
    # BUG REAL (04/08/2026): a carga do snapshot estourou
    # `IntegrityError: UNIQUE constraint failed` na AEGEA. A fonte traz a
    # MESMA ação de rating repetida uma vez por ticker (33 linhas
    # idênticas, porque o rating é do emissor e a planilha é por papel);
    # sem o flush, as 33 viravam 33 INSERTs pendentes e o banco rejeitava
    # o segundo. É exatamente o caso de uso normal deste seed, não uma
    # borda.
    db.flush()
    return acao_obj
