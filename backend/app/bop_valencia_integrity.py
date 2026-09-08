from __future__ import annotations

import hashlib
import re

from . import bop_valencia as _bop
from .database import get_connection


def _es_convocatoria_base(titulo: str, texto: str) -> bool:
    """Distingue bases/convocatoria de publicaciones posteriores del proceso."""
    n = _bop._sin(titulo + " " + texto)

    posteriores = (
        "designacion de miembros", "designacio de membres",
        "designacion del organo", "designacio de l'organ",
        "designacion del tribunal", "designacio del tribunal",
        "modificacion del organo", "modificacio de l'organ",
        "composicion del organo", "composicio de l'organ",
        "relacion provisional", "relacio provisional",
        "relacion definitiva", "relacio definitiva",
        "lista provisional", "llista provisional",
        "lista definitiva", "llista definitiva",
        "personas admitidas", "persones admeses",
        "personas excluidas", "persones excloses",
        "fecha de examen", "data d'examen", "data de l'exercici",
        "primer ejercicio", "primer exercici",
        "calificaciones", "qualificacions", "resultados", "resultats",
        "nombramiento", "nomenament", "constitucion de bolsa", "constitucio de borsa",
    )
    if any(x in n for x in posteriores):
        return False

    bases = (
        "aprobacion de las bases", "aprovacio de les bases",
        "aprobacion de bases", "aprovacio de bases",
        "bases que han de regir", "bases especifiques", "bases especificas",
        "convocatoria para la seleccion", "convocatoria per a la seleccio",
        "convocatoria de la oposicion", "convocatoria de l'oposicio",
        "convocatoria del proceso selectivo", "convocatoria del proces selectiu",
    )
    return any(x in n for x in bases)


def _familia_perfil(texto: str) -> str | None:
    """Familias inequívocas usadas solo para detectar colisiones de código."""
    n = _bop._sin(texto)
    patrones = (
        ("auxiliar_administrativo", ("auxiliar administratiu", "auxiliar administrativo")),
        ("administrativo", ("administratiu", "administrativo")),
        ("recaudacion", ("recaptacio", "recaudacion")),
        ("taller_imprenta", ("taller d'impremta", "taller de imprenta")),
        ("obras_publicas", ("obres publiques", "obras publicas")),
        ("ingenieria", ("enginyer", "ingenier")),
        ("educacion", ("professor", "profesor", "educacio", "educacion")),
    )
    for familia, terminos in patrones:
        if any(t in n for t in terminos):
            return familia
    return None


def _identificador_estable(titulo: str, texto: str) -> str:
    """Usa el código de convocatoria, separando colisiones reales de perfiles.

    Diputación puede reutilizar un mismo código para procesos distintos. Se
    mantiene el identificador histórico cuando el perfil coincide; solo se
    añade sufijo cuando ya existe ese código para otra familia inequívoca.
    """
    contenido = titulo + " " + texto
    convocatoria = _bop._convocatoria(contenido)
    if not convocatoria:
        return "DVAL:T:" + hashlib.sha256(_bop._sin(titulo).encode("utf-8")).hexdigest()[:24]

    base = f"DVAL:{convocatoria}"
    familia_nueva = _familia_perfil(contenido)
    if not familia_nueva:
        return base

    try:
        with get_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT denominacion FROM procesos WHERE identificador_estable=%s LIMIT 1", (base,))
            row = cursor.fetchone()
    except Exception:
        return base

    if not row:
        return base
    familia_existente = _familia_perfil(row[0] or "")
    if not familia_existente or familia_existente == familia_nueva:
        return base

    sufijo = hashlib.sha256(familia_nueva.encode("utf-8")).hexdigest()[:8]
    return f"{base}:{sufijo}"


def aplicar_integridad_bop() -> None:
    _bop._es_convocatoria_base = _es_convocatoria_base
    _bop._tipo_publicacion = lambda titulo, texto: "CONVOCATORIA" if _es_convocatoria_base(titulo, texto) else "BOP"
    _bop._identificador_estable = _identificador_estable
