from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

GVA_SEARCH_URL = "https://sede.gva.es/es/cercador-ocupacio-publica"
GVA_BASE_URL = "https://sede.gva.es"
GVA_ORGANISMO_ID = 1
GVA_FUENTE_ID = 1

TIPOS_INCLUIDOS = {
    "oposicion", "bolsa de trabajo", "promocion interna",
    "contratacion laboral temporal", "contratacion laboral indefinida",
    "proceso de estabilizacion", "acto unico telematico", "acte unic telematic",
    "anuncio dificil cobertura", "concurso general de meritos", "concurso-oposicion",
    "concurso", "cobertura interina", "comision de servicio", "libre designacion",
    "seleccion personal directivo", "procesos especiales",
}


def _normalizar(texto: str) -> str:
    return " ".join(texto.replace("\xa0", " ").split())


def _sin_acentos(texto: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _fecha(valor: str | None) -> date | None:
    if not valor:
        return None
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", valor)
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _anio(texto: str) -> int | None:
    normal = _sin_acentos(texto)
    for patron in (r"convocatoria\s+\d+[/-](\d{2})", r"convocatoria\s+\d{1,3}/(\d{4})"):
        m = re.search(patron, normal)
        if m:
            valor = int(m.group(1))
            return 2000 + valor if valor < 100 else valor
    m = re.search(r"\b(2026|2027)\b", texto)
    return int(m.group(1)) if m else None


def _tipo_proceso(texto: str) -> str | None:
    m = re.search(r"(?:Convocatoria|Convocatoria)\s*:?\s*(.*?)(?=\s+(?:Prueba|Grupo|Titulación|Titulacio|Enlace a organismo|Places|Plazas)\b|$)", texto, re.I)
    return _normalizar(m.group(1)) if m else None


def _plazas(texto: str) -> int | None:
    m = re.search(r"(?:Plazas|Places)\s*([\d.]+)", texto, re.I)
    if not m:
        return None
    try:
        return int(m.group(1).replace(".", ""))
    except ValueError:
        return None


def _grupo(texto: str) -> str | None:
    m = re.search(r"\bGrupo\s+([A-Z0-9/]+)", texto, re.I)
    return m.group(1).upper() if m else None


def _turno(texto: str) -> str | None:
    normal = _sin_acentos(texto)
    candidatos = []
    for patron, valor in (("promocion interna", "PROMOCION_INTERNA"), ("turno libre", "TURNO_LIBRE"), ("discapacidad intelectual", "DISCAPACIDAD_INTELECTUAL"), ("discapacidad", "DISCAPACIDAD")):
        posicion = normal.find(patron)
        if posicion >= 0:
            candidatos.append((posicion, valor))
    return min(candidatos, key=lambda x: x[0])[1] if candidatos else None


def _estado(texto: str) -> str:
    normal = _sin_acentos(texto)
    if "plazo abierto" in normal or "abierto" in normal:
        return "ABIERTO"
    if "pendiente" in normal:
        return "PENDIENTE"
    if "cerrado" in normal or "tancat" in normal:
        return "CERRADO"
    return "EN_SEGUIMIENTO"


def _ultima_fecha_publicacion(texto: str) -> date | None:
    fechas = re.findall(r"Fecha publicaci[oó]n\s*:\s*(\d{2}-\d{2}-\d{4})", texto, re.I)
    parsed = [_fecha(x) for x in fechas]
    parsed = [x for x in parsed if x]
    return max(parsed) if parsed else None


def _denominacion(soup: BeautifulSoup, texto: str) -> str:
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title = _normalizar(title)
    title = re.sub(r"\s*-\s*Sede Electr[oó]nica\s*-\s*Generalitat Valenciana\s*$", "", title, flags=re.I)
    if title and _sin_acentos(title) != "navegacion":
        return title
    h1 = soup.find("h1")
    return _normalizar(h1.get_text(" ", strip=True)) if h1 else f"Proceso GVA {texto[:80]}"


def _es_incluido(tipo: str | None) -> bool:
    normal = _sin_acentos(tipo or "")
    return any(t in normal for t in TIPOS_INCLUIDOS)


def _es_del_ambito(anio_convocatoria: int | None, fecha_pub: date | None) -> bool:
    if anio_convocatoria in (2026, 2027):
        return True
    return fecha_pub is not None and fecha_pub >= date(2026, 1, 1)


def parsear_detalle(url: str, html: str, id_emp: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    texto = _normalizar(soup.get_text(" ", strip=True))
    denominacion = _denominacion(soup, texto)
    tipo = _tipo_proceso(texto)
    grupo = _grupo(texto)
    plazas = _plazas(texto)
    anio_convocatoria = _anio(denominacion + " " + texto)

    m_plazo = re.search(r"(?:Plazo|Termini)\s+(?:de la etapa actual\s*)?(?:Desde|Des de)\s+(\d{2}-\d{2}-\d{4})\s+(?:hasta|a)\s+(\d{2}-\d{2}-\d{4})", texto, re.I)
    fecha_apertura = _fecha(m_plazo.group(1)) if m_plazo else None
    fecha_cierre = _fecha(m_plazo.group(2)) if m_plazo else None
    m_codigo = re.search(r"C[oó]digo GVA\s*:?\s*(\d+)", texto, re.I)
    codigo_gva = m_codigo.group(1) if m_codigo else str(id_emp)
    m_sia = re.search(r"C[oó]digo SIA\s*:?\s*([0-9]+)", texto, re.I)
    codigo_sia = m_sia.group(1) if m_sia else None

    organismo = None
    marker = re.search(r"Enlace a organismo\s+(.+?)\s+Plazas\b", texto, re.I)
    if marker:
        organismo = _normalizar(marker.group(1))
    fecha_pub = _ultima_fecha_publicacion(texto)
    hash_contenido = hashlib.sha256(html.encode("utf-8")).hexdigest()

    return {
        "codigo_externo": codigo_gva,
        "identificador_estable": f"GVA:{id_emp}",
        "denominacion": denominacion,
        "grupo": grupo,
        "tipo_proceso": tipo,
        "turno": _turno(denominacion + " " + texto),
        "plazas": plazas,
        "estado": _estado(texto),
        "anio_convocatoria": anio_convocatoria,
        "fecha_apertura": fecha_apertura,
        "fecha_cierre": fecha_cierre,
        "ultima_publicacion_at": datetime.combine(fecha_pub, datetime.min.time(), tzinfo=timezone.utc) if fecha_pub else None,
        "datos_json": {"id_emp": id_emp, "codigo_gva": codigo_gva, "codigo_sia": codigo_sia, "organismo_detectado": organismo, "url_detalle": url},
        "publicacion": {"referencia": f"GVA:{id_emp}:{hash_contenido}", "tipo": "DETALLE", "titulo": denominacion, "fecha_publicacion": fecha_pub, "url": url, "contenido_hash": hash_contenido, "contenido_texto": texto, "datos_json": {"id_emp": id_emp, "codigo_gva": codigo_gva}},
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
            match = re.search(r"id_emp=(\d+)", href)
            if match:
                encontrados[int(match.group(1))] = urljoin(GVA_BASE_URL, href)
    return sorted(encontrados.items())


def importar_gva(*, max_paginas: int = 3, max_detalles: int | None = None) -> dict[str, Any]:
    from .database import get_connection
    estadisticas: dict[str, Any] = {"descubiertos": 0, "procesos": 0, "publicaciones": 0, "cambios": 0, "diagnostico": []}
    headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        detalles = descubrir_detalles(client, max_paginas=max_paginas)
        if max_detalles is not None:
            detalles = detalles[:max_detalles]
        estadisticas["descubiertos"] = len(detalles)
        with get_connection() as connection:
            with connection.cursor() as cursor:
                for id_emp, url in detalles:
                    respuesta = client.get(url)
                    respuesta.raise_for_status()
                    proceso = parsear_detalle(url, respuesta.text, id_emp)
                    tipo = proceso.get("tipo_proceso") or ""
                    fecha_pub = proceso["ultima_publicacion_at"].date() if proceso.get("ultima_publicacion_at") else None
                    if not _es_incluido(tipo):
                        estadisticas["diagnostico"].append({"id_emp": id_emp, "tipo": tipo, "fecha_publicacion": str(fecha_pub) if fecha_pub else None, "motivo": "tipo_excluido"})
                        continue
                    if not _es_del_ambito(proceso.get("anio_convocatoria"), fecha_pub):
                        estadisticas["diagnostico"].append({"id_emp": id_emp, "tipo": tipo, "anio_convocatoria": proceso.get("anio_convocatoria"), "fecha_publicacion": str(fecha_pub) if fecha_pub else None, "motivo": "fuera_ambito"})
                        continue
                    cursor.execute("SELECT id, denominacion, grupo, tipo_proceso, turno, plazas, estado, anio_convocatoria, fecha_apertura, fecha_cierre FROM procesos WHERE identificador_estable=%s", (proceso["identificador_estable"],))
                    existente = cursor.fetchone()
                    campos = ["denominacion", "grupo", "tipo_proceso", "turno", "plazas", "estado", "anio_convocatoria", "fecha_apertura", "fecha_cierre"]
                    if existente:
                        proceso_id = existente[0]
                        for indice, campo in enumerate(campos, start=1):
                            anterior, nuevo = existente[indice], proceso.get(campo)
                            if anterior != nuevo:
                                cursor.execute("INSERT INTO cambios (proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen) VALUES (%s,%s,%s,%s,%s,%s)", (proceso_id,"ACTUALIZACION",campo,str(anterior) if anterior is not None else None,str(nuevo) if nuevo is not None else None,f"Cambio en {campo}: {anterior!r} -> {nuevo!r}"))
                                estadisticas["cambios"] += 1
                        cursor.execute("UPDATE procesos SET codigo_externo=%s,denominacion=%s,grupo=%s,tipo_proceso=%s,turno=%s,plazas=%s,estado=%s,anio_convocatoria=%s,fecha_apertura=%s,fecha_cierre=%s,ultima_publicacion_at=COALESCE(%s,ultima_publicacion_at),fuente_principal_id=%s,datos_json=%s,updated_at=NOW() WHERE id=%s", (proceso["codigo_externo"],proceso["denominacion"],proceso["grupo"],proceso["tipo_proceso"],proceso["turno"],proceso["plazas"],proceso["estado"],proceso["anio_convocatoria"],proceso["fecha_apertura"],proceso["fecha_cierre"],proceso["ultima_publicacion_at"],GVA_FUENTE_ID,proceso["datos_json"],proceso_id))
                    else:
                        cursor.execute("INSERT INTO procesos (organismo_id,codigo_externo,identificador_estable,denominacion,grupo,tipo_proceso,turno,plazas,estado,anio_convocatoria,fecha_apertura,fecha_cierre,ultima_publicacion_at,fuente_principal_id,datos_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (GVA_ORGANISMO_ID,proceso["codigo_externo"],proceso["identificador_estable"],proceso["denominacion"],proceso["grupo"],proceso["tipo_proceso"],proceso["turno"],proceso["plazas"],proceso["estado"],proceso["anio_convocatoria"],proceso["fecha_apertura"],proceso["fecha_cierre"],proceso["ultima_publicacion_at"],GVA_FUENTE_ID,proceso["datos_json"]))
                        proceso_id = cursor.fetchone()[0]
                        estadisticas["procesos"] += 1
                    publicacion = proceso["publicacion"]
                    cursor.execute("SELECT id FROM publicaciones WHERE fuente_id=%s AND referencia=%s AND url=%s", (GVA_FUENTE_ID,publicacion["referencia"],publicacion["url"]))
                    if cursor.fetchone() is None:
                        cursor.execute("INSERT INTO publicaciones (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,contenido_hash,contenido_texto,datos_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (proceso_id,GVA_FUENTE_ID,publicacion["referencia"],publicacion["tipo"],publicacion["titulo"],publicacion["fecha_publicacion"],publicacion["url"],publicacion["contenido_hash"],publicacion["contenido_texto"],publicacion["datos_json"]))
                        estadisticas["publicaciones"] += 1
            connection.commit()
    return estadisticas


def limpiar_gva_navegacion() -> dict[str, int]:
    from .database import get_connection
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM procesos WHERE denominacion='Navegación' AND datos_json->>'organismo_detectado'='Navegación'")
            eliminados = cursor.rowcount
        connection.commit()
    return {"eliminados": eliminados}
