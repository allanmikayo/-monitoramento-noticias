"""Publica arquivos JSON no Dashboard Data Feeder (hub de dashboards do Itaú BBA).

Protocolo do Feeder, em três passos por arquivo (docs/dashboard-data-feeder.md do
repositório `rTaiShan/research-dashboards`):

    POST /api/projects/<projeto>/files/uploads           -> cria a revisão e
                                                            devolve uma URL de PUT
    PUT  <url assinada>                                  -> sobe os bytes direto
    POST .../uploads/<id>/complete                       -> o Feeder confere e
                                                            libera para publicação

Se o PUT falhar, avisa o Feeder (`/failed`) antes de propagar o erro. Subir o
mesmo nome de arquivo de novo cria a revisão seguinte; o dashboard lê sempre a
mais recente, que chega no Mindassets no ciclo de publicação seguinte (~5 min).

Variáveis de ambiente (no GitHub Actions: `DASHBOARD_FEEDER_PROJECT_ID` como
variável e `DASHBOARD_FEEDER_API_KEY` como SECRET; a chave nunca vai para o
código nem para o .env versionado):

    DASHBOARD_FEEDER_URL         ex.: https://dashboard-data-feeder.vercel.app
    DASHBOARD_FEEDER_PROJECT_ID
    DASHBOARD_FEEDER_API_KEY

Uso:
    python -m scripts.publicar_feeder saida_dashboard/*.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
URL_PADRAO = "https://dashboard-data-feeder.vercel.app"


def _post(base: str, chave: str, caminho: str, corpo: dict | None = None) -> dict:
    req = Request(
        f"{base}{caminho}",
        data=json.dumps(corpo or {}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {chave}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except HTTPError as erro:
        # A mensagem do Feeder diz o motivo (chave, projeto, permissão).
        detalhe = erro.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {erro.code} em {caminho}: {detalhe}") from erro


def publicar(arquivo: Path, base: str, projeto: str, chave: str) -> int:
    """Sobe um arquivo e devolve o número da revisão criada."""
    bruto = arquivo.read_bytes()
    raiz = f"/api/projects/{projeto}/files/uploads"
    inicio = _post(base, chave, raiz, {
        "name": arquivo.name,
        "sourceFileName": arquivo.name,
        "contentType": "application/json",
        "sizeBytes": len(bruto),
        "sourceModifiedAt": datetime.fromtimestamp(arquivo.stat().st_mtime, timezone.utc).isoformat(),
    })
    update_id = inicio["update"]["id"]
    upload = inicio["upload"]
    try:
        with urlopen(Request(upload["url"], data=bruto, method="PUT",
                             headers=upload["requiredHeaders"]), timeout=300):
            pass
    except HTTPError as erro:
        _avisar_falha(base, chave, f"{raiz}/{update_id}/failed",
                      {"reason": "storage_rejected", "storageStatus": erro.code})
        raise
    except Exception:
        _avisar_falha(base, chave, f"{raiz}/{update_id}/failed", {"reason": "network_error"})
        raise
    fim = _post(base, chave, f"{raiz}/{update_id}/complete")
    return fim["update"]["revision"]


def _avisar_falha(base: str, chave: str, caminho: str, corpo: dict) -> None:
    try:
        _post(base, chave, caminho, corpo)
    except Exception:  # o erro original é o que importa
        logger.warning("não consegui avisar o Feeder da falha (%s)", caminho)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    base = (os.environ.get("DASHBOARD_FEEDER_URL") or URL_PADRAO).rstrip("/")
    # strip(): espaço ou quebra de linha colados junto com o valor no cadastro
    # do segredo viram parte da chave e o Feeder responde 401.
    projeto = (os.environ.get("DASHBOARD_FEEDER_PROJECT_ID") or "").strip()
    chave = (os.environ.get("DASHBOARD_FEEDER_API_KEY") or "").strip()
    if not projeto or not chave:
        logger.error("Faltam DASHBOARD_FEEDER_PROJECT_ID e/ou DASHBOARD_FEEDER_API_KEY.")
        return 2
    arquivos = [Path(a) for a in argv]
    if not arquivos:
        logger.error("Nenhum arquivo informado.")
        return 2
    falhas = 0
    for arq in arquivos:
        try:
            rev = publicar(arq, base, projeto, chave)
            logger.info("%s publicado (revisão %s, %.2f MB)", arq.name, rev, arq.stat().st_size / 1e6)
        except Exception as erro:  # segue com os outros; o job fica vermelho no fim
            falhas += 1
            logger.error("%s falhou: %s", arq.name, erro)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
