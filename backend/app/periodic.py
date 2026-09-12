from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from .boe_local_import import previsualizar_importacion_boe_local
from .bop_valencia_patch import diagnosticar_bop, importar_bop_valencia
from .gva_estatal_service import importar_gva_estatal


DIAS_SOLAPE_DEFECTO = 7


def ejecutar_periodico(*, aplicar: bool = False, hoy: date | None = None, dias_solape: int = DIAS_SOLAPE_DEFECTO) -> dict[str, Any]:
    """Orquesta las fuentes validadas de la primera fase de Empleo.

    Fuentes incluidas:
    - BOP Valencia: Diputación y entidades locales de la provincia.
    - BOE local: altas/vinculaciones de convocatorias administrativas valencianas.
    - GVA estatal + seguimiento DOGV directo de convocatorias activas.

    En SOLO_REVISION no se escribe en BD. El BOP no dispone de un modo de
    previsualización completo, por lo que en revisión se ejecuta únicamente su
    diagnóstico de lectura. En APLICADO se usa una ventana solapada para tolerar
    caídas puntuales sin depender de que el cron haya ejecutado el día anterior.
    """
    if dias_solape < 1 or dias_solape > 30:
        raise ValueError("dias_solape debe estar entre 1 y 30")

    fecha_hoy = hoy or date.today()
    desde = fecha_hoy - timedelta(days=dias_solape - 1)
    resultado: dict[str, Any] = {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "escrituras_bd": aplicar,
        "desde": desde.isoformat(),
        "hasta": fecha_hoy.isoformat(),
        "dias_solape": dias_solape,
        "fuentes": {},
    }

    if aplicar:
        resultado["fuentes"]["bop_valencia"] = importar_bop_valencia(
            historico=True,
            dias=dias_solape,
        )
    else:
        headers = {
            "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
            "Accept-Language": "es-ES,es;q=0.9",
        }
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
            resultado["fuentes"]["bop_valencia"] = {
                "modo": "SOLO_DIAGNOSTICO",
                "resultado": diagnosticar_bop(client, fecha=fecha_hoy.isoformat()),
            }

    resultado["fuentes"]["boe_local"] = previsualizar_importacion_boe_local(
        hasta=fecha_hoy,
        dias=dias_solape,
        aplicar=aplicar,
    )

    resultado["fuentes"]["gva"] = importar_gva_estatal(
        desde=desde,
        hasta=fecha_hoy,
        aplicar=aplicar,
    )

    return resultado
