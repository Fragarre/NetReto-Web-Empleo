from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import boe_local_import as _boe_local
from . import bop_valencia_municipios as _municipios
from . import bop_valencia_patch as _bop_patch
from .database import get_connection

_BASE_PREVISUALIZAR = _boe_local.previsualizar_importacion_boe_local

_STOPWORDS = {
    "anunci", "anuncio", "ajuntament", "ayuntamiento", "mancomunitat", "mancomunidad",
    "aprovacio", "aprobacion", "aprovar", "aprobar", "bases", "convocatoria", "proces",
    "proceso", "selectiu", "selectivo", "seleccion", "cobertura", "propietat", "propiedad",
    "placa", "places", "plaza", "plazas", "personal", "turno", "torn", "libre", "lliure",
    "administracio", "administracion", "general", "escala", "subescala", "sistema",
    "concurs", "concurso", "oposicio", "oposicion", "mitjancant", "mediante", "sobre",
}


def _sin(texto: str | None) -> str:
    valor = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in valor if unicodedata.category(c) != "Mn")


def _entidad_objetivo(organismo: dict[str, Any]) -> str | None:
    municipio = organismo.get("municipio")
    if municipio:
        return _sin(str(municipio)).strip()

    nombre = _sin(str(organismo.get("nombre") or "")).strip()
    prefijos = (
        "ayuntamiento de ", "ajuntament de ",
        "mancomunidad de ", "mancomunitat de ",
    )
    for prefijo in prefijos:
        if nombre.startswith(prefijo):
            return nombre[len(prefijo):].strip() or None
    return nombre or None


def _palabras(texto: str | None) -> set[str]:
    return {
        p for p in re.findall(r"[a-z0-9]+", _sin(texto))
        if len(p) >= 4 and p not in _STOPWORDS
    }


def _puntuacion(denominacion: str | None, titulo: str | None) -> tuple[int, int]:
    palabras_denominacion = _palabras(denominacion)
    palabras_titulo = _palabras(titulo)
    comunes = len(palabras_denominacion & palabras_titulo)
    extras = len(palabras_titulo - palabras_denominacion)
    return comunes, -extras


def _titulo_pertenece_entidad(titulo: str | None, entidad: str) -> bool:
    nt = _sin(titulo)
    ne = _sin(entidad).strip()
    if not ne:
        return False
    if ne in nt:
        return True
    tokens = [p for p in re.findall(r"[a-z0-9]+", ne) if len(p) >= 3]
    return bool(tokens) and all(p in nt for p in tokens)


def _candidatos_entidad(
    anuncios: list[dict[str, Any]],
    *,
    organismo: dict[str, Any],
) -> list[dict[str, Any]]:
    entidad = _entidad_objetivo(organismo)
    if not entidad:
        return []
    candidatos = []
    for anuncio in anuncios:
        municipio_anuncio = _sin(anuncio.get("municipio") or "").strip()
        if municipio_anuncio and municipio_anuncio == entidad:
            candidatos.append(anuncio)
            continue
        if _titulo_pertenece_entidad(anuncio.get("titulo"), entidad):
            candidatos.append(anuncio)
    unicos: dict[str, dict[str, Any]] = {}
    for anuncio in candidatos:
        clave = str(anuncio.get("registro") or anuncio.get("url") or anuncio.get("titulo"))
        unicos[clave] = anuncio
    return list(unicos.values())


def _elegir_anuncio(
    anuncios: list[dict[str, Any]],
    *,
    organismo: dict[str, Any],
    denominacion: str | None,
) -> dict[str, Any] | None:
    candidatos = _candidatos_entidad(anuncios, organismo=organismo)
    if not candidatos:
        return None

    nuevas = [
        a for a in candidatos
        if _municipios._clasificar_anuncio(a.get("titulo") or "") == "NUEVA_CONVOCATORIA"
    ]
    if nuevas:
        candidatos = nuevas

    if len(candidatos) == 1:
        return candidatos[0]

    puntuados = sorted(
        ((_puntuacion(denominacion, a.get("titulo")), a) for a in candidatos),
        key=lambda x: x[0],
        reverse=True,
    )
    if not puntuados or puntuados[0][0][0] <= 0:
        return None
    if len(puntuados) > 1 and puntuados[1][0] == puntuados[0][0]:
        return None
    return puntuados[0][1]


