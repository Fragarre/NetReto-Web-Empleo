from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
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


def _ajax_html(response_text: str) -> str:
    if "<partial-response" not in response_text:
        return response_text
    soup = BeautifulSoup(response_text, "xml")
    return "\n".join(u.decode_contents() for u in soup.find_all("update"))


def _obtener_pagina(client: httpx.Client, fecha: date) -> tuple[date, str | None, str | None]:
    try:
        r0 = client.get(BOP_PORTAL_URL)
        r0.raise_for_status()
        soup = BeautifulSoup(r0.text, "html.parser")
        forms = soup.find_all("form")
        form = soup.find("form", id="j_idt132")
        if form is None:
            # El ID JSF puede cambiar. Buscar el formulario que contiene los filtros de fecha.
            for candidato in forms:
                nombres = {i.get("name") for i in candidato.find_all("input") if i.get("name")}
                if "filtroCalendarioIni_input" in nombres and "filtroCalendarioFin_input" in nombres:
                    form = candidato
                    break
        if form is None:
            return fecha, None, f"No se encontró formulario de búsqueda; url={r0.url}; status={r0.status_code}; forms={[f.get('id') for f in forms]}; html={len(r0.text)}"

        data: dict[str, str] = {}
        for element in form.find_all("input"):
            name = element.get("name")
            if not name:
                continue
            typ = (element.get("type") or "").lower()
            if typ in {"submit", "button", "image", "file", "reset"}:
                continue
            if typ in {"checkbox", "radio"} and not element.has_attr("checked"):
                continue
            data[name] = element.get("value") or "on"

        fecha_txt = fecha.strftime("%d/%m/%Y")
        data["filtroCalendarioIni_input"] = fecha_txt
        data["filtroCalendarioFin_input"] = fecha_txt
        data["javax.faces.partial.ajax"] = "true"
        data["javax.faces.source"] = "buscarBtn"
        data["javax.faces.partial.execute"] = "buscarBtn filtroCalendarioIni filtroCalendarioFin"
        data["javax.faces.partial.render"] = "messages boletines3 edictos"
        data["buscarBtn"] = "buscarBtn"

        action = form.get("action") or "/bop/xhtml/portal.xhtml"
        if action.startswith("/"):
            url = str(r0.url).split("/bop/", 1)[0] + action
        else:
            url = str(r0.url).rsplit("/", 1)[0] + "/" + action
        r = client.post(url, data=data, headers={"Referer": str(r0.url), "Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest", "Accept": "application/xml, text/xml, */*; q=0.01"})
        r.raise_for_status()
        return fecha, _ajax_html(r.text), None
    except Exception as exc:
        return fecha, None, str(exc)


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
    def obtener(fecha: date) -> tuple[date, str | None, str | None]:
        headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as c:
            return _obtener_pagina(c, fecha)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(obtener, fecha) for fecha in fechas]
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


def importar_bop_valencia(historico: bool = False, dias: int = 1) -> dict[str, Any]:
    _bop.descubrir_anuncios = descubrir_anuncios
    return _bop.importar_bop_valencia(historico=historico, dias=dias)


def diagnosticar_bop(client: httpx.Client, fecha: str | None = None) -> dict[str, Any]:
    if fecha:
        try:
            fecha_obj = date.fromisoformat(fecha)
        except ValueError:
            fecha_obj = date.today()
        r0 = client.get(BOP_PORTAL_URL)
        r0.raise_for_status()
        soup = BeautifulSoup(r0.text, "html.parser")
        forms = soup.find_all("form")
        _, html, error = _obtener_pagina(client, fecha_obj)
        return {"fecha_solicitada": fecha, "error": error, "get_url": str(r0.url), "get_status": r0.status_code, "get_html": len(r0.text), "form_ids": [f.get("id") for f in forms], "forms": [{"id": f.get("id"), "action": f.get("action"), "inputs": [i.get("name") for i in f.find_all("input") if i.get("name")][:40]} for f in forms[:10]], "post_html": len(html or ""), "anuncios_parser": len(_extraer_anuncios_pagina(html or "")), "primeros_registros": re.findall(r"\b2026/\d+\b", _bop._norm(BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)))[:20], "contiene_10873": "2026/10873" in (html or "")}
    r = client.get(_bop.BOP_URL)
    r.raise_for_status()
    return {"url": str(r.url), "status": r.status_code, "ancho_html": len(r.text), "anuncios_parser": len(_extraer_anuncios_pagina(r.text)), "primeros_registros": re.findall(r"\b2026/\d+\b", _bop._norm(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)))[:20]}
