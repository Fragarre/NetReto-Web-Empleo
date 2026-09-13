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

_NUMEROS_PLAZO = {
    "cinco": 5,
    "diez": 10,
    "quince": 15,
    "veinte": 20,
    "treinta": 30,
}

# Calendario administrativo de la Comunitat Valenciana. Se mantiene por año
# para no calcular fechas exactas con un calendario que no haya sido verificado.
_FESTIVOS_CV: dict[int, set[date]] = {
    2026: {
        date(2026, 1, 1), date(2026, 1, 6), date(2026, 3, 19),
        date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 1),
        date(2026, 6, 24), date(2026, 8, 15), date(2026, 10, 9),
        date(2026, 10, 12), date(2026, 12, 8), date(2026, 12, 25),
    }
}

# Fiestas locales verificadas. Solo se ofrece fecha de cierre calculada cuando
# conocemos también el calendario local del organismo; en los demás casos se
# conserva el plazo literal para evitar una falsa precisión.
_FESTIVOS_LOCALES: dict[tuple[int, str], set[date]] = {
    (2026, "ayuntamiento de benavites"): {date(2026, 3, 18), date(2026, 4, 13)},
}


def es_bolsa(tipo_proceso: str | None) -> bool:
    return "bolsa" in _sin(tipo_proceso) or "borsa" in _sin(tipo_proceso)


def clasificar_evento_terminal(tipo_proceso: str | None, titulo: str | None) -> str | None:
    """Clasifica solo evidencias oficiales inequívocamente terminales."""
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


def _dias_habiles_literal(literal: str) -> int | None:
    normalizado = _sin(literal)
    m = re.search(r"\b(\d+)\s+dias?\s+habiles\b", normalizado, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b([a-z]+)\s+dias?\s+habiles\b", normalizado, re.I)
    if m:
        return _NUMEROS_PLAZO.get(m.group(1))
    return None


def _calcular_cierre_habiles(fecha_boe: date, dias: int, organismo: str | None) -> date | None:
    """Calcula el último día solo con calendario autonómico y local verificados."""
    if dias <= 0 or fecha_boe.year not in _FESTIVOS_CV:
        return None
    clave = (fecha_boe.year, _sin(organismo))
    locales = _FESTIVOS_LOCALES.get(clave)
    if locales is None:
        return None
    festivos = _FESTIVOS_CV[fecha_boe.year] | locales
    actual = fecha_boe
    contados = 0
    while contados < dias:
        actual += timedelta(days=1)
        if actual.weekday() >= 5 or actual in festivos:
            continue
        contados += 1
    return actual


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
    boe_local = datos.get("boe_local") if isinstance(datos.get("boe_local"), dict) else {}
    literal = str(
        datos.get("plazo_solicitudes_literal")
        or boe_local.get("plazo_solicitudes_literal")
        or ""
    ).strip()
    fecha_boe = proceso.get("fecha_boe_publicacion") or proceso.get("fecha_convocatoria")
    if literal and fecha_boe:
        dias = _dias_habiles_literal(literal)
        cierre_calculado = _calcular_cierre_habiles(
            fecha_boe, dias, proceso.get("organismo_nombre")
        ) if dias else None
        if cierre_calculado:
            apertura_calculada = fecha_boe + timedelta(days=1)
            if hoy < apertura_calculada:
                codigo = "PENDIENTE_APERTURA"
            elif hoy <= cierre_calculado:
                codigo = "ABIERTO"
            else:
                codigo = "CERRADO"
            return {
                "codigo": codigo,
                "fecha_apertura": apertura_calculada,
                "fecha_cierre": cierre_calculado,
                "fecha_cierre_calculada": True,
                "dias_habiles": dias,
                "literal": literal,
            }
        return {
            "codigo": "PLAZO_LITERAL",
            "fecha_referencia": fecha_boe + timedelta(days=1),
            "dias_habiles": dias,
            "literal": literal,
        }

    origen = str(datos.get("origen") or "").upper()
    if origen == "BOP_VALENCIA_MUNICIPAL" and not boe_local:
        return {"codigo": "PENDIENTE_BOE"}

    return {"codigo": "NO_DETERMINADO"}
