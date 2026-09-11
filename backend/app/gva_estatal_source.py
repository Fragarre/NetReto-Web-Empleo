from __future__ import annotations

import math
import re
import time
import unicodedata
from datetime import date, timedelta
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

BASE = "https://administracion.gob.es"
RESULTADOS = f"{BASE}/pagFront/ofertasempleopublico/resultadosEmpleo.htm"
DETALLE = f"{BASE}/pagFront/ofertasempleopublico/detalleEmpleo.htm"
UA = "NetReto-Empleo/1.0 (https://netexamenes.com)"
TAM_PAGINA = 100

VIAS_INCLUIDAS = {"INGRESO_LIBRE", "INTERINIDAD", "CONTRATACION_FIJA"}
CODIGOS_ADMIN = ("A1-01", "A2-01", "A2-05", "C1-01", "C1-07", "C2-01")
PATRONES_ADMIN = (
    r"\bsuperior de administracion\b",
    r"\bcuerpo administrativo\b",
    r"\bcuerpo auxiliar\b",
    r"\badministrativ[oa]\b",
    r"\bauxiliar administrativ[oa]\b",
    r"\btecnico tributario\b",
    r"\bagentes? tributarios?\b",
)
PATRONES_ORGANISMO_GVA = (
    "generalitat valenciana",
    "presidencia de la generalitat",
    "vicepresidencia",
    "conselleria",
    "labora",
    "agencia valenciana",
    "agencia tributaria valenciana",
    "institut valencia",
    "instituto valenciano",
)
PATRONES_ORGANISMO_NO_GVA = (
    "universitat",
    "universidad",
    "ayuntamiento",
    "ajuntament",
    "diputacion",
    "diputacio",
)


