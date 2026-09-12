from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

import httpx
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import boe_local_import as _boe_local
from . import bop_valencia_municipios as _municipios
from . import bop_valencia_patch as _bop_patch
from .database import get_connection

_BASE_PREVISUALIZAR = _boe_local.previsualizar_importacion_boe_local

_STOPWORDS = {
    "anunci", "anuncio", "ajuntament", "ayuntamiento", "aprovacio", "aprobacion",
    "bases", "convocatoria", "proces", "proceso", "selectiu", "selectivo", "seleccion",
    "placa", "places", "plaza", "plazas", "personal", "turno", "torn", "libre", "lliure",
    "administracio", "administracion", "general", "escala", "subescala", "sistema",
}


def _sin(texto: str | None) -> str:
    valor = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in valor if unicodedata.category(c) != "Mn")


def _municipio_objetivo(organismo: dict[str, Any]) -> str | None:
    municipio = organismo.get("municipio")
    if municipio:
        return _sin(str(municipio)).strip()
    nombre = _sin(str(organismo.get("nombre") or "")).strip()
    for prefijo in ("ayuntamiento de ", "ajuntament de "):
        if nombre.startswith(prefijo):
            return nombre[len(prefijo):].strip() or None
    return None


def _palabras(texto: str | None) -> set[str]:
    return {
        p for p in re.findall(r"[a-z0-9]+", _sin(texto))
        if len(p) >= 4 and p not in _STOPWORDS
    }


def _score(denominacion: str | None, titulo: str | None) -> int:
    return len(_palabras(denominacion) & _palabras(titulo))


def _elegir_anuncio(
    anuncios: list[dict[str, Any]],
    *,
    organismo: dict[str, Any],
    denominacion: str | None,
) -> dict[str, Any] | None:
    municipio = _municipio_objetivo(organismo)
    if not municipio:
        return None

    candidatos = [
        a for a in anuncios
        if _sin(a.get("municipio") or "").strip() == municipio
    ]
    if not candidatos:
        return None

    nuevas = [a for a in candidatos if _municipios._clasificar_anuncio(a.get("titulo") or "") == "NUEVA_CONVOCATORIA"]
    if nuevas:
        candidatos = nuevas

    if len(candidatos) == 1:
        return candidatos[0]

    puntuados = sorted(
        ((_score(denominacion, a.get("titulo")), a) for a in candidatos),
        key=lambda x: x[0],
        reverse=True,
    )
    if not puntuados or puntuados[0][0] <= 0:
        return None
    if len(puntuados) > 1 and puntuados[1][0] == puntuados[0][0]:
        return None
    return puntuados[0][1]


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
        return _municipios._extraer_anuncios_municipales(html), None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {str(exc)[:180]}"


def reconciliar_bases_bop_boe_local(*, aplicar: bool = False) -> dict[str, Any]:
    """Resuelve el PDF BOP de bases de procesos BOELOCAL que aún enlazan solo al BOE.

    El BOE aporta fecha/número del BOP. Se consulta exclusivamente ese día y solo se
    acepta una coincidencia inequívoca del mismo ayuntamiento. En caso de duda no se
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
            if organismo.get("tipo") != "AYUNTAMIENTO":
                stats["ambiguos_o_no_encontrados"] += 1
                stats["detalle"].append({
                    "proceso_id": proceso["id"],
                    "identificador_estable": proceso["identificador_estable"],
                    "estado": "NO_AYUNTAMIENTO",
                })
                continue

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
