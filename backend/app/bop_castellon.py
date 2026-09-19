from __future__ import annotations

from datetime import date
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
    m = re.search(r"Sumario BOP\s*N[º°]?\s*(\d+)\s*\|\s*(\d{2}/\d{2}/\d{4})", texto, re.I)
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
            titulo = _texto(enlace)
            if not titulo:
                continue
            anuncios.append({
                "id_anuncio": mid.group(1),
                "referencia": f"BOPCS:{mid.group(1)}",
                "numero_bop": numero,
                "fecha_publicacion": fecha,
                "organismo": organismo,
                "titulo": titulo,
                "url_documento": httpx.URL(DESCARGA).copy_add_param("idAnuncio", mid.group(1)).copy_add_param("idioma", "es").human_repr(),
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
    if hasta is not None:
        fecha_esperada = hasta.strftime("%d/%m/%Y")
        if sumario["fecha_publicacion"] != fecha_esperada:
            resultado["errores"].append(
                f"El portal muestra {sumario['fecha_publicacion']!r}, no la fecha solicitada {fecha_esperada!r}"
            )
            return resultado
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