def _diagnostico_candidatos(
    anuncios: list[dict[str, Any]],
    *,
    organismo: dict[str, Any],
    denominacion: str | None,
) -> dict[str, Any]:
    entidad = _entidad_objetivo(organismo)
    candidatos = _candidatos_entidad(anuncios, organismo=organismo)
    tokens_entidad = {
        p for p in re.findall(r"[a-z0-9]+", _sin(entidad or ""))
        if len(p) >= 4
    }
    cercanos = []
    for anuncio in anuncios:
        titulo = anuncio.get("titulo") or ""
        nt = _sin(titulo)
        if tokens_entidad and any(token in nt for token in tokens_entidad):
            cercanos.append(anuncio)
    def resumir(a: dict[str, Any]) -> dict[str, Any]:
        return {
            "registro": a.get("registro"),
            "municipio": a.get("municipio"),
            "clase": _municipios._clasificar_anuncio(a.get("titulo") or ""),
            "puntuacion": list(_puntuacion(denominacion, a.get("titulo"))),
            "titulo": (a.get("titulo") or "")[:500],
        }
    return {
        "entidad_objetivo": entidad,
        "total_anuncios_fecha": len(anuncios),
        "coincidencias_entidad": [resumir(a) for a in candidatos[:12]],
        "anuncios_cercanos": [resumir(a) for a in cercanos[:12]],
    }


def _extraer_anuncios_genericos(html: str) -> list[dict[str, Any]]:
    """Extrae anuncios del índice diario aunque no sean AYUNTAMIENTO.

    Se usa solo como fallback para resolver una fecha BOP ya proporcionada por el BOE.
    """
    texto = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    texto = " ".join(texto.split())
    patron = re.compile(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d+)", re.I)
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for mr in patron.finditer(texto):
        registro = mr.group(1)
        if registro in vistos:
            continue
        inicio = max(texto.rfind("Anunci", 0, mr.start()), texto.rfind("Anuncio", 0, mr.start()))
        if inicio < 0:
            continue
        titulo = " ".join(texto[inicio:mr.start()].split()).rstrip(".") + "."
        if len(titulo) > 1000:
            continue
        vistos.add(registro)
        resultados.append({
            "titulo": titulo,
            "url": f"https://bop.dival.es/bop/downloads?anuncioNumReg={quote(registro)}&lang=es",
            "registro": registro,
            "fecha_publicacion": None,
            "municipio": None,
        })
    return resultados


def _leer_anuncios_fecha(client: httpx.Client, fecha: date) -> tuple[list[dict[str, Any]], str | None]:
    try:
        _, html, error = _bop_patch._obtener_pagina(client, fecha)
    except Exception as exc:
        return [], f"{type(exc).__name__}: {str(exc)[:180]}"
    if error:
        return [], str(error)[:180]
    if not html:
        return [], "BOP sin contenido para la fecha indicada"
    try:
        municipales = _municipios._extraer_anuncios_municipales(html)
        genericos = _extraer_anuncios_genericos(html)
        unicos: dict[str, dict[str, Any]] = {}
        for anuncio in [*municipales, *genericos]:
            clave = str(anuncio.get("registro") or anuncio.get("url") or anuncio.get("titulo"))
            unicos[clave] = anuncio
        return list(unicos.values()), None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {str(exc)[:180]}"


