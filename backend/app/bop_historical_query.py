from __future__ import annotations

import re
from datetime import date
from typing import Any

import httpx
from bs4 import BeautifulSoup

from . import bop_valencia_patch as _bop_patch

_BASE_OBTENER_PAGINA = _bop_patch._obtener_pagina


def _normalizar_id(valor: str | None) -> str | None:
    return (valor or "").strip() or None


def _buscar_por_sufijo(form: Any, sufijo: str) -> Any | None:
    sufijo = sufijo.lower()
    for elemento in form.find_all(True):
        for attr in ("name", "id"):
            valor = _normalizar_id(elemento.get(attr))
            if valor and valor.lower().endswith(sufijo):
                return elemento
    return None


def _buscar_boton(form: Any) -> Any | None:
    candidatos = []
    for elemento in form.find_all(["button", "input", "a"]):
        ident = " ".join(
            str(elemento.get(attr) or "") for attr in ("id", "name", "value", "title")
        ).lower()
        texto = elemento.get_text(" ", strip=True).lower()
        if any(x in ident or x in texto for x in ("buscar", "cerca", "search")):
            candidatos.append(elemento)
    return candidatos[0] if candidatos else None


def _valor_campo(elemento: Any) -> str:
    return str(elemento.get("value") or "on")


def _datos_formulario(form: Any) -> dict[str, str]:
    data: dict[str, str] = {}
    for element in form.find_all("input"):
        name = _normalizar_id(element.get("name"))
        if not name:
            continue
        typ = (element.get("type") or "").lower()
        if typ in {"submit", "button", "image", "file", "reset"}:
            continue
        if typ in {"checkbox", "radio"} and not element.has_attr("checked"):
            continue
        data[name] = _valor_campo(element)
    return data


def _url_accion(r0: httpx.Response, form: Any) -> str:
    action = form.get("action") or "/bop/xhtml/portal.xhtml"
    if action.startswith("http://") or action.startswith("https://"):
        return action
    if action.startswith("/"):
        return str(r0.url).split("/bop/", 1)[0] + action
    return str(r0.url).rsplit("/", 1)[0] + "/" + action


def _fecha_en_resultados(html: str, fecha: date) -> bool:
    texto = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    objetivo = fecha.strftime("%d/%m/%Y")
    patron = re.compile(
        r"(?:Data\s+publicaci[oó]|Fecha\s+publicaci[oó]n)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})",
        re.I,
    )
    fechas = {m.group(1) for m in patron.finditer(texto)}
    if not fechas:
        return False
    return all(f == objetivo or f.lstrip("0").replace("/0", "/") == objetivo.lstrip("0").replace("/0", "/") for f in fechas)


def _ids_render(form: Any) -> list[str]:
    encontrados: list[str] = []
    for sufijo in ("messages", "boletines3", "edictos"):
        elemento = _buscar_por_sufijo(form, sufijo)
        if elemento:
            ident = _normalizar_id(elemento.get("id"))
            if ident:
                encontrados.append(ident)
    return encontrados


def _pagina_dinamica(client: httpx.Client, fecha: date) -> tuple[date, str | None, str | None]:
    try:
        r0 = client.get(_bop_patch.BOP_PORTAL_URL)
        r0.raise_for_status()
        soup = BeautifulSoup(r0.text, "html.parser")

        form = None
        inicio = fin = None
        for candidato in soup.find_all("form"):
            ci = _buscar_por_sufijo(candidato, "filtroCalendarioIni_input")
            cf = _buscar_por_sufijo(candidato, "filtroCalendarioFin_input")
            if ci is not None and cf is not None:
                form, inicio, fin = candidato, ci, cf
                break
        if form is None or inicio is None or fin is None:
            return fecha, None, "No se encontró dinámicamente el formulario de búsqueda por fechas del BOP"

        inicio_name = _normalizar_id(inicio.get("name"))
        fin_name = _normalizar_id(fin.get("name"))
        if not inicio_name or not fin_name:
            return fecha, None, "Los campos de fecha del BOP no tienen name utilizable"

        boton = _buscar_boton(form)
        if boton is None:
            return fecha, None, "No se encontró dinámicamente el botón de búsqueda del BOP"
        source = _normalizar_id(boton.get("id")) or _normalizar_id(boton.get("name"))
        if not source:
            return fecha, None, "El botón de búsqueda del BOP no tiene id/name utilizable"

        data = _datos_formulario(form)
        fecha_txt = fecha.strftime("%d/%m/%Y")
        data[inicio_name] = fecha_txt
        data[fin_name] = fecha_txt
        url = _url_accion(r0, form)

        inicio_base = _normalizar_id(inicio.get("id")) or inicio_name
        fin_base = _normalizar_id(fin.get("id")) or fin_name
        if inicio_base.endswith("_input"):
            inicio_base = inicio_base[:-6]
        if fin_base.endswith("_input"):
            fin_base = fin_base[:-6]

        render_ids = _ids_render(form)
        filtro = dict(data)
        filtro.update({
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": source,
            "javax.faces.partial.execute": " ".join([source, inicio_base, fin_base]),
            "javax.faces.partial.render": " ".join(render_ids) if render_ids else "messages boletines3 edictos",
            source: _valor_campo(boton),
        })
        ajax_headers = {
            "Referer": str(r0.url),
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/xml, text/xml, */*; q=0.01",
        }
        r = client.post(url, data=filtro, headers=ajax_headers)
        r.raise_for_status()
        html_primera = _bop_patch._ajax_html(r.text)

        # La comprobación es deliberadamente estricta: una respuesta con fechas
        # distintas significa que PrimeFaces ignoró el filtro y no debe usarse.
        if not _fecha_en_resultados(html_primera, fecha):
            # Fallback no-AJAX con los mismos nombres reales del formulario.
            normal = dict(data)
            boton_name = _normalizar_id(boton.get("name"))
            if boton_name:
                normal[boton_name] = _valor_campo(boton)
            rn = client.post(url, data=normal, headers={"Referer": str(r0.url)})
            rn.raise_for_status()
            if _fecha_en_resultados(rn.text, fecha):
                return fecha, rn.text, None
            return fecha, None, "El portal BOP ignoró el filtro de fecha; respuesta histórica rechazada"

        # Si el filtro AJAX sí funciona, reutilizamos la paginación estable del
        # método anterior partiendo de la respuesta ya validada. Para el uso de
        # reconciliación BOELOCAL, la primera página suele bastar; no obstante,
        # devolvemos el bloque completo recibido y nunca datos de otra fecha.
        return fecha, html_primera, None
    except Exception as exc:
        return fecha, None, f"{type(exc).__name__}: {str(exc)[:220]}"


def obtener_pagina_validada(client: httpx.Client, fecha: date) -> tuple[date, str | None, str | None]:
    # Primero probamos la implementación existente. Solo se acepta si realmente
    # contiene la fecha solicitada; así preservamos compatibilidad si el portal
    # vuelve a responder correctamente al contrato anterior.
    f, html, error = _BASE_OBTENER_PAGINA(client, fecha)
    if html and _fecha_en_resultados(html, fecha):
        return f, html, None
    return _pagina_dinamica(client, fecha)


def aplicar_consulta_historica_validada() -> None:
    _bop_patch._obtener_pagina = obtener_pagina_validada
