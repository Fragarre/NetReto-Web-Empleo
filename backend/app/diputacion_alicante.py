from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .database import get_connection

RSS_URL = "https://sede.diputacionalicante.es/rssoposiciondipu/"
SEGUIMIENTO_URL = "https://sede.diputacionalicante.es/oposiciones-diputacion/"
BASE_URL = "https://sede.diputacionalicante.es/"
ORGANISMO_ID = 4
FUENTE_ID = 4


def _norm(value: str | None) -> str:
    return " ".join((value or "").replace("\xa0", " ").split())


def _sin_acentos(value: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(c) != "Mn")


def _fecha(value: str | None) -> date | None:
    if not value:
        return None
    for patron in (r"(\d{1,2})/(\d{1,2})/(\d{4})", r"(\d{1,2})-(\d{1,2})-(\d{4})"):
        m = re.search(patron, value)
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def _int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value.replace(".", ""))
    return int(m.group(0)) if m else None


def _parse_rss(xml: str) -> list[tuple[str, str]]:
    root = ET.fromstring(xml)
    resultado: list[tuple[str, str]] = []
    for item in root.findall(".//item"):
        title = _norm(item.findtext("title"))
        link = _norm(item.findtext("link"))
        if link:
            resultado.append((title, urljoin(BASE_URL, link)))
    return resultado


def _codigo(url: str, title: str) -> str:
    query = parse_qs(urlparse(url).query)
    for key in ("c", "id", "codigo"):
        if query.get(key):
            return query[key][0]
    m = re.search(r"(?:convocatoria|conv\.)\s*([\w/-]+)", title, re.I)
    return m.group(1) if m else hashlib.sha1(url.encode()).hexdigest()[:16]


def _campo(texto: str, etiqueta: str, siguiente: str | None = None) -> str | None:
    if siguiente:
        patron = rf"{re.escape(etiqueta)}\s*:?\s*(.*?)(?=\s+{re.escape(siguiente)}\s*:|$)"
    else:
        patron = rf"{re.escape(etiqueta)}\s*:?\s*(.*?)(?=\s+(?:Observaciones|Seguimiento de la oposici[oó]n)\b|$)"
    m = re.search(patron, texto, re.I)
    return _norm(m.group(1)) if m else None


