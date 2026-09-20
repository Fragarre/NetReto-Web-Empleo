from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .ambito_administrativo import clasificar_ambito_administrativo

BASE_URL = "https://sede.diputacionalicante.es/"
LISTADO_URL = urljoin(BASE_URL, "empleo-otras-oposiciones/")
RSS_URL = urljoin(BASE_URL, "rssoposicotras/")


def _norm(value: str | None) -> str:
    return " ".join((value or "").replace("\xa0", " ").split())


def _fecha(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_rss(xml: str | bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    items: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        titulo = _norm(item.findtext("title"))
        url = _norm(item.findtext("link"))
        if not url:
            continue
        referencia = _norm(item.findtext("guid")) or url
        items.append(
            {
                "referencia": referencia,
                "titulo": titulo,
                "url": urljoin(BASE_URL, url),
                "fecha_publicacion": _fecha(item.findtext("pubDate")),
            }
        )
    return items


def _fecha_es(value: str | None) -> date | None:
    value = _norm(value)
    if not value:
        return None
    for formato in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, formato).date()
        except ValueError:
            pass
    return None


def _parse_listado(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    filas: list[dict[str, Any]] = []
    for tr in soup.find_all("tr"):
        celdas = tr.find_all(["th", "td"])
        textos = [_norm(c.get_text(" ", strip=True)) for c in celdas]
        if len(textos) < 2:
            continue
        unidos = " | ".join(textos).lower()
        if "plaza" in unidos and "entidad" in unidos:
            continue
        plaza = textos[0]
        entidad = textos[1] if len(textos) > 1 else None
        if not plaza or not entidad:
            continue
        vacantes = textos[2] if len(textos) > 2 else None
        fecha_inicio = _fecha_es(textos[4] if len(textos) > 4 else None)
        fecha_fin = _fecha_es(textos[5] if len(textos) > 5 else None)
        observaciones = textos[6] if len(textos) > 6 else None
        enlaces = [urljoin(BASE_URL, a.get("href")) for a in tr.find_all("a", href=True)]
        enlace = enlaces[0] if enlaces else None
        clave = f"{entidad}|{plaza}|{enlace or ''}"
        filas.append(
            {
                "referencia": f"DALIOTRAS:{hashlib.sha256(clave.encode('utf-8')).hexdigest()[:20]}",
                "denominacion": plaza,
                "entidad": entidad,
                "vacantes": vacantes,
                "fecha_inicio_presentacion": fecha_inicio,
                "fecha_fin_presentacion": fecha_fin,
                "observaciones": observaciones,
                "url": enlace or LISTADO_URL,
                "enlaces": enlaces,
                "ambito_administrativo": clasificar_ambito_administrativo(
                    {"denominacion": plaza, "cuerpo_escala": None, "grupo": None}
                ),
            }
        )
    return filas


def diagnosticar_otras_entidades_alicante(*, max_items: int = 200) -> dict[str, Any]:
    """SOLO_REVISION: descubre y normaliza sin acceder a la base de datos."""
    headers = {"User-Agent": "TuCoach-Empleo/1.0", "Accept-Language": "es-ES,es;q=0.9"}
    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION",
        "fuente": "Diputación Alicante / otras entidades locales",
        "rss_url": RSS_URL,
        "listado_url": LISTADO_URL,
        "descubiertos": 0,
        "administrativos": 0,
        "errores": [],
        "detalle": [],
    }
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        try:
            rss = client.get(RSS_URL)
            rss.raise_for_status()
            items = _parse_rss(rss.content)
            resultado["rss_status"] = rss.status_code
            resultado["rss_items"] = len(items)
        except Exception as exc:
            resultado["errores"].append({"origen": "RSS", "error": f"{type(exc).__name__}: {exc}"})
            items = []

        try:
            pagina = client.get(LISTADO_URL)
            pagina.raise_for_status()
            filas = _parse_listado(pagina.text)
            resultado["listado_status"] = pagina.status_code
            resultado["listado_items"] = len(filas)
        except Exception as exc:
            resultado["errores"].append({"origen": "LISTADO", "error": f"{type(exc).__name__}: {exc}"})
            filas = []

    # El listado estructurado es la base normalizada. El RSS queda como canal de descubrimiento
    # y diagnóstico; no se enlazan elementos entre ambos por heurística débil.
    detalle = filas[:max_items]
    resultado["descubiertos"] = len(detalle)
    resultado["administrativos"] = sum(1 for x in detalle if x["ambito_administrativo"] == "SI")
    resultado["detalle"] = detalle
    return resultado
