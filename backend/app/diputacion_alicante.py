from __future__ import annotations

import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from .database import get_connection

RSS_URL = "https://sede.diputacionalicante.es/rssoposiciondipu/"
SEGUIMIENTO_URL = "https://sede.diputacionalicante.es/oposiciones-diputacion/"
BASE_URL = "https://sede.diputacionalicante.es/"
ORGANISMO_ID = 4
FUENTE_ID = 4

EXCLUIDOS = (
    "libre designacion", "libre designación",
    "concurso general de meritos", "concurso general de méritos", "concurso de traslados",
    "comision de servicios", "comisión de servicios",
)


def _norm(value: str | None) -> str:
    return " ".join((value or "").replace("\xa0", " ").split())


def _sin_acentos(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(c) != "Mn")


def _fecha(value: str | None) -> date | None:
    if not value:
        return None
    for patron in (r"(\d{1,2})/(\d{1,2})/(\d{4})", r"(\d{1,2})-(\d{1,2})-(\d{4})"):
        m = re.search(patron, value)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                return None
    return None


def _fecha_rss(value: str | None) -> date | None:
    if not value:
        return None
    directa = _fecha(value)
    if directa:
        return directa
    try:
        dt = parsedate_to_datetime(value)
        return dt.date()
    except (TypeError, ValueError, OverflowError):
        return _fecha(re.sub(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*,?\s*", "", value, flags=re.I))


def _int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value.replace(".", ""))
    return int(m.group(0)) if m else None


def _parse_rss(xml: str | bytes) -> list[tuple[str, str, date | None]]:
    root = ET.fromstring(xml)
    resultado: list[tuple[str, str, date | None]] = []
    for item in root.findall(".//item"):
        title = _norm(item.findtext("title"))
        link = _norm(item.findtext("link"))
        pub_date = _fecha_rss(item.findtext("pubDate"))
        if link:
            resultado.append((title, urljoin(BASE_URL, link), pub_date))
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
        patron = rf"{re.escape(etiqueta)}\s*:?\s*(.*?)(?=\s+{re.escape(siguiente)}\s*:?(?:\s|$)|$)"
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

    titulo = plaza or _norm(rss_title) or f"Proceso Diputación Alicante {codigo}"

    # La condición de oportunidad se determina sobre los datos descriptivos
    # del proceso, no sobre toda la tabla histórica de hitos. La tabla puede
    # mencionar promoción interna en un ejercicio de un proceso mixto.
    texto_clave = _sin_acentos(" ".join(filter(None, (rss_title, plaza, observaciones))))
    tiene_turno_libre = "turno libre" in texto_clave or "oposicion libre" in texto_clave
    tiene_promocion_interna = "promocion interna" in texto_clave
    turno = "TURNO_LIBRE" if tiene_turno_libre else ("PROMOCION_INTERNA" if tiene_promocion_interna else None)
    es_exclusivo_interno = tiene_promocion_interna and not tiene_turno_libre
    es_oportunidad = not es_exclusivo_interno and not any(_sin_acentos(x) in texto_clave for x in EXCLUIDOS)

    anio = None
    m_anio = re.search(r"convocatoria\s+(?:\w+\s+)?(20\d{2})", _sin_acentos(rss_title), re.I)
    if m_anio:
        anio = int(m_anio.group(1))

    hash_contenido = hashlib.sha256(html.encode("utf-8")).hexdigest()
    return {
        "codigo_externo": codigo,
        "identificador_estable": f"DALI:{codigo}",
        "denominacion": titulo,
        "grupo": None,
        "tipo_proceso": tipo_prueba,
        "sistema_selectivo": tipo_prueba.upper() if tipo_prueba else None,
        "turno": turno,
        "plazas": vacantes,
        "estado": "EN_CURSO",
        "es_oportunidad": es_oportunidad,
        "anio_convocatoria": anio,
        "fecha_apertura": fecha_apertura,
        "fecha_cierre": fecha_cierre,
        "ultima_publicacion_at": datetime.now(timezone.utc),
        "datos_json": {
            "url_detalle": url,
            "entidad": entidad,
            "observaciones": observaciones,
            "rss_title": rss_title,
            "tiene_turno_libre": tiene_turno_libre,
            "tiene_promocion_interna": tiene_promocion_interna,
        },
        "publicacion": {
            "referencia": f"DALI:{codigo}:{hash_contenido}",
            "tipo": "DETALLE",
            "titulo": rss_title or titulo,
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
            es_oportunidad=EXCLUDED.es_oportunidad,
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
            datos["fecha_cierre"], datos["ultima_publicacion_at"], FUENTE_ID, Jsonb(datos["datos_json"]),
        ),
    )
    row = cursor.fetchone()
    return int(row[0]), bool(row[1])


def importar_diputacion_alicante(*, max_detalles: int = 100) -> dict[str, Any]:
    headers = {"User-Agent": "TuCoach-Empleo/1.0", "Accept-Language": "es-ES,es;q=0.9"}
    estadisticas: dict[str, Any] = {
        "descubiertos": 0, "procesos": 0, "publicaciones": 0, "cambios": 0, "errores": 0,
        "errores_detalle": [],
    }
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        rss = client.get(RSS_URL)
        rss.raise_for_status()
        enlaces = _parse_rss(rss.content)[:max_detalles]
        estadisticas["descubiertos"] = len(enlaces)
        with get_connection() as connection:
            with connection.cursor() as cursor:
                for title, url, rss_date in enlaces:
                    try:
                        respuesta = client.get(url)
                        respuesta.raise_for_status()
                        datos = parsear_detalle(url, title, respuesta.text)
                        proceso_id, inserted = _upsert(cursor, datos)
                        if inserted:
                            estadisticas["procesos"] += 1

                        pub = datos["publicacion"]
                        referencia = pub["referencia"]
                        fecha_publicacion = rss_date or date.today()

                        cursor.execute(
                            "SELECT id FROM publicaciones WHERE fuente_id=%s AND referencia=%s AND url=%s LIMIT 1",
                            (FUENTE_ID, referencia, pub["url"]),
                        )
                        publicacion = cursor.fetchone()
                        if publicacion is None:
                            cursor.execute(
                                """
                                INSERT INTO publicaciones (
                                    proceso_id, fuente_id, referencia, tipo, titulo,
                                    fecha_publicacion, url, contenido_hash, contenido_texto, datos_json
                                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                RETURNING id
                                """,
                                (
                                    proceso_id, FUENTE_ID, referencia, pub["tipo"], pub["titulo"],
                                    fecha_publicacion, pub["url"], pub["contenido_hash"],
                                    pub["contenido_texto"], Jsonb(pub["datos_json"]),
                                ),
                            )
                            publicacion = cursor.fetchone()
                            estadisticas["publicaciones"] += 1
                            cursor.execute(
                                """
                                INSERT INTO cambios (
                                    proceso_id, publicacion_id, tipo, campo, valor_anterior,
                                    valor_nuevo, resumen, significativo
                                ) VALUES (%s,%s,'PUBLICACION','publicacion',NULL,%s,%s,TRUE)
                                """,
                                (proceso_id, publicacion[0], referencia, f"Nueva publicación oficial: {pub['titulo']}"),
                            )
                            estadisticas["cambios"] += 1
                    except Exception as exc:
                        estadisticas["errores"] += 1
                        errores = estadisticas["errores_detalle"]
                        if len(errores) < 10:
                            errores.append({
                                "codigo": _codigo(url, title),
                                "url": url,
                                "error": f"{type(exc).__name__}: {exc}",
                            })
                        connection.rollback()
                        # El rollback invalida el cursor actual; abrimos uno nuevo
                        # para continuar con el siguiente elemento.
                        cursor.close()
                        cursor = connection.cursor()
            connection.commit()
    return estadisticas


def diagnosticar_diputacion_alicante() -> dict[str, Any]:
    headers = {"User-Agent": "TuCoach-Empleo/1.0", "Accept-Language": "es-ES,es;q=0.9"}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        r = client.get(SEGUIMIENTO_URL)
        r.raise_for_status()
        rss = client.get(RSS_URL)
        rss.raise_for_status()
        enlaces = _parse_rss(rss.content)
        return {
            "seguimiento_url": str(r.url),
            "seguimiento_status": r.status_code,
            "seguimiento_html": len(r.text),
            "rss_url": str(rss.url),
            "rss_status": rss.status_code,
            "rss_html": len(rss.content),
            "rss_items": len(enlaces),
            "muestra": [{"titulo": t, "url": u, "fecha": d.isoformat() if d else None} for t, u, d in enlaces[:10]],
        }
