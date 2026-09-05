from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader
from psycopg.types.json import Jsonb

from .database import get_connection

BOP_URL = "https://bop.dival.es/bop/"
DOWNLOAD_URL = "https://bop.dival.es/bop/downloads"
ORGANISMO_ID = 2
FUENTE_ID = 2
INCLUIDOS = (
    "convocatoria",
    "proceso selectivo",
    "selección",
    "seleccion",
    "oposición",
    "oposicion",
    "bolsa de trabajo",
    "bolsa de empleo",
)
EXCLUIDOS = (
    "provisión del puesto",
    "provision del puesto",
    "provisión de puestos",
    "provision de puestos",
    "provisión del lugar",
    "provision del lloc",
    "libre designación",
    "libre designacion",
    "nomenamiento",
    "nomenament",
)


def _norm(s: str) -> str:
    return " ".join(s.replace("\xa0", " ").split())


def _sin(s: str) -> str:
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


def _fecha(s: str | None) -> date | None:
    if not s:
        return None
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _convocatoria(s: str) -> str | None:
    m = re.search(r"convocatoria\s+([A-Z]?\s*\d{1,3}/\d{2,4})", _sin(s))
    return re.sub(r"\s+", "", m.group(1)).upper() if m else None


def _anio_convocatoria(s: str) -> int | None:
    convocatoria = _convocatoria(s)
    if convocatoria:
        m = re.search(r"/(\d{2,4})$", convocatoria)
        if m:
            n = int(m.group(1))
            return 2000 + n if n < 100 else n
    return None


def _plazas(s: str) -> int | None:
    patrones = (
        r"selecci[oó]n de\s+(\d+)\s+plazas?",
        r"selecci[oó]n de una plaza",
        r"convocatoria de\s+(\d+)\s+plazas?",
    )
    for patron in patrones:
        m = re.search(patron, s, re.I)
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


def _extraer_metadatos(a: Any) -> tuple[str | None, date | None]:
    texto = ""
    cont = a
    for _ in range(20):
        cont = cont.parent
        if cont is None:
            break
        texto = _norm(cont.get_text(" ", strip=True))
        if re.search(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*\d{4}/\d{5}", texto, re.I):
            break

    m = re.search(
        r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d{5})",
        texto,
        re.I,
    )
    fecha_m = re.search(
        r"(?:Data publicaci[oó]|Fecha publicaci[oó]n)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})",
        texto,
        re.I,
    )
    fecha = _fecha(fecha_m.group(1)) if fecha_m else _fecha(texto)

    if m:
        return m.group(1), fecha

    # Fallback: en el HTML de PrimeFaces, el enlace del anuncio tiene href="#"
    # y los metadatos aparecen como nodos posteriores al enlace.
    partes: list[str] = []
    for nodo in a.next_elements:
        if getattr(nodo, "name", None) == "a" and nodo is not a:
            break
        if isinstance(nodo, str):
            partes.append(nodo)
        if len(" ".join(partes)) > 1200:
            break
    siguiente = _norm(" ".join(partes))
    m = re.search(
        r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d{5})",
        siguiente,
        re.I,
    )
    fecha_m = re.search(
        r"(?:Data publicaci[oó]|Fecha publicaci[oó]n)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})",
        siguiente,
        re.I,
    )
    fecha = _fecha(fecha_m.group(1)) if fecha_m else fecha
    return (m.group(1) if m else None), fecha


def descubrir_anuncios(client: httpx.Client) -> list[dict[str, Any]]:
    r = client.get(BOP_URL)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()

    for a in soup.find_all("a", href=True):
        titulo = _norm(a.get_text(" ", strip=True))
        href = str(a.get("href"))
        if not titulo or href in vistos or not _incluido(titulo):
            continue

        # Solo la Diputación Provincial, no Consorcios ni ayuntamientos.
        if "diputaci[oó]n provincial de val[eè]ncia" not in _sin(titulo):
            continue

        registro, fecha = _extraer_metadatos(a)
        if not registro:
            continue

        url = f"{DOWNLOAD_URL}?anuncioCSV={registro}&lang=es"
        vistos.add(href + registro)
        resultados.append(
            {
                "titulo": titulo,
                "url": url,
                "registro": registro,
                "fecha_publicacion": fecha,
            }
        )

    return resultados


def _obtener_texto(client: httpx.Client, url: str) -> str:
    r = client.get(url)
    r.raise_for_status()
    content_type = r.headers.get("content-type", "").lower()
    if "pdf" in content_type or r.content.startswith(b"%PDF"):
        reader = PdfReader(BytesIO(r.content))
        paginas = [(page.extract_text() or "") for page in reader.pages]
        return _norm(" ".join(paginas))
    return _norm(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))


