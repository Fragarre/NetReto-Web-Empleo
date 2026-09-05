from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from . import bop_valencia as _bop

BOP_PORTAL_URL = _bop.BOP_PORTAL_URL


def _es_diputacion_valencia(a: Any) -> bool:
    """Comprueba la entidad en el contenedor del anuncio, no en el enlace del título."""
    cont = a
    for _ in range(20):
        cont = cont.parent
        if cont is None:
            break
        texto = _bop._norm(cont.get_text(" ", strip=True))
        if "diputacion provincial de valencia" in _bop._sin(texto):
            return True
        if _bop.re.search(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)", texto, _bop.re.I):
            return False
    return False


def _extraer_anuncios_pagina(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()

    for a in soup.find_all("a"):
        titulo = _bop._norm(a.get_text(" ", strip=True))
        if not titulo or not _bop._incluido(titulo):
            continue
        if "ui-commandlink" not in " ".join(a.get("class", [])):
            continue
        if not _es_diputacion_valencia(a):
            continue

        registro, fecha = _bop._extraer_metadatos(a)
        if not registro or registro in vistos:
            continue
        vistos.add(registro)
        resultados.append({
            "titulo": titulo,
            "url": f"{_bop.DOWNLOAD_URL}?anuncioCSV={quote(registro)}&lang=es",
            "registro": registro,
            "fecha_publicacion": fecha,
        })

    return resultados


def descubrir_anuncios(client: httpx.Client, historico: bool = False, dias: int = 1) -> list[dict[str, Any]]:
    if not historico:
        r = client.get(_bop.BOP_URL)
        r.raise_for_status()
        return _extraer_anuncios_pagina(r.text)

    hoy = date.today()
    desde = hoy - timedelta(days=max(0, dias - 1))
    fechas = [desde + timedelta(days=i) for i in range((hoy - desde).days + 1)]
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_obtener_pagina, client, fecha) for fecha in fechas]
        for future in as_completed(futures):
            _, html, _ = future.result()
            if not html:
                continue
            for anuncio in _extraer_anuncios_pagina(html):
                registro = anuncio["registro"]
                if registro not in vistos:
                    vistos.add(registro)
                    resultados.append(anuncio)

    resultados.sort(key=lambda x: (x["fecha_publicacion"] or date.min, x["registro"]))
    return resultados


def _obtener_pagina(client: httpx.Client, fecha: date) -> tuple[date, str | None, str | None]:
    try:
        r = client.get(f"{BOP_PORTAL_URL}?fecha={quote(fecha.strftime('%d/%m/%Y'))}")
        r.raise_for_status()
        return fecha, r.text, None
    except Exception as exc:
        return fecha, None, str(exc)


def importar_bop_valencia(historico: bool = False, dias: int = 1) -> dict[str, Any]:
    """Usa el importador existente con un extractor que localiza la Diputación en el contenedor del anuncio."""
    _bop.descubrir_anuncios = descubrir_anuncios
    return _bop.importar_bop_valencia(historico=historico, dias=dias)


def diagnosticar_bop(client: httpx.Client) -> dict[str, Any]:
    return _bop.diagnosticar_bop(client)
