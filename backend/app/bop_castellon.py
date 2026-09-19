from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .ambito_administrativo import clasificar_ambito_administrativo
from .bop_valencia_municipios import _clasificar_anuncio

PORTAL = "https://bop.dipcas.es/PortalBOP/"
DESCARGA = "https://bop.dipcas.es/PortalBOP/api/descargarAnuncio"


def _texto(elemento) -> str:
    return " ".join(elemento.get_text(" ", strip=True).split())


def _extraer_sumario(html: str) -> dict[str, Any]:
    """Extrae el sumario visible del BOP; no persiste ni consulta BD."""
    soup = BeautifulSoup(html, "html.parser")
    texto = _texto(soup)
    numero = None
    fecha = None
    import re
    m = re.search(r"Sumario\s+BOP.*?(\d+)\s*\|?\s*(\d{2}/\d{2}/\d{4})", texto, re.I)
    if m:
        numero, fecha = m.group(1), m.group(2)

    anuncios: list[dict[str, Any]] = []
    organismo = ""
    for nodo in soup.find_all(["a", "div", "span", "td", "li"]):
        t = _texto(nodo)
        if not t:
            continue
        enlace = nodo if nodo.name == "a" else nodo.find("a", href=True)
        href = enlace.get("href") if enlace else None
        if href and "descargarAnuncio" in href and "idAnuncio=" in href:
            import re
            mid = re.search(r"idAnuncio=(\d+)", href)
            if not mid:
                continue
            # El enlace solo contiene "ui-button". En PrimeFaces aparecen
            # scripts auxiliares entre el botón y el texto visible del anuncio.
            titulo = ""
            siguiente = enlace.find_next()
            while siguiente is not None:
                if siguiente.name == "a" and siguiente.get("href") and "descargarAnuncio" in siguiente.get("href"):
                    break
                if siguiente.name in {"script", "style"}:
                    siguiente = siguiente.find_next()
                    continue
                candidato = _texto(siguiente)
                if (
                    candidato
                    and candidato != "ui-button"
                    and "PrimeFaces.cw(" not in candidato
                    and not candidato.startswith("$(function()")
                ):
                    titulo = candidato
                    break
                siguiente = siguiente.find_next()
            if not titulo:
                continue
            anuncios.append({
                "id_anuncio": mid.group(1),
                "referencia": f"BOPCS:{mid.group(1)}",
                "numero_bop": numero,
                "fecha_publicacion": fecha,
                "organismo": organismo,
                "titulo": titulo,
                "url_documento": str(httpx.URL(DESCARGA).copy_add_param("idAnuncio", mid.group(1)).copy_add_param("idioma", "es")),
            })
            continue

        normal = t.upper()
        if (
            normal.startswith("AYUNTAMIENTO ")
            or normal.startswith("AJUNTAMENT ")
            or normal.startswith("DIPUTACIÓ PROVINCIAL")
            or normal.startswith("DIPUTACIÓN PROVINCIAL")
        ) and len(t) < 180:
            organismo = t

    # El DOM puede repetir nodos contenedores: identidad documental por idAnuncio.
    unicos: dict[str, dict[str, Any]] = {}
    for anuncio in anuncios:
        unicos.setdefault(anuncio["id_anuncio"], anuncio)
    return {"numero_bop": numero, "fecha_publicacion": fecha, "anuncios": list(unicos.values())}



def _boletines_disponibles(html: str) -> list[dict[str, Any]]:
    """Extrae fecha, número y componente JSF de «Boletines anteriores»."""
    import re
    soup = BeautifulSoup(html, "html.parser")
    meses = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
        "noviembre": 11, "diciembre": 12,
    }
    encontrados = {}
    patron = re.compile(r"N[º°]\s*(\d+).*?(\d{2})\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)\s+(\d{4})")
    for nodo in soup.find_all(id=True):
        m = patron.search(_texto(nodo))
        if not m:
            continue
        mes = meses.get(m.group(3).lower())
        if not mes:
            continue
        item = {
            "numero": m.group(1),
            "fecha": date(int(m.group(4)), mes, int(m.group(2))),
            "source": nodo.get("id"),
        }
        clave = (item["numero"], item["fecha"])
        previo = encontrados.get(clave)
        if previo is None or item["source"].count(":") > previo["source"].count(":"):
            encontrados[clave] = item
    return sorted(encontrados.values(), key=lambda x: x["fecha"], reverse=True)


