from __future__ import annotations

from . import bop_valencia as _bop


_TERMINOS_NO_EMPLEO = (
    "subvencion", "subvencio", "subvencions", "subvenciones",
    "ayuda", "ayudas", "ajuda", "ajudes",
    "premio", "premios", "premi", "premis",
    "bdns", "concessio de subvencions", "concesion de subvenciones",
    "justificacio de la convocatoria", "justificacion de la convocatoria",
)

_TERMINOS_EMPLEO = (
    "proceso selectivo", "proces selectiu",
    "seleccion de", "seleccio de", "selección de",
    "oposicion", "oposicio", "oposición",
    "concurso oposicion", "concurs oposicio",
    "concurso-oposicion", "concurs-oposicio",
    "bolsa de trabajo", "borsa de treball", "bolsa de empleo",
    "plaza de", "plazas de", "placa de", "places de",
    "personas admitidas", "persones admeses",
    "personas excluidas", "persones excloses",
    "tribunal calificador", "tribunal qualificador",
    "organo tecnico de seleccion", "organ tecnic de seleccio",
    "data d'examen", "fecha de examen", "primer ejercicio", "primer exercici",
    "borsa de treball", "constitucio de borsa", "constitucion de bolsa",
)


def incluido_empleo_bop(titulo: str) -> bool:
    """Acepta únicamente anuncios inequívocamente vinculados a empleo público."""
    n = _bop._sin(titulo)
    if any(_bop._sin(x) in n for x in _TERMINOS_NO_EMPLEO):
        return False
    if any(_bop._sin(x) in n for x in _bop.EXCLUIDOS):
        return False
    return any(_bop._sin(x) in n for x in _TERMINOS_EMPLEO)


def aplicar_reglas_bop() -> None:
    _bop._incluido = incluido_empleo_bop
