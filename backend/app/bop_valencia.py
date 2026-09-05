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

BOP_URL = "https://bop.dival.es/bop/"
ORGANISMO_ID = 2
FUENTE_ID = 2
INCLUIDOS = ("convocatoria", "proceso selectivo", "selección", "seleccion", "oposición", "oposicion", "bolsa de trabajo", "bolsa de empleo")
EXCLUIDOS = ("provisión del puesto", "provision del puesto", "provisión de puestos", "provision de puestos", "libre designación", "libre designacion")


def _norm(s: str) -> str:
    return " ".join(s.replace("\xa0", " ").split())


def _sin(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def _fecha(s: str | None) -> date | None:
    if not s:
        return None
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _anio(s: str) -> int | None:
    m = re.search(r"convocatoria\s+\d{1,3}/(\d{2,4})", _sin(s))
    if m:
        n = int(m.group(1))
        return 2000 + n if n < 100 else n
    m = re.search(r"\b(2026|2027)\b", s)
    return int(m.group(1)) if m else None


def _plazas(s: str) -> int | None:
    for p in (r"selecci[oó]n de\s+(\d+)\s+plazas?", r"selecci[oó]n de una plaza"):
        m = re.search(p, s, re.I)
        if m:
            return int(m.group(1)) if m.lastindex else 1
    return None


def _grupo(s: str) -> str | None:
    m = re.search(r"subgrupo\s+([A-Z]\d(?:/\d)?)", s, re.I)
    return m.group(1).upper() if m else None


def _turno(s: str) -> str | None:
    n = _sin(s)
    if "promocion interna" in n:
        return "PROMOCION_INTERNA"
    if "turno libre" in n or "oposicion libre" in n:
        return "TURNO_LIBRE"
    if "estabilizacion" in n:
        return "ESTABILIZACION"
    return None


def _tipo(s: str) -> str:
    n = _sin(s)
    if "bolsa de trabajo" in n or "bolsa de empleo" in n:
        return "Bolsa de trabajo"
    if "concurso-oposicion" in n or "concurso oposicion" in n:
        return "Concurso-oposición"
    if "oposicion" in n:
        return "Oposición"
    return "Proceso selectivo"


def _incluido(titulo: str) -> bool:
    n = _sin(titulo)
    if any(_sin(x) in n for x in EXCLUIDOS):
        return False
    return any(_sin(x) in n for x in INCLUIDOS)


def descubrir_anuncios(client: httpx.Client) -> list[dict[str, Any]]:
    r = client.get(BOP_URL)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for a in soup.find_all("a", href=True):
        titulo = _norm(a.get_text(" ", strip=True))
        href = str(a.get("href"))
        if not titulo or not href or href in vistos or not _incluido(titulo):
            continue
        cont = a
        texto = ""
        for _ in range(6):
            cont = cont.parent
            if cont is None:
                break
            texto = _norm(cont.get_text(" ", strip=True))
            if "Núm. registre" in texto or "Núm. registro" in texto:
                break
        if "Diputació" not in texto and "Diputación" not in texto:
            continue
        if "Gestió de Recursos Humans" not in texto and "Gestión de Recursos Humanos" not in texto:
            continue
        m = re.search(r"(?:N[uú]m\.\s*(?:de\s*)?registre|N[uú]m\.\s*registro)\s*:?\s*(\d{4}/\d{5})", texto, re.I)
        fecha_m = re.search(r"(?:Data publicaci[oó]|Fecha publicaci[oó]n)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", texto, re.I)
        fecha = _fecha(fecha_m.group(1)) if fecha_m else _fecha(texto)
        url = urljoin(BOP_URL, href)
        vistos.add(href)
        resultados.append({"titulo": titulo, "url": url, "registro": m.group(1) if m else None, "fecha_publicacion": fecha})
    return resultados


def _obtener_texto(client: httpx.Client, url: str) -> str:
    r = client.get(url)
    r.raise_for_status()
    return _norm(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))


