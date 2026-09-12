from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Header, HTTPException, Query

from .empleo_admin_catalogo import router
from .periodic import ejecutar_periodico


def _autorizado(x_import_secret: str | None, x_cron_secret: str | None) -> bool:
    import_secret = os.getenv("EMPLOYMENT_IMPORT_SECRET")
    cron_secret = os.getenv("EMPLOYMENT_CRON_SECRET")
    if import_secret and x_import_secret and hmac.compare_digest(x_import_secret, import_secret):
        return True
    if cron_secret and x_cron_secret and hmac.compare_digest(x_cron_secret, cron_secret):
        return True
    return False


@router.post("/periodic")
def periodic_empleo(
    aplicar: bool = Query(default=False),
    dias_solape: int = Query(default=7, ge=1, le=30),
    x_import_secret: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Ejecuta o previsualiza el ciclo periódico de fuentes validadas."""
    if not _autorizado(x_import_secret, x_cron_secret):
        raise HTTPException(status_code=403, detail="No autorizado")
    try:
        return ejecutar_periodico(aplicar=aplicar, dias_solape=dias_solape)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en ciclo periódico de Empleo: {exc}") from exc
