from __future__ import annotations

import re
from datetime import date
from typing import Any

import httpx
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from .database import get_connection
from . import gva as legacy

GVA_SEARCH_URL = legacy.GVA_SEARCH_URL
GVA_BASE_URL = legacy.GVA_BASE_URL
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


def _sin_acentos(texto: str) -> str:
    return legacy._sin_acentos(texto)


def _normalizar(texto: str) -> str:
    return legacy._normalizar(texto)


def _tipo_convocatoria(texto: str) -> str | None:
    normal = _sin_acentos(texto)
    m = re.search(r"convocatoria\s+(.{1,140}?)(?=\s+(?:prueba|grupo|titulacion|enlace a organismo|places|plazas)\b)", normal, re.I)
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
        ("concurso general de meritos", "Concurso general de méritos"),
        ("concurso-oposicion", "Concurso-oposición"),
        ("concurso", "Concurso"),
        ("cobertura interina", "Cobertura interina"),
        ("comision de servicio", "Comisión de servicio"),
        ("libre designacion", "Libre designación"),
        ("seleccion personal directivo", "Selección personal directivo"),
        ("procesos especiales", "Procesos especiales"),
    ):
        if patron in valor:
            return nombre
    return valor


def _turno(texto: str) -> str | None:
    normal = _sin_acentos(texto)
    candidatos = []
    for patron, valor in (("promocion interna", "PROMOCION_INTERNA"), ("turno libre", "TURNO_LIBRE"), ("discapacidad intelectual", "DISCAPACIDAD_INTELECTUAL"), ("discapacidad", "DISCAPACIDAD")):
        posicion = normal.find(patron)
        if posicion >= 0:
            candidatos.append((posicion, valor))
    return min(candidatos, key=lambda x: x[0])[1] if candidatos else None


def _es_incluido(tipo: str | None) -> bool:
    normal = _sin_acentos(tipo or "")
    return any(patron in normal for patron in TIPOS_INCLUIDOS)


def _extraer_organismo(texto: str, titulo: str) -> str | None:
    normal = _normalizar(texto)
    posicion = normal.find(titulo)
    if posicion >= 0:
        resto = normal[posicion + len(titulo):]
        m = re.match(r"\s*(.*?)\s+Etapa actual\s*:", resto, re.I)
        if m and _normalizar(m.group(1)):
            return _normalizar(m.group(1))
    m = re.search(r"\b(Conselleria[^|]+?|Labora[^|]+?|Ag[eè]ncia[^|]+?|Institut[^|]+?|Turisme Comunitat Valenciana)\s+Etapa actual\s*:", normal, re.I)
    return _normalizar(m.group(1)) if m else None


def _resolver_organismo(texto: str, titulo: str, organismo: str | None) -> tuple[int | None, str]:
    normal = _sin_acentos(f"{titulo} {organismo or ''} {texto}")
    externos = (
        "administracion de justicia", "tramitacion procesal", "gestion procesal",
        "auxilio judicial", "orden pjc/", "ministerio de justicia",
        "administracion general del estado",
    )
    if any(marca in normal for marca in externos):
        return None, "organismo_externo"
    org_normal = _sin_acentos(organismo or "")
    if not org_normal:
        return None, "organismo_no_identificado"
    if (
        "generalitat valenciana" in org_normal
        or org_normal.startswith("conselleria ")
        or "labora" in org_normal
        or "agencia valenciana" in org_normal
        or "institut valencia" in org_normal
        or "instituto valenciano" in org_normal
        or "turisme comunitat valenciana" in org_normal
    ):
        return GVA_ORGANISMO_ID, "generalitat_valenciana"
    return None, "organismo_no_pertenece_a_generalitat"


