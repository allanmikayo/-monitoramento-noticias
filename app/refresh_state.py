"""Estado em memória da varredura manual em andamento -- usado só para o
endpoint de status que alimenta a barra de progresso no dashboard. Não
precisa ser persistido (se o servidor reiniciar no meio, o estado reseta,
o que é o comportamento certo)."""
from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {
    "running": False,
    "current": 0,
    "total": 0,
    "source_name": "",
    "summary": None,   # preenchido quando termina
    "error": None,      # erro inesperado que derrubou a thread inteira
}


def is_running() -> bool:
    with _lock:
        return _state["running"]


def start(total: int) -> bool:
    """Marca como iniciado. Retorna False se já havia uma varredura rodando
    (nesse caso não faz nada -- evita duas varreduras simultâneas)."""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, current=0, total=total, source_name="", summary=None, error=None)
        return True


def update(current: int, total: int, source_name: str) -> None:
    with _lock:
        _state.update(current=current, total=total, source_name=source_name)


def finish(summary: dict) -> None:
    with _lock:
        _state.update(running=False, summary=summary, source_name="")


def fail(error: str) -> None:
    with _lock:
        _state.update(running=False, error=error, source_name="")


def snapshot() -> dict:
    with _lock:
        return dict(_state)
