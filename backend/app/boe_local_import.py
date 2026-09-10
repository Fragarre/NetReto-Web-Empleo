from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from .boe_local_extractor import extraer_convocatorias_boe_local
from .database import get_connection


def _sin(texto: str | None) -> str:
    valor = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in valor if unicodedata.category(c) != "Mn")


def _entidad_visible(entidad: str | None) -> str | None:
    if not entidad:
        return None
    return re.sub(r"\s*\([^)]*\)\s*$", "", entidad).strip() or None


def _familia(denominacion: str | None) -> str | None:
    n = _sin(denominacion)
    if "auxiliar administr" in n:
        return "AUXILIAR_ADMINISTRATIVO"
    if "tecnico" in n and "administracion general" in n:
        return "TAG"
    if "tecnica" in n and "administracion general" in n:
        return "TAG"
    if "administrativo" in n or "administrativa" in n:
        return "ADMINISTRATIVO"
    if "tecnico" in n and "gestion" in n:
        return "TECNICO_GESTION"
    if "tecnica" in n and "gestion" in n:
        return "TECNICO_GESTION"
    return None


def _buscar_organismo(organismos: list[dict[str, Any]], entidad: str | None) -> dict[str, Any] | None:
    visible = _entidad_visible(entidad)
    if not visible:
        return None
    objetivo = _sin(visible)
    exactos = [o for o in organismos if _sin(o.get("nombre")) == objetivo]
    return exactos[0] if len(exactos) == 1 else None


def _candidatos_bop(
    cursor,
    *,
    organismo_id: int,
    fecha_bases: str | None,
    denominacion: str | None,
    plazas: int | None,
) -> list[dict[str, Any]]:
    if not fecha_bases:
        return []
    cursor.execute(
        """
        SELECT id,identificador_estable,codigo_externo,denominacion,plazas,fecha_convocatoria,datos_json
        FROM procesos
        WHERE organismo_id=%s
          AND ambito_administrativo='SI'
          AND fecha_convocatoria=%s
        ORDER BY id
        """,
        (organismo_id, fecha_bases),
    )
    familia = _familia(denominacion)
    resultado = []
    for proceso in cursor.fetchall():
        if familia and _familia(proceso.get("denominacion")) != familia:
            continue
        if plazas is not None and proceso.get("plazas") is not None and proceso.get("plazas") != plazas:
            continue
        resultado.append(proceso)
    return resultado


def _es_turno_interno(turno: str | None) -> bool:
    return "promocion interna" in _sin(turno)


def previsualizar_importacion_boe_local(*, hasta: date, dias: int = 30) -> dict[str, Any]:
    """SOLO LECTURA. Adapta el extractor BOE a la estructura real de NetReto sin escribir."""
    extraccion = extraer_convocatorias_boe_local(hasta=hasta, dias=dias)
    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION",
        "desde": extraccion["desde"],
        "hasta": extraccion["hasta"],
        "convocatorias_extraidas": extraccion["convocatorias"],
        "dias_con_error": extraccion["dias_con_error"],
        "errores": extraccion["errores"],
        "fuera_alcance_provincia": 0,
        "excluidas_turno_interno": 0,
        "nuevas": 0,
        "existentes_boe": 0,
        "posibles_existentes_bop": 0,
        "revision_solapamiento": 0,
        "detalle": [],
    }

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id,nombre,tipo,provincia,municipio FROM organismos ORDER BY id")
        organismos = cursor.fetchall()
        cursor.execute("SELECT id FROM fuentes WHERE id=9 AND tipo='BOE'")
        if cursor.fetchone() is None:
            raise RuntimeError("No existe la fuente BOE esperada (id=9, tipo=BOE)")

        for convocatoria in extraccion["detalle"]:
            if convocatoria.get("provincia") != "Valencia":
                resultado["fuera_alcance_provincia"] += 1
                continue

            codigo = convocatoria["codigo_externo"]
            estable = f"BOELOCAL:{codigo}"

            if _es_turno_interno(convocatoria.get("turno")):
                resultado["excluidas_turno_interno"] += 1
                resultado["detalle"].append({
                    "codigo_externo": codigo,
                    "identificador_estable": estable,
                    "entidad": convocatoria.get("entidad"),
                    "denominacion": convocatoria.get("denominacion"),
                    "plazas": convocatoria.get("plazas"),
                    "sistema_selectivo": convocatoria.get("sistema_selectivo"),
                    "turno": convocatoria.get("turno"),
                    "fecha_boe": convocatoria.get("fecha_boe"),
                    "estado_importacion": "EXCLUIDA_TURNO_INTERNO",
                })
                continue

            cursor.execute(
                "SELECT id,identificador_estable FROM procesos WHERE identificador_estable=%s",
                (estable,),
            )
            existente = cursor.fetchone()
            if existente:
                resultado["existentes_boe"] += 1
                resultado["detalle"].append({
                    "codigo_externo": codigo,
                    "identificador_estable": estable,
                    "estado_importacion": "EXISTENTE_BOE",
                    "proceso_id": existente["id"],
                })
                continue

            organismo = _buscar_organismo(organismos, convocatoria.get("entidad"))
            candidatos = []
            if organismo:
                bases = convocatoria.get("bases_bop") or {}
                candidatos = _candidatos_bop(
                    cursor,
                    organismo_id=organismo["id"],
                    fecha_bases=bases.get("fecha"),
                    denominacion=convocatoria.get("denominacion"),
                    plazas=convocatoria.get("plazas"),
                )

            item = {
                "codigo_externo": codigo,
                "identificador_estable": estable,
                "entidad": convocatoria.get("entidad"),
                "organismo_id": organismo["id"] if organismo else None,
                "denominacion": convocatoria.get("denominacion"),
                "plazas": convocatoria.get("plazas"),
                "sistema_selectivo": convocatoria.get("sistema_selectivo"),
                "turno": convocatoria.get("turno"),
                "fecha_boe": convocatoria.get("fecha_boe"),
                "bases_bop": convocatoria.get("bases_bop"),
                "url_html": convocatoria.get("url_html"),
            }

            if len(candidatos) == 1:
                resultado["posibles_existentes_bop"] += 1
                item["estado_importacion"] = "POSIBLE_EXISTENTE_BOP"
                item["proceso_bop_candidato"] = candidatos[0]
            elif len(candidatos) > 1:
                resultado["revision_solapamiento"] += 1
                item["estado_importacion"] = "REVISION_SOLAPAMIENTO"
                item["procesos_bop_candidatos"] = candidatos
            else:
                resultado["nuevas"] += 1
                item["estado_importacion"] = "NUEVA"

            resultado["detalle"].append(item)

        connection.rollback()

    return resultado
