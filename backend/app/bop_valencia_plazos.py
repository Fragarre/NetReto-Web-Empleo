from __future__ import annotations

import re
from datetime import date, timedelta

from . import bop_valencia as _bop


_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def _sumar_habiles(inicio: date, dias: int) -> date:
    """Suma días hábiles lunes-viernes. Los festivos no se infieren."""
    actual = inicio
    restantes = dias
    while restantes > 0:
        actual += timedelta(days=1)
        if actual.weekday() < 5:
            restantes -= 1
    return actual


def _fecha_textual(texto: str) -> date | None:
    n = _bop._sin(texto)
    m = re.search(r"(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})", n)
    if not m:
        return None
    mes = _MESES.get(m.group(2))
    return date(int(m.group(3)), mes, int(m.group(1))) if mes else None


def extraer_plazo_inscripcion(texto: str, fecha_publicacion: date | None = None) -> tuple[date | None, date | None]:
    """Extrae apertura/cierre cuando la publicación contiene fechas explícitas.

    No usa la fecha del BOP provincial como inicio cuando las bases remiten al BOE.
    Para esos casos el BOE deberá incorporarse como fuente para fijar el plazo.
    """
    n = _bop._sin(texto)

    # Rango explícito: "del 4 al 15 de mayo de 2026".
    m = re.search(r"(?:del|desde el)\s+(\d{1,2})\s+(?:de\s+[a-z]+\s+)?(?:al|hasta el)\s+(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})", n)
    if m:
        mes = _MESES.get(m.group(3))
        if mes:
            return date(int(m.group(4)), mes, int(m.group(1))), date(int(m.group(4)), mes, int(m.group(2)))

    # Fechas completas explícitas en formato numérico.
    m = re.search(r"(?:desde|del)\s+(\d{1,2}[-/]\d{1,2}[-/]\d{4}).{0,80}?(?:hasta|al)\s+(\d{1,2}[-/]\d{1,2}[-/]\d{4})", n, re.S)
    if m:
        return _bop._fecha(m.group(1)), _bop._fecha(m.group(2))

    # Solo calculamos desde esta publicación cuando el propio texto lo ordena.
    m = re.search(r"plazo\s+de\s+(\d+)\s+dias\s+habiles?.{0,160}?(?:dia\s+siguiente|dia\s+seguent).{0,120}?(?:publicacion|publicacio)\s+(?:de\s+esta|d'aquesta|del\s+presente)", n, re.S)
    if m and fecha_publicacion:
        apertura = fecha_publicacion + timedelta(days=1)
        while apertura.weekday() >= 5:
            apertura += timedelta(days=1)
        cierre = _sumar_habiles(fecha_publicacion, int(m.group(1)))
        return apertura, cierre

    return None, None