def parsear_detalle(url: str, html: str, id_emp: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    texto = _normalizar(soup.get_text(" ", strip=True))
    title_tag = soup.find("title")
    titulo = _normalizar(title_tag.get_text(" ", strip=True)) if title_tag else f"Proceso GVA {id_emp}"
    titulo = re.sub(r"\s*-\s*Sede Electr[oó]nica\s*-\s*Generalitat Valenciana\s*$", "", titulo, flags=re.I)
    base = legacy.parsear_detalle(url, html, id_emp)
    if not titulo or _sin_acentos(titulo) == "navegacion":
        titulo = base.get("denominacion") or f"Proceso GVA {id_emp}"
    tipo = _tipo_convocatoria(texto) or base.get("tipo_proceso")
    organismo = _extraer_organismo(texto, titulo)
    organismo_id, motivo = _resolver_organismo(texto, titulo, organismo)
    base.update({
        "denominacion": titulo,
        "tipo_proceso": tipo,
        "turno": _turno(titulo + " " + texto),
        "organismo_id": organismo_id,
        "datos_json": {**(base.get("datos_json") or {}), "organismo_detectado": organismo, "organismo_id_resuelto": organismo_id, "organismo_motivo": motivo},
    })
    return base


def descubrir_detalles(client: httpx.Client, max_paginas: int = 3) -> list[tuple[int, str]]:
    return legacy.descubrir_detalles(client, max_paginas=max_paginas)


def _es_del_ambito(proceso: dict[str, Any]) -> bool:
    if proceso.get("anio_convocatoria") in (2026, 2027):
        return True
    fecha = proceso.get("ultima_publicacion_at")
    return bool(fecha and fecha.date() >= date(2026, 1, 1))


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
                    if not _es_incluido(tipo):
                        stats["diagnostico"].append({"id_emp": id_emp, "tipo": tipo, "organismo": proceso["datos_json"].get("organismo_detectado"), "motivo": "tipo_excluido"})
                        continue
                    if not _es_del_ambito(proceso):
                        stats["diagnostico"].append({"id_emp": id_emp, "tipo": tipo, "anio_convocatoria": proceso.get("anio_convocatoria"), "motivo": "fuera_ambito"})
                        continue
                    if proceso.get("organismo_id") != GVA_ORGANISMO_ID:
                        stats["diagnostico"].append({"id_emp": id_emp, "tipo": tipo, "organismo": proceso["datos_json"].get("organismo_detectado"), "motivo": proceso["datos_json"].get("organismo_motivo")})
                        continue
                    campos = ["denominacion", "grupo", "tipo_proceso", "turno", "plazas", "estado", "anio_convocatoria", "fecha_apertura", "fecha_cierre"]
                    cursor.execute("SELECT id, denominacion, grupo, tipo_proceso, turno, plazas, estado, anio_convocatoria, fecha_apertura, fecha_cierre FROM procesos WHERE identificador_estable=%s", (proceso["identificador_estable"],))
                    existente = cursor.fetchone()
                    if existente:
                        proceso_id = existente[0]
                        for indice, campo in enumerate(campos, start=1):
                            anterior, nuevo = existente[indice], proceso.get(campo)
                            if anterior != nuevo:
                                cursor.execute("INSERT INTO cambios (proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen) VALUES (%s,%s,%s,%s,%s,%s)", (proceso_id, "ACTUALIZACION", campo, str(anterior) if anterior is not None else None, str(nuevo) if nuevo is not None else None, f"Cambio en {campo}: {anterior!r} -> {nuevo!r}"))
                                stats["cambios"] += 1
                        cursor.execute("UPDATE procesos SET organismo_id=%s,codigo_externo=%s,denominacion=%s,grupo=%s,tipo_proceso=%s,turno=%s,plazas=%s,estado=%s,anio_convocatoria=%s,fecha_apertura=%s,fecha_cierre=%s,ultima_publicacion_at=COALESCE(%s,ultima_publicacion_at),fuente_principal_id=%s,datos_json=%s,updated_at=NOW() WHERE id=%s", (GVA_ORGANISMO_ID,proceso["codigo_externo"],proceso["denominacion"],proceso["grupo"],proceso["tipo_proceso"],proceso["turno"],proceso["plazas"],proceso["estado"],proceso["anio_convocatoria"],proceso["fecha_apertura"],proceso["fecha_cierre"],proceso["ultima_publicacion_at"],GVA_FUENTE_ID,Jsonb(proceso["datos_json"]),proceso_id))
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