def reconciliar_bases_bop_boe_local(*, aplicar: bool = False) -> dict[str, Any]:
    """Resuelve el PDF BOP de bases de procesos BOELOCAL que aún enlazan solo al BOE.

    El BOE aporta fecha/número del BOP. Se consulta exclusivamente ese día y solo se
    acepta una coincidencia inequívoca de la misma entidad local. En caso de duda no se
    modifica nada.
    """
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id,p.identificador_estable,p.denominacion,p.datos_json,
                   o.nombre,o.municipio,o.tipo
            FROM procesos p
            JOIN organismos o ON o.id=p.organismo_id
            WHERE p.identificador_estable LIKE 'BOELOCAL:%%'
              AND p.datos_json->'bases_bop'->>'fecha' IS NOT NULL
              AND NULLIF(TRIM(COALESCE(p.datos_json->>'url_oficial','')), '') IS NULL
            ORDER BY p.id
            """
        )
        procesos = list(cursor.fetchall())

    stats: dict[str, Any] = {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "pendientes": len(procesos),
        "resueltos": 0,
        "actualizados": 0,
        "ambiguos_o_no_encontrados": 0,
        "errores": [],
        "detalle": [],
    }
    if not procesos:
        return stats

    headers = {
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    cache: dict[date, tuple[list[dict[str, Any]], str | None]] = {}

    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        for proceso in procesos:
            organismo = {
                "nombre": proceso.get("nombre"),
                "municipio": proceso.get("municipio"),
                "tipo": proceso.get("tipo"),
            }

            bases = (proceso.get("datos_json") or {}).get("bases_bop") or {}
            try:
                fecha = date.fromisoformat(str(bases.get("fecha")))
            except Exception:
                stats["ambiguos_o_no_encontrados"] += 1
                stats["detalle"].append({
                    "proceso_id": proceso["id"],
                    "identificador_estable": proceso["identificador_estable"],
                    "estado": "FECHA_BOP_INVALIDA",
                })
                continue

            if fecha not in cache:
                cache[fecha] = _leer_anuncios_fecha(client, fecha)
            anuncios, error = cache[fecha]
            if error:
                stats["errores"].append({"fecha": fecha.isoformat(), "error": error})
                stats["ambiguos_o_no_encontrados"] += 1
                continue

            anuncio = _elegir_anuncio(
                anuncios,
                organismo=organismo,
                denominacion=proceso.get("denominacion"),
            )
            if anuncio is None:
                stats["ambiguos_o_no_encontrados"] += 1
                stats["detalle"].append({
                    "proceso_id": proceso["id"],
                    "identificador_estable": proceso["identificador_estable"],
                    "estado": "SIN_COINCIDENCIA_INEQUIVOCA",
                    "fecha_bop": fecha.isoformat(),
                    "diagnostico": _diagnostico_candidatos(
                        anuncios,
                        organismo=organismo,
                        denominacion=proceso.get("denominacion"),
                    ),
                })
                continue

            stats["resueltos"] += 1
            item = {
                "proceso_id": proceso["id"],
                "identificador_estable": proceso["identificador_estable"],
                "estado": "RESUELTO",
                "fecha_bop": fecha.isoformat(),
                "referencia_bop": anuncio.get("registro"),
                "url_bases": anuncio.get("url"),
                "titulo_bop": anuncio.get("titulo"),
            }
            stats["detalle"].append(item)

            if aplicar:
                with get_connection() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE procesos
                        SET datos_json = COALESCE(datos_json,'{}'::jsonb) || %s,
                            updated_at = NOW()
                        WHERE id=%s
                          AND NULLIF(TRIM(COALESCE(datos_json->>'url_oficial','')), '') IS NULL
                        """,
                        (
                            Jsonb({
                                "url_oficial": anuncio.get("url"),
                                "bases_bop_resueltas": {
                                    "referencia": anuncio.get("registro"),
                                    "fecha": fecha.isoformat(),
                                    "titulo": anuncio.get("titulo"),
                                },
                            }),
                            proceso["id"],
                        ),
                    )
                    if cursor.rowcount:
                        stats["actualizados"] += 1
                    connection.commit()

    return stats


def _previsualizar_con_bases_bop(*, hasta: date, dias: int = 30, aplicar: bool = False) -> dict[str, Any]:
    resultado = _BASE_PREVISUALIZAR(hasta=hasta, dias=dias, aplicar=aplicar)
    resultado["bases_bop"] = reconciliar_bases_bop_boe_local(aplicar=aplicar)
    return resultado


def aplicar_resolucion_bases_bop() -> None:
    _boe_local.previsualizar_importacion_boe_local = _previsualizar_con_bases_bop
