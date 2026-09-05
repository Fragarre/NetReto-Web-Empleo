from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote
import re

import httpx
from bs4 import BeautifulSoup

from . import bop_valencia as _bop

BOP_PORTAL_URL = _bop.BOP_PORTAL_URL


def _contenedor_anuncio(a: Any) -> Any | None:
    cont = a
    for _ in range(12):
        cont = cont.parent
        if cont is None:
            return None
        texto = cont.get_text(" ", strip=True)
        if re.search(r"\b\d{4}/\d+\b", texto) and re.search(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)", texto, re.I):
            return cont
    return None


def _titulo_desde_contenedor(cont: Any) -> str:
    texto = cont.get_text(" ", strip=True)
    m = re.search(r"(Anunci\b.*?)(?=N[uú]m\.\s*(?:de\s*)?(?:registre|registro))", texto, re.I)
    return _bop._norm(m.group(1)) if m else _bop._norm(texto)


def _extraer_anuncios_pagina(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for a in soup.find_all("a"):
        if "ui-commandlink" not in " ".join(a.get("class", [])):
            continue
        cont = _contenedor_anuncio(a)
        if cont is None:
            continue
        texto_cont = _bop._norm(cont.get_text(" ", strip=True))
        titulo = _titulo_desde_contenedor(cont)
        if "diputacion provincial de valencia" not in _bop._sin(texto_cont):
            continue
        if not _bop._incluido(titulo):
            continue
        registro, fecha = _bop._extraer_metadatos(a)
        if not registro:
            m = re.search(r"\b(\d{4}/\d+)\b", texto_cont)
            registro = m.group(1) if m else None
        if not registro or registro in vistos:
            continue
        vistos.add(registro)
        resultados.append({"titulo": titulo, "url": f"{_bop.DOWNLOAD_URL}?anuncioCSV={quote(registro)}&lang=es", "registro": registro, "fecha_publicacion": fecha})
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
                if anuncio["registro"] not in vistos:
                    vistos.add(anuncio["registro"])
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
    _bop.descubrir_anuncios = descubrir_anuncios
    return _bop.importar_bop_valencia(historico=historico, dias=dias)


def diagnosticar_bop(client: httpx.Client, fecha: str | None = None) -> dict[str, Any]:
    if fecha:
        r = client.get(f"{BOP_PORTAL_URL}?fecha={quote(fecha)}")
    else:
        r = client.get(_bop.BOP_URL)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    texto = _bop._norm(soup.get_text(" ", strip=True))
    commandlinks = [a for a in soup.find_all("a") if "ui-commandlink" in " ".join(a.get("class", []))]

    historico_links = []
    for a in soup.find_all("a", href=True):
        label = _bop._norm(a.get_text(" ", strip=True))
        href = a.get("href")
        if "hist" in _bop._sin(label).lower() or "fondosdigitales.dival.es" in href:
            historico_links.append({"texto": label, "href": href})

    formularios = []
    for form in soup.find_all("form"):
        formularios.append({
            "id": form.get("id"),
            "action": form.get("action"),
            "method": form.get("method"),
            "inputs": [
                {"name": i.get("name"), "type": i.get("type"), "value": i.get("value")}
                for i in form.find_all("input") if i.get("name")
            ][:80],
        })

    return {
        "url": str(r.url),
        "fecha_solicitada": fecha,
        "status": r.status_code,
        "ancho_html": len(r.text),
        "contiene_10873": "2026/10873" in r.text,
        "contiene_texto_diputacion": "diputació provincial de valència" in _bop._sin(texto),
        "commandlinks": len(commandlinks),
        "anuncios_parser": len(_extraer_anuncios_pagina(r.text)),
        "primeros_registros": re.findall(r"\b2026/\d+\b", texto)[:20],
        "historico_links": historico_links[:10],
        "formularios": formularios[:10],
    }
