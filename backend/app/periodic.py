from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from .boe_local_import import previsualizar_importacion_boe_local
from .bop_valencia_patch import diagnosticar_bop, importar_bop_valencia
from .bop_valencia_municipios import importar_municipales_bop
from .gva_estatal_service import importar_gva_estatal


DIAS_SOLAPE_DEFECTO = 7


def ejecutar_periodico(*, aplicar: bool = False, hoy: date | None = None, dias_solape: int = DIAS_SOLAPE_DEFECTO) -> dict[str, Any]:
    """Orquesta las fuentes validadas de la primera fase de Empleo.

    Fuentes incluidas:
    - BOP Valencia Diputación.
    - BOP Valencia municipal: altas y seguimientos de ayuntamientos.
    - BOE local: altas/vinculaciones de convocatorias administrativas valencianas.
    - GVA estatal + seguimiento DOGV directo de convocatorias activas.

    En SOLO_REVISION no se escribe en BD. La parte municipal del BOP dispone
    de previsualización real; la parte de Diputación mantiene únicamente su
    diagnóstico de lectura porque su importador histórico no tiene dry-run.
    En APLICADO todas las fuentes usan la misma ventana solapada para tolerar
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
        resultado["fuentes"]["bop_valencia_diputacion"] = importar_bop_valencia(
            historico=True,
            dias=dias_solape,
        )
    else:
        headers = {
            "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
            "Accept-Language": "es-ES,es;q=0.9",
        }
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
            resultado["fuentes"]["bop_valencia_diputacion"] = {
                "modo": "SOLO_DIAGNOSTICO",
                "resultado": diagnosticar_bop(client, fecha=fecha_hoy.isoformat()),
            }

    # El BOP municipal sí tiene planificación idempotente y modo solo revisión.
    # Se ejecuta antes que BOE para que este pueda vincular una convocatoria BOE
    # con las bases municipales detectadas en la misma ejecución aplicada.
    resultado["fuentes"]["bop_valencia_municipios"] = importar_municipales_bop(
        hasta=fecha_hoy,
        dias=dias_solape,
        aplicar=aplicar,
    )

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