def importar_bop_valencia() -> dict[str, Any]:
    stats: dict[str, Any] = {"descubiertos": 0, "procesos": 0, "publicaciones": 0, "cambios": 0, "anuncios": []}
    headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        anuncios = descubrir_anuncios(client)
        stats["descubiertos"] = len(anuncios)
        with get_connection() as connection:
            with connection.cursor() as cursor:
                for anuncio in anuncios:
                    texto = _obtener_texto(client, anuncio["url"])
                    anio = _anio(anuncio["titulo"] + " " + texto)
                    if anio is not None and anio not in {2026, 2027}:
                        continue
                    referencia = anuncio["registro"] or hashlib.sha256(anuncio["url"].encode()).hexdigest()[:24]
                    estable = f"BOPV:{referencia}"
                    fecha = anuncio["fecha_publicacion"]
                    ultima = datetime.combine(fecha, datetime.min.time(), tzinfo=timezone.utc) if fecha else None
                    titulo = anuncio["titulo"]
                    valores = (titulo, _grupo(titulo + " " + texto), _tipo(titulo), _turno(titulo + " " + texto), _plazas(titulo), "EN_SEGUIMIENTO", anio)
                    cursor.execute("SELECT id, denominacion, grupo, tipo_proceso, turno, plazas, estado, anio_convocatoria FROM procesos WHERE identificador_estable=%s", (estable,))
                    existente = cursor.fetchone()
                    campos = ("denominacion", "grupo", "tipo_proceso", "turno", "plazas", "estado", "anio_convocatoria")
                    if existente:
                        proceso_id = existente[0]
                        for i, campo in enumerate(campos, 1):
                            if existente[i] != valores[i - 1]:
                                cursor.execute("INSERT INTO cambios (proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen) VALUES (%s,%s,%s,%s,%s,%s)", (proceso_id, "ACTUALIZACION", campo, str(existente[i]) if existente[i] is not None else None, str(valores[i - 1]) if valores[i - 1] is not None else None, f"Cambio en {campo}: {existente[i]!r} -> {valores[i - 1]!r}"))
                                stats["cambios"] += 1
                        cursor.execute("UPDATE procesos SET denominacion=%s,grupo=%s,tipo_proceso=%s,turno=%s,plazas=%s,estado=%s,anio_convocatoria=%s,fecha_convocatoria=COALESCE(%s,fecha_convocatoria),ultima_publicacion_at=COALESCE(%s,ultima_publicacion_at),fuente_principal_id=%s,datos_json=%s,updated_at=NOW() WHERE id=%s", (*valores, fecha, ultima, FUENTE_ID, Jsonb({"registro": anuncio["registro"], "url": anuncio["url"]}), proceso_id))
                    else:
                        cursor.execute("INSERT INTO procesos (organismo_id,codigo_externo,identificador_estable,denominacion,grupo,tipo_proceso,turno,plazas,estado,anio_convocatoria,fecha_convocatoria,ultima_publicacion_at,fuente_principal_id,datos_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (ORGANISMO_ID, anuncio["registro"], estable, *valores, fecha, ultima, FUENTE_ID, Jsonb({"registro": anuncio["registro"], "url": anuncio["url"]})))
                        proceso_id = cursor.fetchone()[0]
                        stats["procesos"] += 1
                    contenido_hash = hashlib.sha256(texto.encode("utf-8")).hexdigest()
                    cursor.execute("SELECT 1 FROM publicaciones WHERE proceso_id=%s AND contenido_hash=%s LIMIT 1", (proceso_id, contenido_hash))
                    if cursor.fetchone() is None:
                        cursor.execute("INSERT INTO publicaciones (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,contenido_hash,contenido_texto,datos_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (proceso_id, FUENTE_ID, referencia, "BOP", titulo, fecha, anuncio["url"], contenido_hash, texto, Jsonb({"registro": anuncio["registro"], "url": anuncio["url"]})))
                        stats["publicaciones"] += 1
                    stats["anuncios"].append({"registro": anuncio["registro"], "titulo": titulo, "fecha_publicacion": fecha.isoformat() if fecha else None, "proceso_id": proceso_id})
            connection.commit()
    return stats
