from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import Any


def _sin(texto: str | None) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


_TERMINALES_COMUNES = (
    "finalizacion del proceso",
    "finalizacion del proceso selectivo",
    "finalitzacio del proces",
    "finalitzacio del proces selectiu",
    "desistimiento",
    "desistiment",
    "anulacion",
    "anul·lacio",
    "anullacio",
    "nombramiento como funcionario",
    "nombramiento como funcionaria",
    "nombramiento de funcionario",
    "nomenament com a funcionari",
    "nomenament com a funcionaria",
    "nomenament de funcionari",
    "toma de posesion",
    "presa de possessio",
    "adjudicacion definitiva",
    "adjudicacio definitiva",
    "adjudicacion de destinos",
    "adjudicacio de destinacions",
)

_TERMINALES_PROVISION = (
    "nombramiento mediante concurso",
    "nomenament mitjancant concurs",
)

_TERMINALES_BOLSA = (
    "constitucion de bolsa",
    "constitucion de la bolsa",
    "constitucio de borsa",
    "constitucio de la borsa",
    "aprobacion definitiva de la bolsa",
    "aprovacio definitiva de la borsa",
)


def es_bolsa(tipo_proceso: str | None) -> bool:
    return "bolsa" in _sin(tipo_proceso) or "borsa" in _sin(tipo_proceso)


def clasificar_evento_terminal(tipo_proceso: str | None, titulo: str | None) -> str | None:
    """Clasifica solo evidencias oficiales inequívocamente terminales.

    Devuelve FINALIZADO, DESISTIDO, ANULADO o None. Ante cualquier duda se
    conserva el proceso activo.
    """
    n = _sin(titulo)
    if not n:
        return None
    if "desistimiento" in n or "desistiment" in n:
        return "DESISTIDO"
    if any(x in n for x in ("anulacion", "anullacio", "anul·lacio")):
        return "ANULADO"
    if any(_sin(x) in n for x in _TERMINALES_COMUNES + _TERMINALES_PROVISION):
        return "FINALIZADO"
    if es_bolsa(tipo_proceso) and any(_sin(x) in n for x in _TERMINALES_BOLSA):
        return "FINALIZADO"
    return None


def estado_inscripcion(proceso: dict[str, Any], *, hoy: date | None = None) -> dict[str, Any]:
    """Deriva la situación de inscripción sin mezclarla con el ciclo selectivo."""
    hoy = hoy or date.today()
    apertura = proceso.get("fecha_apertura")
    cierre = proceso.get("fecha_cierre")

    if apertura and cierre:
        if hoy < apertura:
            return {"codigo": "PENDIENTE_APERTURA", "fecha_apertura": apertura, "fecha_cierre": cierre}
        if hoy <= cierre:
            return {"codigo": "ABIERTO", "fecha_apertura": apertura, "fecha_cierre": cierre}
        return {"codigo": "CERRADO", "fecha_apertura": apertura, "fecha_cierre": cierre}

    datos = proceso.get("datos_json") or {}
    literal = str(datos.get("plazo_solicitudes_literal") or "").strip()
    fecha_boe = proceso.get("fecha_convocatoria")
    if literal and fecha_boe:
        m = re.search(r"(\d+)\s+d[ií]as?\s+h[aá]biles", _sin(literal), re.I)
        dias = int(m.group(1)) if m else None
        return {
            "codigo": "PLAZO_LITERAL",
            "fecha_referencia": fecha_boe + timedelta(days=1),
            "dias_habiles": dias,
            "literal": literal,
        }

    origen = str(datos.get("origen") or "").upper()
    boe_local = datos.get("boe_local")
    if origen == "BOP_VALENCIA_MUNICIPAL" and not boe_local:
        return {"codigo": "PENDIENTE_BOE"}

    return {"codigo": "NO_DETERMINADO"}
