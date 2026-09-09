from __future__ import annotations

import re
from collections import Counter
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


def _reparar_mojibake_utf8(texto: str) -> str:
    """Repara UTF-8 interpretado erróneamente como latin-1/cp1252.

    Se aplica únicamente cuando aparecen marcadores típicos de mojibake.
    Si la reconversión no es válida, devuelve el texto original.
    """
    if not texto or not any(m in texto for m in ("Ã", "Â", "â€", "â€™", "â€œ", "â€")):
        return texto
    for encoding in ("latin-1", "cp1252"):
        try:
            reparado = texto.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if reparado.count("Ã") + reparado.count("Â") < texto.count("Ã") + texto.count("Â"):
            return reparado
    return texto


def _es_emisor_ayuntamiento(titulo: str) -> bool:
    """Evita falsos positivos donde Diputación cita a un ayuntamiento."""
    n = _sin(titulo).strip()
    return bool(
        re.match(
            r"^(?:anunci|anuncio)\s+(?:de\s+)?(?:l['’]\s*)?(?:ayuntamiento|ajuntament)\s+(?:de|del|d['’])\s+",
            n,
            re.I,
        )
    )


def _municipio_desde_titulo(titulo: str) -> str | None:
    if not _es_emisor_ayuntamiento(titulo):
        return None
    n = _sin(titulo)
    patrones = (
        r"(?:ayuntamiento|ajuntament)\s+de\s+(.+?)(?=\s+sobre\b|[.,;:]|$)",
        r"(?:ayuntamiento|ajuntament)\s+del\s+(.+?)(?=\s+sobre\b|[.,;:]|$)",
        r"(?:ayuntamiento|ajuntament)\s+d['’]\s*(.+?)(?=\s+sobre\b|[.,;:]|$)",
    )
    for patron in patrones:
        m = re.search(patron, n, re.I)
        if m:
            return " ".join(m.group(1).split()).strip()[:120]
    return None


def _es_perfil_administrativo(titulo: str) -> bool:
    ambito = clasificar_ambito_administrativo(
        {"denominacion": titulo, "cuerpo_escala": None, "grupo": None}
    )
    return ambito == "SI"


def _clasificar_anuncio(titulo: str) -> str:
    """Clasifica el papel del anuncio dentro del proceso selectivo.

    NUEVA_CONVOCATORIA: bases/convocatoria de una plaza administrativa.
    BOLSA_TEMPORAL: bolsa/interinidad; se conserva para decidir política aparte.
    SEGUIMIENTO: admitidos, tribunal, examen, nombramiento, correcciones, etc.
    EXCLUIDO_INTERNO: provisión/comisión/traslado/promoción interna.
    RUIDO: no es una oportunidad de empleo administrativo utilizable.
    """
    n = _sin(titulo)

    internos = (
        "libre designacion", "lliure designacio",
        "comision de servicios", "comissio de serveis",
        "concurso de traslados", "concurs de trasllats",
        "concurso especifico de meritos", "concurs especific de merits",
        "provision de puesto", "provisio de lloc",
        "provision del puesto", "provisio del lloc",
        "abierto a otras administraciones publicas", "obert a altres administracions publiques",
        "promocion interna", "promocio interna",
        "cesion de la bolsa", "cessio de la borsa",
        "conveni de collaboracio", "convenio de colaboracion",
    )
    if any(x in n for x in internos):
        return "EXCLUIDO_INTERNO"

    ruido = (
        "subvencion", "subvencio", "premio", "premi", "ayuda", "ajuda",
        "ordenanza fiscal", "ordenanca fiscal", "tasa", "taxa",
        "gestion tributaria", "gestio tributaria", "recaptacio", "recaudacion",
    )
    if any(x in n for x in ruido) and not any(
        x in n for x in ("administratiu", "administrativo", "auxiliar administratiu", "auxiliar administrativo", "tecnic d'administracio", "tecnico de administracion")
    ):
        return "RUIDO"

    bolsas = (
        "borsa d'ocupacio", "borsa de treball", "bolsa de empleo", "bolsa de trabajo",
        "funcionari interi", "funcionario interino", "funcionaria interina", "nomenament interi",
    )
    if any(x in n for x in bolsas):
        return "BOLSA_TEMPORAL"

    seguimiento = (
        "relacio provisional", "relacion provisional", "relacio definitiva", "relacion definitiva",
        "admeses", "admesos", "admitidos", "admitidas", "exclosos", "excloses", "excluidos", "excluidas",
        "tribunal", "organ tecnic de seleccio", "organo tecnico de seleccion",
        "primer exercici", "primer ejercicio", "data de l'exercici", "fecha del ejercicio",
        "nomenament", "nombramiento", "persona aprovada", "persones aprovades",
        "resultats", "resultados", "proposta de nomenament", "propuesta de nombramiento",
        "correccio d'errors", "correccion de errores", "modificacio", "modificacion",
        "resolucio de recursos", "resolucion de recursos", "acumulacio de places", "acumulacion de plazas",
    )
    if any(x in n for x in seguimiento):
        return "SEGUIMIENTO"

    bases = (
        "aprovacio de les bases", "aprobacion de las bases",
        "bases de la convocatoria", "bases de la convocatoria",
        "bases i la convocatoria", "bases y la convocatoria",
        "bases reguladores del procediment selectiu", "bases reguladoras del procedimiento selectivo",
    )
    if any(x in n for x in bases):
        return "NUEVA_CONVOCATORIA"

    return "SEGUIMIENTO"


def _es_empleo_administrativo(titulo: str) -> bool:
    return _es_emisor_ayuntamiento(titulo) and _es_perfil_administrativo(titulo)


def _extraer_anuncios_municipales(html: str) -> list[dict[str, Any]]:
    html = _reparar_mojibake_utf8(html)
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
    headers = {
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
        "Accept-Language": "es-ES,es;q=0.9",
    }
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
                    clase = _clasificar_anuncio(titulo)
                    hallazgos.append({
                        "registro": registro,
                        "fecha_publicacion": anuncio["fecha_publicacion"].isoformat() if anuncio["fecha_publicacion"] else None,
                        "municipio_detectado": anuncio["municipio"],
                        "clase": clase,
                        "titulo": titulo,
                        "url": anuncio["url"],
                    })
            fecha += timedelta(days=1)

    hallazgos.sort(key=lambda x: (x["fecha_publicacion"] or "", x["registro"]))
    conteo = Counter(h["clase"] for h in hallazgos)
    candidatas = [h for h in hallazgos if h["clase"] == "NUEVA_CONVOCATORIA"]
    return {
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "descubiertos": len(hallazgos),
        "resumen_clases": dict(sorted(conteo.items())),
        "candidatas_nuevas": len(candidatas),
        "hallazgos": hallazgos,
    }


def listar_municipios_detectados() -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id,nombre,municipio,activo FROM organismos WHERE tipo='AYUNTAMIENTO' ORDER BY municipio NULLS LAST,nombre"
        )
        rows = cursor.fetchall()
    return [{"id": r[0], "nombre": r[1], "municipio": r[2], "activo": r[3]} for r in rows]