def _view_state(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    nodo = soup.find("input", attrs={"name": "javax.faces.ViewState"})
    return nodo.get("value") if nodo else None


def _cargar_boletin_anterior(client: httpx.Client, html_inicial: str, source: str) -> str:
    """Reproduce la acción JSF/PrimeFaces observada en el navegador."""
    import xml.etree.ElementTree as ET
    view_state = _view_state(html_inicial)
    if not view_state:
        raise RuntimeError("El portal no expone javax.faces.ViewState")
    formulario = "busquedaBoletinesForm"
    datos = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": source,
        "javax.faces.partial.execute": "@all",
        "javax.faces.partial.render": formulario,
        source: source,
        formulario: formulario,
        "javax.faces.ViewState": view_state,
    }
    respuesta = client.post(
        PORTAL,
        data=datos,
        headers={
            "Accept": "application/xml, text/xml, */*; q=0.01",
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": PORTAL,
        },
    )
    respuesta.raise_for_status()
    raiz = ET.fromstring(respuesta.text)
    for update in raiz.findall(".//update"):
        if update.get("id") == formulario and update.text:
            return update.text
    raise RuntimeError("La respuesta JSF no contiene la actualización del formulario")


def consultar_bop_castellon(*, hasta: date | None = None) -> dict[str, Any]:
    """Fase 4: SOLO_REVISION sobre el sumario oficial visible."""
    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION",
        "fuente": "Boletín Oficial de la Provincia de Castellón",
        "hasta": (hasta or date.today()).isoformat(),
        "fecha_boletin": None,
        "numero_bop": None,
        "descubiertos": 0,
        "administrativos": 0,
        "revision": 0,
        "resumen_clases": {},
        "errores": [],
        "detalle": [],
        "muestra_extraida": [],
    }
    try:
        with httpx.Client(timeout=45, follow_redirects=True, headers={"User-Agent": "TuCoach-Empleo/1.0"}) as client:
            respuesta = client.get(PORTAL)
            respuesta.raise_for_status()
    except Exception as exc:
        resultado["errores"].append(f"{type(exc).__name__}: {exc}")
        return resultado

    sumario = _extraer_sumario(respuesta.text)
    resultado["fecha_boletin"] = sumario["fecha_publicacion"]
    resultado["numero_bop"] = sumario["numero_bop"]
    if sumario["fecha_publicacion"] is None or sumario["numero_bop"] is None:
        resultado["errores"].append("No se pudo identificar fecha/número del boletín mostrado")
        return resultado
    if hasta is not None:
        fecha_esperada = hasta.strftime("%d/%m/%Y")
        if sumario["fecha_publicacion"] != fecha_esperada:
            resultado["errores"].append(
                f"El portal muestra {sumario['fecha_publicacion']!r}, no la fecha solicitada {fecha_esperada!r}"
            )
            return resultado
    resultado["muestra_extraida"] = [
        {"organismo": x["organismo"], "titulo": x["titulo"], "referencia": x["referencia"]}
        for x in sumario["anuncios"]
    ]
    candidatos = []
    for anuncio in sumario["anuncios"]:
        ambito = clasificar_ambito_administrativo({
            "denominacion": anuncio["titulo"],
            "cuerpo_escala": None,
            "grupo": None,
        })
        if ambito != "SI":
            continue
        item = dict(anuncio)
        item["ambito_administrativo"] = ambito
        item["clase"] = _clasificar_anuncio(item["titulo"])
        candidatos.append(item)

    from collections import Counter
    conteo = Counter(x["clase"] for x in candidatos)
    resultado["descubiertos"] = len(sumario["anuncios"])
    resultado["administrativos"] = len(candidatos)
    resultado["revision"] = conteo.get("REVISION", 0)
    resultado["resumen_clases"] = dict(sorted(conteo.items()))
    resultado["detalle"] = candidatos
    return resultado
