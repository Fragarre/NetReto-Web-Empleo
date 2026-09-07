from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from .database import get_connection

INDEX_URL = "https://w3.alicante.es/rrhh/oposiciones/?idioma=es"
BASE_URL = "https://w3.alicante.es/"
ORGANISMO_ID = 5
FUENTE_ID = 5

TURNOS_EXCLUIDOS = (
    "promocion interna",
    "movilidad",
    "promocion interadministrativa",
    "promoción interna",
    "promoción interadministrativa",
)


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
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                return None
    return None


def _int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value.replace(".", ""))
    return int(m.group(0)) if m else None


def _query(url: str) -> dict[str, str]:
    q = parse_qs(urlparse(url).query)
    return {k: v[0] for k, v in q.items() if v}


def _enlaces_indice(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    resultado: list[tuple[str, str]] = []
    vistos: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(BASE_URL, a["href"])
        if "/rrhh/oposiciones/oposicion.php" not in href:
            continue
        if href in vistos:
            continue
        vistos.add(href)
        texto = _norm(a.get_text(" ", strip=True))
        if texto:
            resultado.append((texto, href))
    return resultado


def _campo_lineas(soup: BeautifulSoup, etiqueta: str) -> str | None:
    texto = soup.get_text("\n", strip=True)
    patron = re.compile(rf"^{re.escape(etiqueta)}:\s*(.+)$", re.I | re.M)
    m = patron.search(texto)
    return _norm(m.group(1)) if m else None


def _rango_instancias(texto: str) -> tuple[date | None, date | None]:
    m = re.search(
        r"Desde\s+(\d{1,2}/\d{1,2}/\d{4})\s+hasta\s+(\d{1,2}/\d{1,2}/\d{4})",
        texto,
        re.I,
    )
    if not m:
        return None, None
    return _fecha(m.group(1)), _fecha(m.group(2))


def _historial(soup: BeautifulSoup, codigo: str) -> list[dict[str, Any]]:
    encabezado = None
    for nodo in soup.find_all(["h2", "h3", "h4"]):
        if "historial del proceso selectivo" in _sin_acentos(_norm(nodo.get_text(" ", strip=True))):
            encabezado = nodo
            break
    if encabezado is None:
        return []

    hitos: list[dict[str, Any]] = []
    actual = encabezado.find_next_sibling()
    while actual is not None:
        if actual.name in {"h2", "h3", "h4"}:
            break
        bloque = _norm(actual.get_text(" ", strip=True))
        if bloque:
            fecha = _fecha(bloque)
            titulo = _norm(actual.get_text(" ", strip=True))
            if titulo and fecha:
                enlace = None
                a = actual.find("a", href=True)
                if a:
                    enlace = urljoin(BASE_URL, a["href"])
                    titulo = _norm(a.get_text(" ", strip=True)) or titulo
                clave = f"{codigo}|{fecha.isoformat()}|{titulo}|{enlace or ''}"
                h = hashlib.sha256(clave.encode("utf-8")).hexdigest()
                hitos.append({
                    "referencia": f"AALI:{codigo}:HITO:{fecha.isoformat()}:{h[:16]}",
                    "tipo": "SEGUIMIENTO",
                    "titulo": titulo,
                    "url": enlace,
                    "fecha_publicacion": fecha,
                    "contenido_hash": h,
                    "contenido_texto": titulo,
                    "datos_json": {"codigo": codigo, "hito": True},
                })
        actual = actual.find_next_sibling()

    return list({x["referencia"]: x for x in hitos}.values())


def parsear_detalle(url: str, titulo_indice: str, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    texto = _norm(soup.get_text(" ", strip=True))
    q = _query(url)
    anyo = q.get("anyo")
    idconv = q.get("idconv")
    nconv = q.get("nconv")
    codigo = f"{anyo or '0'}-{idconv or '0'}-{nconv or '0'}"

    h2 = soup.find("h2")
    denominacion = _norm(h2.get_text(" ", strip=True)) if h2 else titulo_indice
    if denominacion.lower() == "convocatorias de oposiciones":
        denominacion = titulo_indice

    grupo = _campo_lineas(soup, "Grupo")
    turno = _campo_lineas(soup, "Turno")
    acceso = _campo_lineas(soup, "Acceso")
    anio_texto = _campo_lineas(soup, "Año")
    fase = _campo_lineas(soup, "Fase actual") or ""
    plazas = _int(_campo_lineas(soup, "Plazas"))
    fecha_apertura, fecha_cierre = _rango_instancias(texto)

    fecha_examen = None
    m_examen = re.search(r"Fecha:\s*(\d{1,2}/\d{1,2}/\d{4})", texto, re.I)
    if m_examen:
        fecha_examen = _fecha(m_examen.group(1))

    turno_norm = _sin_acentos(turno or "")
    es_excluido = any(x in turno_norm for x in ("promocion interna", "movilidad", "promocion interadministrativa"))
    finalizado = "finalizado" in _sin_acentos(fase)
    estado = "FINALIZADO" if finalizado else "EN_CURSO"
    es_oportunidad = not es_excluido and not finalizado

    anio_convocatoria = None
    m_anio = re.search(r"20\d{2}", anio_texto or "")
    if m_anio:
        anio_convocatoria = int(m_anio.group(0))

    fuentes: list[dict[str, Any]] = []
    for a in soup.find_all("a", href=True):
        label = _norm(a.get_text(" ", strip=True))
        href = urljoin(BASE_URL, a["href"])
        if not label:
            continue
        if re.search(r"\b(B\.O\.P\.|B\.O\.E\.|D\.O\.G\.V\.)", label, re.I):
            fuentes.append({"titulo": label, "url": href, "fecha": _fecha(label)})

    ultima = max((x["fecha"] for x in fuentes if x["fecha"]), default=None)
    hash_detalle = hashlib.sha256(html.encode("utf-8")).hexdigest()

    return {
        "codigo_externo": codigo,
        "identificador_estable": f"AALI:{codigo}",
        "denominacion": denominacion,
        "grupo": grupo,
        "tipo_proceso": acceso,
        "sistema_selectivo": acceso.upper() if acceso else None,
        "turno": turno.upper().replace("-", "_").replace(" ", "_") if turno else None,
        "plazas": plazas,
        "estado": estado,
        "es_oportunidad": es_oportunidad,
        "anio_convocatoria": anio_convocatoria,
        "fecha_apertura": fecha_apertura,
        "fecha_cierre": fecha_cierre,
        "fecha_examen": fecha_examen,
        "ultima_publicacion_at": datetime.now(timezone.utc),
        "datos_json": {
            "url_detalle": url,
            "titulo_indice": titulo_indice,
            "fase_actual": fase,
            "fuentes": fuentes,
        },
        "publicacion": {
            "referencia": f"AALI:{codigo}:DETALLE:{hash_detalle}",
            "tipo": "DETALLE",
            "titulo": denominacion,
            "url": url,
            "fecha_publicacion": ultima,
            "contenido_hash": hash_detalle,
            "contenido_texto": texto,
            "datos_json": {"codigo": codigo},
        },
        "seguimiento": _historial(soup, codigo),
    }


def _upsert(cursor, datos: dict[str, Any]) -> tuple[int, bool]:
    cursor.execute(
        """
        INSERT INTO procesos (
            organismo_id,codigo_externo,identificador_estable,denominacion,grupo,
            tipo_proceso,sistema_selectivo,turno,plazas,estado,es_oportunidad,
            anio_convocatoria,fecha_apertura,fecha_cierre,fecha_examen,
            ultima_publicacion_at,fuente_principal_id,datos_json,updated_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (identificador_estable) DO UPDATE SET
            denominacion=EXCLUDED.denominacion,
            grupo=COALESCE(EXCLUDED.grupo,procesos.grupo),
            tipo_proceso=COALESCE(EXCLUDED.tipo_proceso,procesos.tipo_proceso),
            sistema_selectivo=COALESCE(EXCLUDED.sistema_selectivo,procesos.sistema_selectivo),
            turno=COALESCE(EXCLUDED.turno,procesos.turno),
            plazas=COALESCE(EXCLUDED.plazas,procesos.plazas),
            estado=EXCLUDED.estado,
            es_oportunidad=EXCLUDED.es_oportunidad,
            anio_convocatoria=COALESCE(EXCLUDED.anio_convocatoria,procesos.anio_convocatoria),
            fecha_apertura=COALESCE(EXCLUDED.fecha_apertura,procesos.fecha_apertura),
            fecha_cierre=COALESCE(EXCLUDED.fecha_cierre,procesos.fecha_cierre),
            fecha_examen=COALESCE(EXCLUDED.fecha_examen,procesos.fecha_examen),
            ultima_publicacion_at=EXCLUDED.ultima_publicacion_at,
            datos_json=EXCLUDED.datos_json,
            updated_at=NOW()
        RETURNING id,(xmax=0) AS inserted
        """,
        (
            ORGANISMO_ID,datos["codigo_externo"],datos["identificador_estable"],datos["denominacion"],datos["grupo"],
            datos["tipo_proceso"],datos["sistema_selectivo"],datos["turno"],datos["plazas"],datos["estado"],
            datos["es_oportunidad"],datos["anio_convocatoria"],datos["fecha_apertura"],datos["fecha_cierre"],
            datos["fecha_examen"],datos["ultima_publicacion_at"],FUENTE_ID,Jsonb(datos["datos_json"]),
        ),
    )
    row = cursor.fetchone()
    return int(row[0]), bool(row[1])


def _insertar_publicacion(cursor, proceso_id: int, pub: dict[str, Any]) -> bool:
    cursor.execute(
        "SELECT id FROM publicaciones WHERE fuente_id=%s AND referencia=%s LIMIT 1",
        (FUENTE_ID,pub["referencia"]),
    )
    if cursor.fetchone() is not None:
        return False
    cursor.execute(
        """
        INSERT INTO publicaciones(
            proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,
            contenido_hash,contenido_texto,datos_json
        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """,
        (
            proceso_id,FUENTE_ID,pub["referencia"],pub["tipo"],pub["titulo"],pub["fecha_publicacion"],
            pub["url"],pub["contenido_hash"],pub["contenido_texto"],Jsonb(pub["datos_json"]),
        ),
    )
    publicacion_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO cambios(proceso_id,publicacion_id,tipo,campo,valor_anterior,valor_nuevo,resumen,significativo)
        VALUES(%s,%s,'PUBLICACION','publicacion',NULL,%s,%s,TRUE)
        """,
        (proceso_id,publicacion_id,pub["referencia"],f"Nueva publicación oficial: {pub['titulo']}"),
    )
    return True


def importar_ayuntamiento_alicante(*, max_detalles: int = 150) -> dict[str, Any]:
    headers = {"User-Agent":"TuCoach-Empleo/1.0","Accept-Language":"es-ES,es;q=0.9"}
    estadisticas = {"descubiertos":0,"procesos":0,"publicaciones":0,"cambios":0,"errores":0,"errores_detalle":[]}
    with httpx.Client(timeout=30,headers=headers,follow_redirects=True) as client:
        indice = client.get(INDEX_URL)
        indice.raise_for_status()
        enlaces = _enlaces_indice(indice.text)[:max_detalles]
        estadisticas["descubiertos"] = len(enlaces)
        with get_connection() as connection:
            with connection.cursor() as cursor:
                for titulo,url in enlaces:
                    try:
                        respuesta = client.get(url)
                        respuesta.raise_for_status()
                        datos = parsear_detalle(url,titulo,respuesta.text)
                        proceso_id,inserted = _upsert(cursor,datos)
                        if inserted:
                            estadisticas["procesos"] += 1

                        pub = datos["publicacion"]
                        if _insertar_publicacion(cursor,proceso_id,pub):
                            estadisticas["publicaciones"] += 1
                            estadisticas["cambios"] += 1

                        for hito in datos["seguimiento"]:
                            if _insertar_publicacion(cursor,proceso_id,hito):
                                estadisticas["publicaciones"] += 1
                                estadisticas["cambios"] += 1
                    except Exception as exc:
                        estadisticas["errores"] += 1
                        estadisticas["errores_detalle"].append({"titulo":titulo,"url":url,"error":str(exc)})
                connection.commit()
    return estadisticas


def diagnosticar_ayuntamiento_alicante() -> dict[str, Any]:
    with httpx.Client(timeout=30,headers={"User-Agent":"TuCoach-Empleo/1.0"},follow_redirects=True) as client:
        r = client.get(INDEX_URL)
        r.raise_for_status()
        enlaces = _enlaces_indice(r.text)
        return {"url":INDEX_URL,"descubiertos":len(enlaces),"muestra":[{"titulo":t,"url":u} for t,u in enlaces[:20]]}
