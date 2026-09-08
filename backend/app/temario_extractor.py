from __future__ import annotations

import re
import unicodedata
from typing import Any


# Algunos PDFs oficiales del DOGV usan glifos de fuentes que los extractores
# Unicode devuelven como caracteres alternativos. Son errores de extracción,
# no contenido del documento, y se corrigen antes de presentar/guardar el texto.
_REPARACIONES_GLYPH = {
    "Ɵ": "ti",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
}

# Algunas versiones del extractor devuelven U+FFFD en lugar del glifo "ti".
# Solo se reparan secuencias conocidas del corpus oficial; no se sustituye
# cualquier U+FFFD de forma indiscriminada porque podría ocultar otro carácter.
_REPARACIONES_REPLACEMENT = {
    "E�ología": "Etiología",
    "Caracterís�cas": "Características",
    "Incon�nencia": "Incontinencia",
    "�pos": "tipos",
    "palia�vos": "paliativos",
    "Ac�tudes": "Actitudes",
    "an�cipadas": "anticipadas",
    "ges�ón": "gestión",
}


def _reparar_caracteres(texto: str) -> str:
    texto = unicodedata.normalize("NFKC", texto)
    for origen, destino in _REPARACIONES_GLYPH.items():
        texto = texto.replace(origen, destino)
    for origen, destino in _REPARACIONES_REPLACEMENT.items():
        texto = texto.replace(origen, destino)
    return texto


def _codigo_convocatoria(denominacion: str) -> str | None:
    match = re.search(r"\bConvocatoria\s+(\d+\s*/\s*\d+)\b", denominacion or "", re.I)
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(1))


def _bloque_por_convocatoria(texto: str, codigo: str) -> str | None:
    """Extrae solo el bloque del temario correspondiente a la convocatoria."""
    lineas = texto.splitlines()
    patron_conv = re.compile(r"^\s*Convocatoria\s+(\d+)\s*/\s*(\d+)\b", re.I)
    coincidencia = None
    for i, linea in enumerate(lineas):
        m = patron_conv.match(linea)
        if m and f"{m.group(1)}/{m.group(2)}" == codigo:
            coincidencia = i
            break
    if coincidencia is None:
        return None

    inicio = coincidencia
    for i in range(coincidencia - 1, max(-1, coincidencia - 12), -1):
        if re.search(r"\bTEMARIO\b", lineas[i], re.I):
            inicio = i
            break

    fin = len(lineas)
    for j in range(coincidencia + 1, len(lineas)):
        if patron_conv.match(lineas[j]):
            fin = j
            break

    bloque = "\n".join(lineas[inicio:fin]).strip()
    return bloque if len(bloque) >= 80 else None


def _bloque_generico(texto: str) -> str | None:
    lineas = texto.splitlines()
    patrones_inicio = (
        r"^\s*(?:ANEXO\s+[^\n]{0,80}\s*)?(?:TEMARIO|PROGRAMA|PROGRAMA DE MATERIAS|MATERIAS)\s*:?.*$",
        r"^\s*(?:ANEXO\s+[^\n]{0,80}\s*)?(?:TEMARIO|PROGRAMA|MATERIAS)\s*$",
    )
    inicio = None
    for i, linea in enumerate(lineas):
        if any(re.match(p, linea, re.I) for p in patrones_inicio):
            inicio = i
            break
    if inicio is None:
        return None

    fin = len(lineas)
    patron_fin = re.compile(
        r"^\s*(?:ANEXO|BASES|PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|SEXTA|SÉPTIMA|SEPTIMA|OCTAVA|NOVENA|DÉCIMA|DECIMA|SOLICITUDES|TRIBUNAL|COMISIÓN DE SELECCIÓN|COMISION DE SELECCION|RECURSOS)\b",
        re.I,
    )
    for j in range(inicio + 1, len(lineas)):
        if patron_fin.match(lineas[j]) and j - inicio >= 3:
            fin = j
            break
    bloque = "\n".join(lineas[inicio:fin]).strip()
    return bloque if len(bloque) >= 80 else None


def _validar_extraccion(texto: str) -> bool:
    return "�" not in texto


def extraer_temario_oficial(proceso_id: int) -> dict[str, Any]:
    from .empleo_admin import _extraer_texto_fuente, _puntuacion_fuente
    from .database import get_connection

    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id, denominacion FROM procesos WHERE id=%s", (proceso_id,))
        proceso = cursor.fetchone()
        if proceso is None:
            raise ValueError("Proceso no encontrado")
        proceso = {"id": proceso[0], "denominacion": proceso[1]}
        cursor.execute(
            """
            SELECT id, referencia, tipo, titulo, fecha_publicacion, url
            FROM publicaciones
            WHERE proceso_id=%s AND url IS NOT NULL AND TRIM(url) <> ''
            ORDER BY fecha_publicacion DESC NULLS LAST, id DESC
            """
        )
        publicaciones = [
            {"id": r[0], "referencia": r[1], "tipo": r[2], "titulo": r[3],
             "fecha_publicacion": r[4], "url": r[5]}
            for r in cursor.fetchall()
        ]

    codigo = _codigo_convocatoria(proceso["denominacion"])
    candidatas = sorted(publicaciones, key=_puntuacion_fuente, reverse=True)
    errores: list[str] = []

    for pub in candidatas[:12]:
        try:
            texto, url_final = _extraer_texto_fuente(str(pub["url"]))
            texto = _reparar_caracteres(texto)
            if not _validar_extraccion(texto):
                errores.append(f"{pub['id']}: extracción con caracteres de reemplazo")
                continue

            bloque = _bloque_por_convocatoria(texto, codigo) if codigo else None
            if bloque is None:
                bloque = _bloque_generico(texto)

            if codigo and bloque is None:
                continue
            if bloque:
                bloque = _reparar_caracteres(bloque)
                if not _validar_extraccion(bloque):
                    errores.append(f"{pub['id']}: temario con caracteres de reemplazo no reparables")
                    continue
                return {
                    "proceso_id": proceso_id,
                    "denominacion": proceso["denominacion"],
                    "contenido_texto": bloque,
                    "fuente_url": url_final,
                    "fuente_publicacion_id": pub["id"],
                    "fuente_referencia": pub["referencia"],
                    "fuente_titulo": pub["titulo"],
                    "caracteres_fuente": len(texto),
                }
        except Exception as exc:
            errores.append(f"{pub['id']}: {exc}")

    detalle = "; ".join(errores[-4:])
    raise ValueError(
        "No se ha localizado automáticamente el temario correspondiente a la convocatoria en las publicaciones oficiales disponibles."
        + (f" Detalles: {detalle}" if detalle else "")
    )
