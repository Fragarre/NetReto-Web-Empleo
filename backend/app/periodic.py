from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

import httpx

from .boe_local_import import previsualizar_importacion_boe_local
from .bop_valencia_patch import diagnosticar_bop, importar_bop_valencia
from .bop_valencia_municipios import importar_municipales_bop
from .gva_estatal_service import importar_gva_estatal


DIAS_SOLAPE_DEFECTO = 7


def _estado_fuente(valor: Any) -> str:
    """Clasifica el resultado sin reinterpretar la semántica propia de cada conector."""
    if isinstance(valor, dict):
        if valor.get("errores") or valor.get("dias_con_error"):
            return "DEGRADADA"
        contadores = (
            "descubiertos",
            "convocatorias_extraidas",
            "insertados",
            "publicaciones_creadas",
            "cambios_creados",
            "nuevas",
        )
        presentes = [valor.get(k) for k in contadores if isinstance(valor.get(k), int)]
        if presentes and not any(presentes):
            return "SIN_NOVEDADES"
    return "OK"


def _ejecutar_fuente(nombre: str, funcion: Callable[[], Any]) -> dict[str, Any]:
    """Aísla una fuente: su fallo se informa y no aborta las siguientes."""
    try:
        valor = funcion()
        return {
            "estado": _estado_fuente(valor),
            "resultado": valor,
        }
    except Exception as exc:
        return {
            "estado": "ERROR",
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }


def ejecutar_periodico(*, aplicar: bool = False, hoy: date | None = None, dias_solape: int = DIAS_SOLAPE_DEFECTO) -> dict[str, Any]:
    """Orquesta las fuentes validadas de Empleo con aislamiento por fuente.

    Fuentes incluidas:
    - BOP Valencia Diputación.
    - BOP Valencia municipal: altas y seguimientos de ayuntamientos.
    - BOE local: altas/vinculaciones de convocatorias administrativas valencianas.
    - GVA estatal + seguimiento DOGV directo de convocatorias activas.

    En SOLO_REVISION no se escribe en BD. Cada fuente se ejecuta de forma
    aislada: un fallo se registra como ERROR y el ciclo continúa con las demás.
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

    def ejecutar_bop_diputacion() -> Any:
        if aplicar:
            return importar_bop_valencia(historico=True, dias=dias_solape)
        headers = {
            "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
            "Accept-Language": "es-ES,es;q=0.9",
        }
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
            return {
                "modo": "SOLO_DIAGNOSTICO",
                "resultado": diagnosticar_bop(client, fecha=fecha_hoy.isoformat()),
            }

    resultado["fuentes"]["bop_valencia_diputacion"] = _ejecutar_fuente(
        "bop_valencia_diputacion",
        ejecutar_bop_diputacion,
    )

    # El BOP municipal se mantiene antes que BOE: en modo aplicado, BOE puede
    # vincular con bases municipales detectadas en esta misma ejecución.
    resultado["fuentes"]["bop_valencia_municipios"] = _ejecutar_fuente(
        "bop_valencia_municipios",
        lambda: importar_municipales_bop(
            hasta=fecha_hoy,
            dias=dias_solape,
            aplicar=aplicar,
        ),
    )

    resultado["fuentes"]["boe_local"] = _ejecutar_fuente(
        "boe_local",
        lambda: previsualizar_importacion_boe_local(
            hasta=fecha_hoy,
            dias=dias_solape,
            aplicar=aplicar,
        ),
    )

    resultado["fuentes"]["gva"] = _ejecutar_fuente(
        "gva",
        lambda: importar_gva_estatal(
            desde=desde,
            hasta=fecha_hoy,
            aplicar=aplicar,
        ),
    )

    estados = [fuente["estado"] for fuente in resultado["fuentes"].values()]
    resultado["resumen_fuentes"] = {
        "total": len(estados),
        "ok": sum(e == "OK" for e in estados),
        "sin_novedades": sum(e == "SIN_NOVEDADES" for e in estados),
        "degradadas": sum(e == "DEGRADADA" for e in estados),
        "errores": sum(e == "ERROR" for e in estados),
    }
    return resultado