def parsear_detalle(url: str, rss_title: str, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    texto = _norm(soup.get_text(" ", strip=True))
    plaza = _campo(texto, "Plaza", "Entidad") or _norm(rss_title)
    entidad = _campo(texto, "Entidad", "Vacantes")
    vacantes = _int(_campo(texto, "Vacantes", "Tipo contrato"))
    tipo_prueba = _campo(texto, "Tipo Prueba", "Presentación instancias")
    fecha_apertura = _fecha(_campo(texto, "Fecha Inicio", "Fecha Final"))
    fecha_cierre = _fecha(_campo(texto, "Fecha Final", "Observaciones"))
    observaciones = _campo(texto, "Observaciones")
    codigo = _codigo(url, rss_title)

    titulo = plaza
    if entidad and entidad != "Diputación Provincial de Alicante":
        titulo = f"{plaza} — {entidad}"

    normal = _sin_acentos(texto)
    turno = None
    if "promocion interna" in normal:
        turno = "PROMOCION_INTERNA"
    elif "turno libre" in normal or "oposicion libre" in normal:
        turno = "TURNO_LIBRE"

    sistema = None
    if tipo_prueba:
        sistema = tipo_prueba.upper()

    anio = None
    m_anio = re.search(r"convocatoria\s+(?:\w+\s+)?(20\d{2})", _sin_acentos(rss_title + " " + texto), re.I)
    if m_anio:
        anio = int(m_anio.group(1))

    # El RSS de esta sede alimenta el seguimiento de convocatorias; se conserva
    # el proceso como EN_CURSO y las publicaciones posteriores como historial.
    hash_contenido = hashlib.sha256(html.encode("utf-8")).hexdigest()
    return {
        "codigo_externo": codigo,
        "identificador_estable": f"DALI:{codigo}",
        "denominacion": titulo or f"Proceso Diputación Alicante {codigo}",
        "grupo": None,
        "tipo_proceso": tipo_prueba,
        "sistema_selectivo": sistema,
        "turno": turno,
        "plazas": vacantes,
        "estado": "EN_CURSO",
        "es_oportunidad": True,
        "anio_convocatoria": anio,
        "fecha_apertura": fecha_apertura,
        "fecha_cierre": fecha_cierre,
        "ultima_publicacion_at": datetime.now(timezone.utc),
        "datos_json": {
            "url_detalle": url,
            "entidad": entidad,
            "observaciones": observaciones,
            "rss_title": rss_title,
        },
        "publicacion": {
            "referencia": f"DALI:{codigo}:{hash_contenido}",
            "tipo": "DETALLE",
            "titulo": rss_title or titulo,
            "fecha_publicacion": date.today(),
            "url": url,
            "contenido_hash": hash_contenido,
            "contenido_texto": texto,
            "datos_json": {"codigo": codigo},
        },
    }


def _upsert(cursor, datos: dict[str, Any]) -> tuple[int, bool]:
    cursor.execute(
        """
        INSERT INTO procesos (
            organismo_id, codigo_externo, identificador_estable, denominacion,
            grupo, tipo_proceso, sistema_selectivo, turno, plazas, estado,
            es_oportunidad, anio_convocatoria, fecha_apertura, fecha_cierre,
            ultima_publicacion_at, fuente_principal_id, datos_json, updated_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (identificador_estable) DO UPDATE SET
            denominacion=EXCLUDED.denominacion,
            grupo=COALESCE(EXCLUDED.grupo, procesos.grupo),
            tipo_proceso=COALESCE(EXCLUDED.tipo_proceso, procesos.tipo_proceso),
            sistema_selectivo=COALESCE(EXCLUDED.sistema_selectivo, procesos.sistema_selectivo),
            turno=COALESCE(EXCLUDED.turno, procesos.turno),
            plazas=COALESCE(EXCLUDED.plazas, procesos.plazas),
            estado=EXCLUDED.estado,
            anio_convocatoria=COALESCE(EXCLUDED.anio_convocatoria, procesos.anio_convocatoria),
            fecha_apertura=COALESCE(EXCLUDED.fecha_apertura, procesos.fecha_apertura),
            fecha_cierre=COALESCE(EXCLUDED.fecha_cierre, procesos.fecha_cierre),
            ultima_publicacion_at=EXCLUDED.ultima_publicacion_at,
            datos_json=EXCLUDED.datos_json,
            updated_at=NOW()
        RETURNING id, (xmax = 0) AS inserted
        """,
        (
            ORGANISMO_ID, datos["codigo_externo"], datos["identificador_estable"], datos["denominacion"],
            datos["grupo"], datos["tipo_proceso"], datos["sistema_selectivo"], datos["turno"], datos["plazas"],
            datos["estado"], datos["es_oportunidad"], datos["anio_convocatoria"], datos["fecha_apertura"],
            datos["fecha_cierre"], datos["ultima_publicacion_at"], FUENTE_ID, datos["datos_json"],
        ),
    )
    row = cursor.fetchone()
    return int(row[0]), bool(row[1])


def importar_diputacion_alicante(*, max_detalles: int = 100) -> dict[str, int]:
    headers = {
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    estadisticas = {"descubiertos": 0, "procesos": 0, "publicaciones": 0, "errores": 0}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        rss = client.get(RSS_URL)
        rss.raise_for_status()
        enlaces = _parse_rss(rss.text)[:max_detalles]
        estadisticas["descubiertos"] = len(enlaces)
        with get_connection() as connection:
            with connection.cursor() as cursor:
                for title, url in enlaces:
                    try:
                        respuesta = client.get(url)
                        respuesta.raise_for_status()
                        datos = parsear_detalle(url, title, respuesta.text)
                        proceso_id, _ = _upsert(cursor, datos)
                        estadisticas["procesos"] += 1
                        pub = datos["publicacion"]
                        cursor.execute(
                            """
                            INSERT INTO publicaciones (
                                proceso_id, fuente_id, referencia, tipo, titulo,
                                fecha_publicacion, url, contenido_hash, contenido_texto, datos_json
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (fuente_id, referencia, url) DO NOTHING
                            RETURNING id
                            """,
                            (
                                proceso_id, FUENTE_ID, pub["referencia"], pub["tipo"], pub["titulo"],
                                pub["fecha_publicacion"], pub["url"], pub["contenido_hash"],
                                pub["contenido_texto"], pub["datos_json"],
                            ),
                        )
                        if cursor.fetchone():
                            estadisticas["publicaciones"] += 1
                    except Exception:
                        estadisticas["errores"] += 1
    return estadisticas