def _sin(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").lower()


def _limpio(texto: str) -> str:
    return " ".join((texto or "").replace("\xa0", " ").split())


def _get(client: httpx.Client, url: str, *, params: dict | None = None) -> httpx.Response:
    ultimo: Exception | None = None
    for intento in range(1, 5):
        try:
            respuesta = client.get(url, params=params)
            respuesta.raise_for_status()
            return respuesta
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            ultimo = exc
            if intento < 4:
                time.sleep(2 * intento)
    assert ultimo is not None
    raise ultimo


def _parse_total(soup: BeautifulSoup) -> int:
    texto = _limpio(soup.get_text(" ", strip=True))
    m = re.search(r"Total resultados:\s*([\d.]+)", texto, re.I)
    if not m:
        raise ValueError("No se localiza el total de resultados")
    return int(m.group(1).replace(".", ""))


def _parse_tarjetas(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    salida: list[dict] = []
    for div in soup.select("div.resultado_empleo"):
        enlace = div.find("a", href=re.compile(r"idConvocatoria=\d+"))
        if not enlace:
            continue
        href = enlace.get("href") or ""
        m = re.search(r"idConvocatoria=(\d+)", href)
        if not m:
            continue
        texto = _limpio(div.get_text(" ", strip=True))
        m_ubic = re.search(r"Ubicaci[oó]n:\s*(.+?)(?=\s+[ÓO]rgano convocante:)", texto, re.I)
        m_org = re.search(r"[ÓO]rgano convocante:\s*(.+)$", texto, re.I)
        salida.append({
            "referencia": int(m.group(1)),
            "titulo": _limpio(enlace.get_text(" ", strip=True)),
            "ubicacion": _limpio(m_ubic.group(1)) if m_ubic else None,
            "organo": _limpio(m_org.group(1)) if m_org else None,
            "url": urljoin(BASE, href),
        })
    return salida


def _params_dia(dia: date, pagina: int) -> dict[str, str]:
    f = dia.strftime("%d/%m/%Y")
    p = {
        "tipoBusqueda": "CONVOCATORIAS",
        "buscar": "true",
        "tipoFechas": "intervaloFechas",
        "fechaPublicacionDesde": f,
        "fechaPublicacionHasta": f,
        "orders": "id",
        "sort": "desc",
        "tam": str(TAM_PAGINA),
    }
    if pagina > 1:
        p["desde"] = str(pagina)
    return p


def descubrir_referencias(client: httpx.Client, desde: date, hasta: date) -> list[dict]:
    encontrados: dict[int, dict] = {}
    dia = desde
    while dia <= hasta:
        r = _get(client, RESULTADOS, params=_params_dia(dia, 1))
        total = _parse_total(BeautifulSoup(r.text, "html.parser"))
        paginas = max(1, math.ceil(total / TAM_PAGINA)) if total else 1
        tarjetas = _parse_tarjetas(r.text)
        for pagina in range(2, paginas + 1):
            rp = _get(client, RESULTADOS, params=_params_dia(dia, pagina))
            tarjetas.extend(_parse_tarjetas(rp.text))
        if len({x["referencia"] for x in tarjetas}) != total:
            raise RuntimeError(f"Paginación incompleta en {dia.isoformat()}: {len(tarjetas)}/{total}")
        for item in tarjetas:
            if "AUTONÓMICO - COMUNITAT VALENCIANA" in (item.get("ubicacion") or "").upper():
                item["fecha_publicacion_busqueda"] = dia.isoformat()
                encontrados[item["referencia"]] = item
        dia += timedelta(days=1)
    return [encontrados[k] for k in sorted(encontrados)]


def _extraer_via(texto: str) -> str | None:
    n = _sin(texto)
    if "promocion interna" in n:
        return "PROMOCION_INTERNA"
    if "ingreso libre" in n or "acceso libre" in n:
        return "INGRESO_LIBRE"
    if "interinidad" in n:
        return "INTERINIDAD"
    if "contratacion fija" in n:
        return "CONTRATACION_FIJA"
    return None


def _es_admin(titulo: str, texto: str) -> tuple[bool, list[str]]:
    nt = _sin(titulo)
    nd = _sin(texto)
    codigos = sorted({c for c in CODIGOS_ADMIN if c.lower() in nd})
    return bool(codigos or any(re.search(p, nt) for p in PATRONES_ADMIN)), codigos


def _clasificar_organismo(texto: str | None) -> str:
    n = _sin(texto or "")
    if any(p in n for p in PATRONES_ORGANISMO_NO_GVA):
        return "NO"
    if any(p in n for p in PATRONES_ORGANISMO_GVA):
        return "SI"
    return "REVISION"


def parsear_detalle(referencia: int, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    texto = _limpio(soup.get_text(" ", strip=True))
    titulo_tag = soup.find("h1") or soup.find("h2")
    titulo = _limpio(titulo_tag.get_text(" ", strip=True)) if titulo_tag else ""
    m_org = re.search(r"[ÓO]rgano convocante\s+(.+?)(?=\s+Requisitos\b|\s+Observaciones\b|\s+Más información\b|\s+Plazo de presentación\b)", texto, re.I)
    m_personal = re.search(r"Tipo de personal\s+(.+?)(?=\s+Tipo de vía\b)", texto, re.I)
    m_inicio = re.search(r"Desde el\s+(\d{2}/\d{2}/\d{4})", texto, re.I)
    m_fin = re.search(r"Hasta el\s+(\d{2}/\d{2}/\d{4})", texto, re.I)
    return {
        "referencia": referencia,
        "texto": texto,
        "titulo_detalle": titulo,
        "organo_detalle": _limpio(m_org.group(1)) if m_org else None,
        "tipo_personal": _limpio(m_personal.group(1)) if m_personal else None,
        "via": _extraer_via(texto),
        "fecha_apertura": m_inicio.group(1) if m_inicio else None,
        "fecha_cierre": m_fin.group(1) if m_fin else None,
    }


def obtener_detalle(client: httpx.Client, referencia: int) -> dict:
    r = _get(client, DETALLE, params={"idConvocatoria": referencia, "idioma": "es"})
    return parsear_detalle(referencia, r.text)


def clasificar_oportunidad(tarjeta: dict, detalle: dict) -> dict:
    es_admin, codigos = _es_admin(tarjeta.get("titulo") or "", detalle.get("texto") or "")
    via = detalle.get("via")
    organismo_estado = _clasificar_organismo(tarjeta.get("organo") or detalle.get("organo_detalle"))
    incluida = bool(es_admin and organismo_estado == "SI" and via in VIAS_INCLUIDAS)
    return {
        **tarjeta,
        **{k: v for k, v in detalle.items() if k != "texto"},
        "codigos_administrativos": codigos,
        "ambito_administrativo": "SI" if es_admin else "NO",
        "organismo_gva": organismo_estado,
        "es_oportunidad": incluida,
        "motivo": "incluida" if incluida else (
            "promocion_interna" if via == "PROMOCION_INTERNA" else
            "fuera_ambito_administrativo" if not es_admin else
            "organismo_excluido" if organismo_estado == "NO" else
            "organismo_revision" if organismo_estado == "REVISION" else
            "via_no_incluida"
        ),
    }


def nuevo_cliente() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(45.0, connect=15.0),
        headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"},
        follow_redirects=True,
    )
