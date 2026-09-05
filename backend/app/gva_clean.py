from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from .database import get_connection

GVA_SEARCH_URL = "https://sede.gva.es/es/cercador-ocupacio-publica"
GVA_BASE_URL = "https://sede.gva.es"
GVA_ORGANISMO_ID = 1
GVA_FUENTE_ID = 1

TIPOS_INCLUIDOS = (
    "oposicion",
    "bolsa de trabajo",
    "promocion interna",
    "contratacion laboral temporal",
    "contratacion laboral indefinida",
    "proceso de estabilizacion",
    "acto unico telematico",
    "acte unic telematic",
    "anuncio dificil cobertura",
)


def _sin_acentos(texto: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _normalizar(texto: str) -> str:
    return " ".join(texto.replace("\xa0", " ").split())


def _fecha(valor: str | None) -> date | None:
    if not valor:
        return None
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", valor)
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _anio(texto: str) -> int | None:
    normal = _sin_acentos(texto)
    m = re.search(r"convocatoria\s+\d+[/-](\d{2,4})", normal)
    if m:
        valor = int(m.group(1))
        return 2000 + valor if valor < 100 else valor
    m = re.search(r"\b(2026|2027)\b", texto)
    return int(m.group(1)) if m else None


def _denominacion(soup: BeautifulSoup, texto: str) -> str | None:
    tag = soup.find("title")
    if tag:
        valor = _normalizar(tag.get_text(" ", strip=True))
        valor = re.sub(r"\s*-\s*Sede Electr[oó]nica\s*-\s*Generalitat Valenciana\s*$", "", valor, flags=re.I)
        if valor and valor.lower() != "navegacion":
            return valor
    m = re.search(
        r"Empleo público\s+Detalle empleo público\s+Detalle empleo público\s+Atrás\s+(.+?)\s+(?=[A-ZÁÉÍÓÚÜ][^ ]*\s+Etapa actual:|Conselleria\s|Labora\s|Ag[eè]ncia\s)",
        texto,
        re.I,
    )
    return _normalizar(m.group(1)) if m else None


def _tipo_convocatoria(texto: str) -> str | None:
    normal = _sin_acentos(texto)
    m = re.search(r"Informacion basica\s+Convocatoria\s+(.+?)\s+Prueba\s+", normal, re.I)
    if not m:
        return None
    valor = _normalizar(m.group(1))
    for patron, nombre in (
        ("bolsa de trabajo", "Bolsa de trabajo"),
        ("oposicion", "Oposición"),
        ("promocion interna", "Promoción interna"),
        ("contratacion laboral temporal", "Contratación laboral temporal"),
        ("contratacion laboral indefinida", "Contratación laboral indefinida"),
        ("proceso de estabilizacion", "Proceso de estabilización"),
        ("acto unico telematico", "Acto único telemático"),
        ("acte unic telematic", "Acto único telemático"),
        ("anuncio dificil cobertura", "Anuncio difícil cobertura"),
    ):
        if patron in valor:
            return nombre
    return None


def _grupo(texto: str) -> str | None:
    m = re.search(r"\bGrupo\s+([A-Z0-9/]+)", texto, re.I)
    return m.group(1).upper() if m else None


def _plazas(texto: str) -> int | None:
    m = re.search(r"(?:N[uú]m\. de plazas totales|Plazas)\s*([\d.]+)", texto, re.I)
    if not m:
        return None
    try:
        return int(m.group(1).replace(".", ""))
    except ValueError:
        return None


def _turno(texto: str) -> str | None:
    normal = _sin_acentos(texto)
    if "promocion interna" in normal:
        return "PROMOCION_INTERNA"
    if "turno libre" in normal:
        return "TURNO_LIBRE"
    if "discapacidad intelectual" in normal:
        return "DISCAPACIDAD_INTELECTUAL"
    if "discapacidad" in normal:
        return "DISCAPACIDAD"
    return None


def _estado(texto: str) -> str:
    normal = _sin_acentos(texto)
    if "plazo abierto" in normal or re.search(r"\bplazo abierto\b", normal):
        return "ABIERTO"
    if "pendiente" in normal:
        return "PENDIENTE"
    if "cerrado" in normal or "tancat" in normal:
        return "CERRADO"
    return "EN_SEGUIMIENTO"


def _ultima_fecha_publicacion(texto: str) -> date | None:
    fechas = []
    for patron in (
        r"Fecha publicación\s*:\s*(\d{2}-\d{2}-\d{4})",
        r"Publicación\s+(?:DOGV[^ ]*\s+)?(?:n[uú]m\.[^ ]+\s+)?(?:de\s+)?(\d{2}-\d{2}-\d{4})",
    ):
        fechas.extend(_fecha(x) for x in re.findall(patron, texto, re.I))
    fechas = [x for x in fechas if x]
    return max(fechas) if fechas else None


def parsear_detalle(url: str, html: str, id_emp: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    texto = _normalizar(soup.get_text(" ", strip=True))
    titulo = _denominacion(soup, texto) or f"Proceso GVA {id_emp}"
    tipo = _tipo_convocatoria(texto)
    anio = _anio(titulo)
    grupo = _grupo(texto)
    plazas = _plazas(texto)
    fecha_pub = _ultima_fecha_publicacion(texto)
    m_plazo = re.search(r"(?:Plazo|Termini)\s+(?:de la etapa actual\s*)?(?:Desde|Des de)\s+(\d{2}-\d{2}-\d{4})(?:\s+(?:hasta|a)\s+(\d{2}-\d{2}-\d{4}))?", texto, re.I)
    fecha_apertura = _fecha(m_plazo.group(1)) if m_plazo else None
    fecha_cierre = _fecha(m_plazo.group(2)) if m_plazo and m_plazo.group(2) else None
    m_codigo = re.search(r"C[oó]digo GVA\s*:?\s*(\d+)", texto, re.I)
    codigo_gva = m_codigo.group(1) if m_codigo else str(id_emp)
    m_sia = re.search(r"C[oó]digo SIA\s*:?\s*([0-9]+)", texto, re.I)
    codigo_sia = m_sia.group(1) if m_sia else None
    hash_contenido = hashlib.sha256(texto.encode("utf-8")).hexdigest()
    return {
        "codigo_externo": codigo_gva,
        "identificador_estable": f"GVA:{id_emp}",
        "denominacion": titulo,
        "grupo": grupo,
        "tipo_proceso": tipo,
        "turno": _turno(texto),
        "plazas": plazas,
        "estado": _estado(texto),
        "anio_convocatoria": anio,
        "fecha_apertura": fecha_apertura,
        "fecha_cierre": fecha_cierre,
        "ultima_publicacion_at": datetime.combine(fecha_pub, datetime.min.time(), tzinfo=timezone.utc) if fecha_pub else None,
        "datos_json": {"id_emp": id_emp, "codigo_gva": codigo_gva, "codigo_sia": codigo_sia, "url_detalle": url},
        "publicacion": {
            "referencia": f"GVA:{id_emp}:{hash_contenido}",
            "tipo": "DETALLE",
            "titulo": titulo,
            "fecha_publicacion": fecha_pub,
            "url": url,
            "contenido_hash": hash_contenido,
            "contenido_texto": texto,
            "datos_json": {"id_emp": id_emp, "codigo_gva": codigo_gva},
        },
    }


def descubrir_detalles(client: httpx.Client, max_paginas: int = 3) -> list[tuple[int, str]]:
    encontrados: dict[int, str] = {}
    for pagina in range(1, max_paginas + 1):
        respuesta = client.get(GVA_SEARCH_URL, params={"pagina": pagina, "tipoOrganismo": "1", "plazos": "A", "tamanyoPagina": "30"})
        respuesta.raise_for_status()
        soup = BeautifulSoup(respuesta.text, "html.parser")
        for enlace in soup.select('a[href*="detall-ocupacio-publica"]'):
            href = enlace.get("href")
            if not href:
                continue
            m = re.search(r"id_emp=(\d+)", href)
            if m:
                encontrados[int(m.group(1))] = urljoin(GVA_BASE_URL, href)
    return sorted(encontrados.items())


def _es_incluido(tipo: str | None) -> bool:
    normal = _sin_acentos(tipo or "")
    return any(t in normal for t in TIPOS_INCLUIDOS)


def _es_del_ambito(proceso: dict[str, Any]) -> bool:
    anio = proceso.get("anio_convocatoria")
    if anio in (2026, 2027):
        return True
    fecha = proceso["publicacion"].get("fecha_publicacion")
    return bool(fecha and fecha >= date(2026, 1, 1))


def importar_gva_robusto(*, max_paginas: int = 3, max_detalles: int | None = None) -> dict[str, Any]:
    stats: dict[str, Any] = {"descubiertos": 0, "procesos": 0, "publicaciones": 0, "cambios": 0, "diagnostico": []}
    headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        detalles = descubrir_detalles(client, max_paginas=max_paginas)
        if max_detalles is not None:
            detalles = detalles[:max_detalles]
        stats["descubiertos"] = len(detalles)
        with get_connection() as connection:
            with connection.cursor() as cursor:
                for id_emp, url in detalles:
                    respuesta = client.get(url)
                    respuesta.raise_for_status()
                    proceso = parsear_detalle(url, respuesta.text, id_emp)
                    tipo = proceso.get("tipo_proceso")
                    if not _es_incluido(tipo) or not _es_del_ambito(proceso):
                        stats["diagnostico"].append({"id_emp": id_emp, "url": url, "tipo": tipo, "anio_convocatoria": proceso.get("anio_convocatoria"), "fecha_publicacion": proceso["publicacion"].get("fecha_publicacion"), "motivo": "tipo_excluido" if not _es_incluido(tipo) else "fuera_ambito"})
                        continue
                    campos = ["denominacion", "grupo", "tipo_proceso", "turno", "plazas", "estado", "anio_convocatoria", "fecha_apertura", "fecha_cierre"]
                    cursor.execute("SELECT id, denominacion, grupo, tipo_proceso, turno, plazas, estado, anio_convocatoria, fecha_apertura, fecha_cierre FROM procesos WHERE identificador_estable=%s", (proceso["identificador_estable"],))
                    existente = cursor.fetchone()
                    valores = tuple(proceso.get(c) for c in campos)
                    if existente:
                        proceso_id = existente[0]
                        for i, campo in enumerate(campos, start=1):
                            anterior, nuevo = existente[i], valores[i - 1]
                            if anterior != nuevo:
                                cursor.execute("INSERT INTO cambios (proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen) VALUES (%s,%s,%s,%s,%s,%s)", (proceso_id, "ACTUALIZACION", campo, str(anterior) if anterior is not None else None, str(nuevo) if nuevo is not None else None, f"Cambio en {campo}: {anterior!r} -> {nuevo!r}"))
                                stats["cambios"] += 1
                        cursor.execute("UPDATE procesos SET codigo_externo=%s,denominacion=%s,grupo=%s,tipo_proceso=%s,turno=%s,plazas=%s,estado=%s,anio_convocatoria=%s,fecha_apertura=%s,fecha_cierre=%s,ultima_publicacion_at=COALESCE(%s,ultima_publicacion_at),fuente_principal_id=%s,datos_json=%s,updated_at=NOW() WHERE id=%s", (proceso["codigo_externo"],proceso["denominacion"],proceso["grupo"],proceso["tipo_proceso"],proceso["turno"],proceso["plazas"],proceso["estado"],proceso["anio_convocatoria"],proceso["fecha_apertura"],proceso["fecha_cierre"],proceso["ultima_publicacion_at"],GVA_FUENTE_ID,Jsonb(proceso["datos_json"]),proceso_id))
                    else:
                        cursor.execute("INSERT INTO procesos (organismo_id,codigo_externo,identificador_estable,denominacion,grupo,tipo_proceso,turno,plazas,estado,anio_convocatoria,fecha_apertura,fecha_cierre,ultima_publicacion_at,fuente_principal_id,datos_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (GVA_ORGANISMO_ID,proceso["codigo_externo"],proceso["identificador_estable"],proceso["denominacion"],proceso["grupo"],proceso["tipo_proceso"],proceso["turno"],proceso["plazas"],proceso["estado"],proceso["anio_convocatoria"],proceso["fecha_apertura"],proceso["fecha_cierre"],proceso["ultima_publicacion_at"],GVA_FUENTE_ID,Jsonb(proceso["datos_json"])))
                        proceso_id = cursor.fetchone()[0]
                    stats["procesos"] += 1
                    pub = proceso["publicacion"]
                    cursor.execute("SELECT 1 FROM publicaciones WHERE proceso_id=%s AND contenido_hash=%s LIMIT 1", (proceso_id, pub["contenido_hash"]))
                    if cursor.fetchone() is None:
                        cursor.execute("INSERT INTO publicaciones (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,contenido_hash,contenido_texto,datos_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (proceso_id,GVA_FUENTE_ID,pub["referencia"],pub["tipo"],pub["titulo"],pub["fecha_publicacion"],pub["url"],pub["contenido_hash"],pub["contenido_texto"],Jsonb(pub["datos_json"])))
                        stats["publicaciones"] += 1
            connection.commit()
    return stats
