from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

from . import boe_local_bases_bop as _bases

_REGISTRO = re.compile(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d+)", re.I)
_TITULO = re.compile(r"\b(?:Anunci|Anuncio)\b", re.I)


def _limpiar_titulo(fragmento: str) -> str | None:
    fragmento = " ".join(fragmento.split()).strip()
    mt = _TITULO.search(fragmento)
    if not mt:
        return None
    titulo = fragmento[mt.start():]
    # El título del índice termina en el primer punto. Evitamos capturar la
    # ficha siguiente o metadatos del portal.
    punto = titulo.find(".")
    if punto >= 20:
        titulo = titulo[: punto + 1]
    if not (20 <= len(titulo) <= 1000):
        return None
    return titulo


def extraer_anuncios_estructurales(html: str) -> list[dict[str, Any]]:
    """Asocia cada registro BOP con su título dentro de su propio bloque.

    El número de registro es el delimitador estable. El título se busca primero
    antes del registro dentro del bloque delimitado por registros consecutivos;
    solo si no existe se busca después, también sin invadir el bloque siguiente.
    Así se soportan ambas disposiciones del portal sin asociar el título de un
    anuncio al registro anterior.
    """
    texto = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    texto = " ".join(texto.split())
    marcas = list(_REGISTRO.finditer(texto))
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()

    for i, mr in enumerate(marcas):
        registro = mr.group(1)
        if registro in vistos:
            continue

        limite_anterior = marcas[i - 1].end() if i else 0
        limite_siguiente = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)

        antes = texto[limite_anterior:mr.start()]
        despues = texto[mr.end():limite_siguiente]

        # En la presentación normal del BOP: entidad -> título -> registro.
        # Tomamos el último "Anunci/Anuncio" del bloque anterior al registro.
        posiciones = [m.start() for m in _TITULO.finditer(antes)]
        titulo = _limpiar_titulo(antes[posiciones[-1]:]) if posiciones else None

        # Algunas respuestas parciales invierten el orden. Fallback acotado al
        # mismo bloque, nunca al anuncio siguiente.
        if not titulo:
            titulo = _limpiar_titulo(despues)
        if not titulo:
            continue

        vistos.add(registro)
        resultados.append({
            "titulo": titulo,
            "url": f"https://bop.dival.es/bop/downloads?anuncioNumReg={quote(registro)}&lang=es",
            "registro": registro,
            "fecha_publicacion": None,
            "municipio": None,
        })

    return resultados


def aplicar_extraccion_estructural_bop() -> None:
    _bases._extraer_anuncios_genericos = extraer_anuncios_estructurales
