from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database import get_connection

ORGANISMO_ID = 3
FUENTE_ID = 3
LISTADO_URL = "https://www.valencia.es/cas/tramites/seguimiento-de-oposiciones"

_ADMIN_PATTERNS = (
    r"\badministrativ[oa]/?a?\b",
    r"\baux(?:iliar)?\.?\s+administrativ[oa]/?a?\b",
    r"tecnic[oa]/?a?\s+(?:de\s+)?administracion\s+general",
    r"tecnic[oa]/?a?\s+gestion\s+administracion\s+general",
    r"gestio[nó]\s+administracio[nó]\s+general",
)
_EXCLUIR_PATTERNS = (
    r"promocion\s+interna",
    r"provision\s+definitiva",
    r"libre\s+designacion",
    r"concurso\s+de\s+meritos\s+para\s+(?:la\s+)?provision\s+de\s+(?:un|dos|\d+)\s+puesto",
)
_FINAL_PATTERNS = (
    "propuesta nombramiento", "propuesta de nombramiento", "nombramiento como personal",
    "aspirante definitivo", "constitucion de bolsa de trabajo", "constitución de bolsa de trabajo",
)


def _sin(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").split())


def _fecha_es(s: str | None) -> date | None:
    if not s:
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def _es_administrativa(nombre: str) -> bool:
    n = _sin(nombre)
    if any(re.search(p, n, re.I) for p in _EXCLUIR_PATTERNS):
        return False
    return any(re.search(p, n, re.I) for p in _ADMIN_PATTERNS)


def _codigo_desde_url(url: str) -> str | None:
    q = parse_qs(urlparse(url).query)
    for clave, valores in q.items():
        if clave.endswith("_oposicion") and valores:
            return valores[0].strip().upper()
    return None


def _extraer_links_listado(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    encontrados: dict[str, dict[str, str]] = {}
    for a in soup.find_all("a", href=True):
        href = a.get("href") or ""
        if "seguimiento-de-oposiciones" not in href or "oposicion=" not in href:
            continue
        url = urljoin(LISTADO_URL, href)
        codigo = _codigo_desde_url(url)
        nombre = _norm(a.get_text(" ", strip=True))
        if not codigo or not nombre or not _es_administrativa(nombre):
            continue
        encontrados[codigo] = {"codigo": codigo, "nombre": nombre, "url": url}
    return list(encontrados.values())


def _campo(texto: str, patron: str) -> str | None:
    m = re.search(patron, texto, re.I | re.S)
    return _norm(m.group(1)) if m else None


def _parse_detalle(html: str, url: str, nombre_fallback: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    texto = _norm(soup.get_text(" ", strip=True))
    nombre = _campo(texto, r"Nombre:\s*(.*?)\s+Tipo:") or nombre_fallback
    grupo = _campo(texto, r"Grupo:\s*([A-Z]{1,2}\d?(?:\s*,\s*Subgrupo\s+[A-Z]\d)?)\s+Plazas")
    if grupo:
        m = re.search(r"([A-Z]\d)", grupo, re.I)
        subgrupo = m.group(1).upper() if m else None
        grupo_base = subgrupo[0] if subgrupo else grupo.strip().upper()[:2]
    else:
        subgrupo = None
        grupo_base = None
    total = _campo(texto, r"Plazas\s+Total:\s*(\d+)")
    plazas = int(total) if total and total.isdigit() else None
    inst = re.search(r"Instancias:\s*(\d{4}-\d{1,2}-\d{1,2})[^a-zA-Z0-9]+a\s+(\d{4}-\d{1,2}-\d{1,2})", texto, re.I)
    if not inst:
        inst = re.search(r"Instancias:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s+a\s+(\d{1,2}[/-]\d{1,2}[/-]\d{4})", texto, re.I)
    apertura = _fecha_es(inst.group(1)) if inst else None
    cierre = _fecha_es(inst.group(2)) if inst else None
    bop = _campo(texto, r"BOP:\s*([^D]*?)(?=\s+DOCV:)")
    boe = _campo(texto, r"BOE:\s*([^P]*?)(?=\s+Presentaci[oó]n instancias)")
    fecha_conv = _fecha_es(boe) or _fecha_es(bop)
    n = _sin(nombre)
    turno = "ESTABILIZACION" if "estabilizacion" in n else ("PROMOCION_INTERNA" if "promocion interna" in n else ("TURNO_LIBRE" if "turno libre" in n else None))
    tipo = "Concurso-oposición" if "concurso-oposicion" in n else ("Oposición" if "turno libre" in n or "oposicion" in n else "Proceso selectivo")
    especificos = texto
    m = re.search(r"Ficheros adjuntos\s+(.*?)\s+Utilidades", texto, re.I | re.S)
    if m:
        especificos = m.group(1)
    estado = "FINALIZADO" if any(x in _sin(especificos) for x in (_sin(v) for v in _FINAL_PATTERNS)) else "EN_CURSO"
    return {
        "nombre": nombre,
        "grupo": grupo_base,
        "subgrupo": subgrupo,
        "plazas": plazas,
        "turno": turno,
        "tipo_proceso": tipo,
        "fecha_convocatoria": fecha_conv,
        "fecha_apertura": apertura,
        "fecha_cierre": cierre,
        "estado": estado,
        "url": url,
        "texto_hash": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
    }


def importar_ayuntamiento_valencia() -> dict[str, Any]:
    stats: dict[str, Any] = {"descubiertos": 0, "administrativos": 0, "procesos_nuevos": 0, "actualizados": 0, "cambios": 0}
    headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
    with httpx.Client(timeout=40, headers=headers, follow_redirects=True) as client:
        r = client.get(LISTADO_URL)
        r.raise_for_status()
        links = _extraer_links_listado(r.text)
        stats["descubiertos"] = len(links)
        with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            for link in links:
                rd = client.get(link["url"])
                rd.raise_for_status()
                d = _parse_detalle(rd.text, link["url"], link["nombre"])
                if not _es_administrativa(d["nombre"]):
                    continue
                stats["administrativos"] += 1
                estable = f"AVAL:{link['codigo']}"
                cursor.execute("SELECT * FROM procesos WHERE identificador_estable=%s", (estable,))
                ex = cursor.fetchone()
                valores = {
                    "denominacion": d["nombre"], "grupo": d["grupo"], "subgrupo": d["subgrupo"],
                    "tipo_proceso": d["tipo_proceso"], "turno": d["turno"], "plazas": d["plazas"],
                    "estado": d["estado"], "fecha_convocatoria": d["fecha_convocatoria"],
                    "fecha_apertura": d["fecha_apertura"], "fecha_cierre": d["fecha_cierre"],
                }
                if ex is None:
                    cursor.execute("""
                        INSERT INTO procesos
                        (organismo_id,codigo_externo,identificador_estable,denominacion,grupo,subgrupo,tipo_proceso,turno,plazas,estado,
                         fecha_convocatoria,fecha_apertura,fecha_cierre,fuente_principal_id,es_oportunidad,ambito_administrativo,datos_json,updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,'SI',%s,NOW()) RETURNING id
                    """, (ORGANISMO_ID, link["codigo"], estable, d["nombre"], d["grupo"], d["subgrupo"], d["tipo_proceso"], d["turno"], d["plazas"], d["estado"], d["fecha_convocatoria"], d["fecha_apertura"], d["fecha_cierre"], FUENTE_ID, Jsonb({"url_oficial": d["url"], "ficha_hash": d["texto_hash"]})))
                    cursor.fetchone()
                    stats["procesos_nuevos"] += 1
                else:
                    proceso_id = ex["id"]
                    for campo, nuevo in valores.items():
                        anterior = ex.get(campo)
                        if nuevo is not None and anterior != nuevo:
                            cursor.execute("""
                                INSERT INTO cambios (proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen,significativo)
                                VALUES (%s,'ACTUALIZACION',%s,%s,%s,%s,%s)
                            """, (proceso_id, campo, str(anterior) if anterior is not None else None, str(nuevo), f"Actualización oficial: {campo}", anterior is not None and campo in {"fecha_apertura","fecha_cierre","fecha_examen","estado","plazas","turno","etapa_actual"}))
                            stats["cambios"] += 1
                    cursor.execute("""
                        UPDATE procesos SET denominacion=%s,grupo=COALESCE(%s,grupo),subgrupo=COALESCE(%s,subgrupo),tipo_proceso=%s,
                        turno=COALESCE(%s,turno),plazas=COALESCE(%s,plazas),estado=%s,fecha_convocatoria=COALESCE(%s,fecha_convocatoria),
                        fecha_apertura=COALESCE(%s,fecha_apertura),fecha_cierre=COALESCE(%s,fecha_cierre),fuente_principal_id=%s,
                        es_oportunidad=TRUE,ambito_administrativo='SI',datos_json=COALESCE(datos_json,'{}'::jsonb)||%s::jsonb,updated_at=NOW()
                        WHERE id=%s
                    """, (d["nombre"],d["grupo"],d["subgrupo"],d["tipo_proceso"],d["turno"],d["plazas"],d["estado"],d["fecha_convocatoria"],d["fecha_apertura"],d["fecha_cierre"],FUENTE_ID,Jsonb({"url_oficial": d["url"], "ficha_hash": d["texto_hash"]}),proceso_id))
                    stats["actualizados"] += 1
            connection.commit()
    return stats
