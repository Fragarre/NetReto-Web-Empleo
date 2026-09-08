from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from . import bop_valencia as _bop
from . import bop_valencia_patch as _bop_patch
from .ambito_administrativo import clasificar_ambito_administrativo
from .database import get_connection


def _sin(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def _municipio_desde_titulo(titulo: str) -> str | None:
    n = _sin(titulo)
    patrones = (
        r"ayuntamiento de\s+([^.,;:]+)",
        r"ajuntament de\s+([^.,;:]+)",
        r"ayuntamiento del\s+([^.,;:]+)",
        r"ajuntament del\s+([^.,;:]+)",
    )
    for patron in patrones:
        m = re.search(patron, n, re.I)
        if m:
            return " ".join(m.group(1).split()).strip()[:120]
    return None


def _es_empleo_administrativo(titulo: str) -> bool:
    n = _sin(titulo)
    exclusiones = (
        "subvencion", "subvencio", "premio", "premi", "ayuda", "ajuda",
        "libre designacion", "provision de puesto", "provisio de lloc",
        "promocion interna", "promocio interna",
    )
    if any(x in n for x in exclusiones):
        return False
    ambito = clasificar_ambito_administrativo({"denominacion": titulo, "cuerpo_escala": None, "grupo": None})
    return ambito == "SI"


def _extraer_anuncios_municipales(html: str) -> list[dict[str, Any]]:
    texto = _bop._norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    registro_patron = re.compile(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d+)", re.I)
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for mr in registro_patron.finditer(texto):
        numero = mr.group(1)
        inicio = texto.rfind("Anunci", 0, mr.start())
        if inicio < 0:
            inicio = texto.rfind("Anuncio", 0, mr.start())
        if inicio < 0:
            continue
        titulo = _bop._norm(texto[inicio:mr.start()]).rstrip(".") + "."
        municipio = _municipio_desde_titulo(titulo)
        if not municipio or numero in vistos:
            continue
        vistos.add(numero)
        contexto = texto[inicio:mr.end() + 150]
        fm = re.search(r"(?:Data publicaci[oó]|Fecha publicaci[oó]n)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", contexto, re.I)
        fecha = _bop._fecha(fm.group(1)) if fm else None
        resultados.append({
            "titulo": titulo,
            "url": f"{_bop.DOWNLOAD_URL}?anuncioNumReg={quote(numero)}&lang=es",
            "registro": numero,
            "fecha_publicacion": fecha,
            "municipio": municipio,
        })
    return resultados


def descubrir_municipales_bop(*, hasta: date | None = None, dias: int = 30) -> dict[str, Any]:
    hasta = hasta or date.today()
    desde = hasta - timedelta(days=max(0, dias - 1))
    headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
    hallazgos: list[dict[str, Any]] = []
    vistos: set[str] = set()
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        fecha = desde
        while fecha <= hasta:
            _, html, _ = _bop_patch._obtener_pagina(client, fecha)
            if html:
                for anuncio in _extraer_anuncios_municipales(html):
                    titulo = anuncio["titulo"]
                    if not _es_empleo_administrativo(titulo):
                        continue
                    registro = anuncio["registro"]
                    if registro in vistos:
                        continue
                    vistos.add(registro)
                    hallazgos.append({
                        "registro": registro,
                        "fecha_publicacion": anuncio["fecha_publicacion"].isoformat() if anuncio["fecha_publicacion"] else None,
                        "municipio_detectado": anuncio["municipio"],
                        "titulo": titulo,
                        "url": anuncio["url"],
                    })
            fecha += timedelta(days=1)
    hallazgos.sort(key=lambda x: (x["fecha_publicacion"] or "", x["registro"]))
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "descubiertos": len(hallazgos), "hallazgos": hallazgos}


def listar_municipios_detectados() -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id,nombre,municipio,activo FROM organismos WHERE tipo='AYUNTAMIENTO' ORDER BY municipio NULLS LAST,nombre")
        rows = cursor.fetchall()
    return [{"id": r[0], "nombre": r[1], "municipio": r[2], "activo": r[3]} for r in rows]