def _identificador_estable(titulo: str, texto: str, registro: str) -> str:
    convocatoria = _convocatoria(titulo + " " + texto)
    if convocatoria:
        return f"DVAL:{convocatoria}"
    # Si el anuncio no contiene número de convocatoria, mantenemos una identidad
    # estable basada en el título, no en el número de publicación BOP.
    base = _sin(titulo)
    return "DVAL:T:" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def importar_bop_valencia() -> dict[str, Any]:
    stats: dict[str, Any] = {
        "descubiertos": 0,
        "procesos": 0,
        "publicaciones": 0,
        "cambios": 0,
        "anuncios": [],
    }
    headers = {
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        anuncios = descubrir_anuncios(client)
        stats["descubiertos"] = len(anuncios)
        with get_connection() as connection:
            with connection.cursor() as cursor:
                for anuncio in anuncios:
                    texto = _obtener_texto(client, anuncio["url"])
                    # El alcance es por fecha de publicación: procesos activos en
                    # 2026/2027 pueden conservar una convocatoria de años anteriores.
                    fecha = anuncio["fecha_publicacion"]
                    if fecha and fecha.year not in {2026, 2027}:
                        continue

                    titulo = anuncio["titulo"]
                    registro = anuncio["registro"]
                    estable = _identificador_estable(titulo, texto, registro)
                    anio = _anio_convocatoria(titulo + " " + texto)
                    ultima = (
                        datetime.combine(fecha, datetime.min.time(), tzinfo=timezone.utc)
                        if fecha
                        else None
                    )
                    valores = (
                        titulo,
                        _grupo(titulo + " " + texto),
                        _tipo(titulo),
                        _turno(titulo + " " + texto),
                        _plazas(titulo),
                        "EN_SEGUIMIENTO",
                        anio,
                    )
                    cursor.execute(
                        "SELECT id, denominacion, grupo, tipo_proceso, turno, plazas, estado, anio_convocatoria "
                        "FROM procesos WHERE identificador_estable=%s",
                        (estable,),
                    )
                    existente = cursor.fetchone()
                    campos = (
                        "denominacion",
                        "grupo",
                        "tipo_proceso",
                        "turno",
                        "plazas",
                        "estado",
                        "anio_convocatoria",
                    )
                    if existente:
                        proceso_id = existente[0]
                        for i, campo in enumerate(campos, 1):
                            if existente[i] != valores[i - 1]:
                                cursor.execute(
                                    "INSERT INTO cambios "
                                    "(proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen) "
                                    "VALUES (%s,%s,%s,%s,%s,%s)",
                                    (
                                        proceso_id,
                                        "ACTUALIZACION",
                                        campo,
                                        str(existente[i]) if existente[i] is not None else None,
                                        str(valores[i - 1]) if valores[i - 1] is not None else None,
                                        f"Cambio en {campo}: {existente[i]!r} -> {valores[i - 1]!r}",
                                    ),
                                )
                                stats["cambios"] += 1
                        cursor.execute(
                            "UPDATE procesos SET denominacion=%s,grupo=%s,tipo_proceso=%s,turno=%s,plazas=%s,"
                            "estado=%s,anio_convocatoria=%s,fecha_convocatoria=COALESCE(%s,fecha_convocatoria),"
                            "ultima_publicacion_at=COALESCE(%s,ultima_publicacion_at),fuente_principal_id=%s,"
                            "datos_json=%s,updated_at=NOW() WHERE id=%s",
                            (
                                *valores,
                                fecha,
                                ultima,
                                FUENTE_ID,
                                Jsonb({"registro": registro, "url": anuncio["url"]}),
                                proceso_id,
                            ),
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO procesos "
                            "(organismo_id,codigo_externo,identificador_estable,denominacion,grupo,tipo_proceso,turno,plazas,"
                            "estado,anio_convocatoria,fecha_convocatoria,ultima_publicacion_at,fuente_principal_id,datos_json) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                            (
                                ORGANISMO_ID,
                                registro,
                                estable,
                                *valores,
                                fecha,
                                ultima,
                                FUENTE_ID,
                                Jsonb({"registro": registro, "url": anuncio["url"]}),
                            ),
                        )
                        proceso_id = cursor.fetchone()[0]
                        stats["procesos"] += 1

                    contenido_hash = hashlib.sha256(texto.encode("utf-8")).hexdigest()
                    cursor.execute(
                        "SELECT 1 FROM publicaciones WHERE proceso_id=%s AND referencia=%s LIMIT 1",
                        (proceso_id, registro),
                    )
                    if cursor.fetchone() is None:
                        cursor.execute(
                            "INSERT INTO publicaciones "
                            "(proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,contenido_hash,contenido_texto,datos_json) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                proceso_id,
                                FUENTE_ID,
                                registro,
                                "BOP",
                                titulo,
                                fecha,
                                anuncio["url"],
                                contenido_hash,
                                texto,
                                Jsonb({"registro": registro, "url": anuncio["url"]}),
                            ),
                        )
                        stats["publicaciones"] += 1

                    stats["anuncios"].append(
                        {
                            "registro": registro,
                            "titulo": titulo,
                            "fecha_publicacion": fecha.isoformat() if fecha else None,
                            "proceso_id": proceso_id,
                            "identificador_estable": estable,
                        }
                    )
            connection.commit()
    return stats
