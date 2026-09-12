from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

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
    if ("tecnico" in n or "tecnica" in n or "tecnic" in n) and (
        "administracion general" in n or "administracio general" in n
    ):
        return "TAG"
    if any(x in n for x in ("administrativo", "administrativa", "administratiu", "administrativa")):
        return "ADMINISTRATIVO"
    if ("tecnico" in n or "tecnica" in n or "tecnic" in n) and ("gestion" in n or "gestio" in n):
        return "TECNICO_GESTION"
    return None


def _buscar_organismo(organismos: list[dict[str, Any]], entidad: str | None) -> dict[str, Any] | None:
    visible = _entidad_visible(entidad)
    if not visible:
        return None
    objetivo = _sin(visible)
    exactos = [o for o in organismos if _sin(o.get("nombre")) == objetivo]
    return exactos[0] if len(exactos) == 1 else None


def _tipo_y_municipio(entidad: str) -> tuple[str, str | None]:
    normalizada = _sin(entidad)
    if normalizada.startswith("ayuntamiento de "):
        return "AYUNTAMIENTO", entidad[len("Ayuntamiento de "):].strip() or None
    if normalizada.startswith("mancomunitat ") or normalizada.startswith("mancomunidad "):
        return "MANCOMUNIDAD", None
    return "ENTIDAD_LOCAL", None


def _candidatos_bop(
    cursor,
    *,
    organismo_id: int,
    fecha_bases: str | None,
    denominacion: str | None,
    plazas: int | None,
) -> list[dict[str, Any]]:
    familia = _familia(denominacion)
    if not fecha_bases or not familia:
        return []
    cursor.execute(
        """
        SELECT id,identificador_estable,codigo_externo,denominacion,plazas,fecha_convocatoria,datos_json
        FROM procesos
        WHERE organismo_id=%s
          AND ambito_administrativo='SI'
          AND fecha_convocatoria=%s
          AND identificador_estable LIKE 'BOPMUN:%%'
        ORDER BY id
        """,
        (organismo_id, fecha_bases),
    )
    resultado = []
    for proceso in cursor.fetchall():
        if _familia(proceso.get("denominacion")) != familia:
            continue
        if plazas is not None and proceso.get("plazas") is not None and proceso.get("plazas") != plazas:
            continue
        resultado.append(proceso)
    return resultado


def _es_turno_interno(turno: str | None) -> bool:
    return "promocion interna" in _sin(turno)


def _datos_boe(convocatoria: dict[str, Any], codigo: str) -> dict[str, Any]:
    return {
        "origen": "BOE_LOCAL",
        "boe_id": convocatoria.get("boe_id"),
        "codigo_externo": codigo,
        "bases_bop": convocatoria.get("bases_bop"),
        "plazo_solicitudes_literal": convocatoria.get("plazo_solicitudes_literal"),
        "url_html": convocatoria.get("url_html"),
        "url_xml": convocatoria.get("url_xml"),
        "url_pdf": convocatoria.get("url_pdf"),
        "texto_plaza": convocatoria.get("texto_plaza"),
    }


def _insertar_publicacion_boe(cursor, *, proceso_id: int, convocatoria: dict[str, Any], codigo: str) -> bool:
    url = convocatoria.get("url_html")
    if not url:
        raise RuntimeError(f"URL BOE no disponible para {codigo}")
    cursor.execute(
        "SELECT 1 FROM publicaciones WHERE fuente_id=9 AND referencia=%s LIMIT 1",
        (codigo,),
    )
    if cursor.fetchone() is not None:
        return False
    cursor.execute(
        """
        INSERT INTO publicaciones (
            proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,datos_json,detectada_at
        ) VALUES (%s,9,%s,'BOE',%s,%s,%s,%s,NOW())
        """,
        (
            proceso_id, codigo, convocatoria.get("denominacion"),
            convocatoria.get("fecha_boe"), url,
            Jsonb({
                "origen": "BOE_LOCAL",
                "boe_id": convocatoria.get("boe_id"),
                "codigo_externo": codigo,
                "bases_bop": convocatoria.get("bases_bop"),
            }),
        ),
    )
    return True


def previsualizar_importacion_boe_local(*, hasta: date, dias: int = 30, aplicar: bool = False) -> dict[str, Any]:
    """Previsualiza por defecto; solo inserta o vincula convocatorias inequívocas con aplicar=True."""
    extraccion = extraer_convocatorias_boe_local(hasta=hasta, dias=dias)
    resultado: dict[str, Any] = {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
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
        "vinculadas_bop": 0,
        "revision_solapamiento": 0,
        "insertados": 0,
        "organismos_creados": 0,
        "publicaciones_creadas": 0,
        "detalle": [],
    }

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id,nombre,tipo,provincia,municipio FROM organismos ORDER BY id")
        organismos = list(cursor.fetchall())
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

            cursor.execute("SELECT id,identificador_estable FROM procesos WHERE identificador_estable=%s", (estable,))
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
                candidato = candidatos[0]
                item["proceso_bop_candidato"] = candidato
                item["estado_importacion"] = "POSIBLE_EXISTENTE_BOP"
                if aplicar:
                    proceso_id = candidato["id"]
                    datos_boe = _datos_boe(convocatoria, codigo)
                    cursor.execute(
                        """
                        UPDATE procesos
                        SET datos_json = COALESCE(datos_json,'{}'::jsonb) || %s,
                            ultima_publicacion_at = GREATEST(
                                COALESCE(ultima_publicacion_at, %s::date::timestamptz),
                                %s::date::timestamptz
                            ),
                            updated_at = NOW()
                        WHERE id=%s
                        """,
                        (
                            Jsonb({"boe_local": datos_boe}),
                            convocatoria.get("fecha_boe"),
                            convocatoria.get("fecha_boe"),
                            proceso_id,
                        ),
                    )
                    if _insertar_publicacion_boe(cursor, proceso_id=proceso_id, convocatoria=convocatoria, codigo=codigo):
                        resultado["publicaciones_creadas"] += 1
                    resultado["vinculadas_bop"] += 1
                    item["proceso_id"] = proceso_id
                    item["estado_importacion"] = "VINCULADA_BOP"
                resultado["detalle"].append(item)
                continue
            if len(candidatos) > 1:
                resultado["revision_solapamiento"] += 1
                item["estado_importacion"] = "REVISION_SOLAPAMIENTO"
                item["procesos_bop_candidatos"] = candidatos
                resultado["detalle"].append(item)
                continue

            resultado["nuevas"] += 1
            item["estado_importacion"] = "NUEVA"

            if aplicar:
                if organismo is None:
                    nombre = _entidad_visible(convocatoria.get("entidad"))
                    if not nombre:
                        raise RuntimeError(f"Entidad no identificada para {codigo}")
                    tipo, municipio = _tipo_y_municipio(nombre)
                    cursor.execute(
                        """
                        INSERT INTO organismos (nombre,tipo,municipio,provincia,activo,created_at,updated_at)
                        VALUES (%s,%s,%s,'Valencia',TRUE,NOW(),NOW())
                        RETURNING id,nombre,tipo,provincia,municipio
                        """,
                        (nombre, tipo, municipio),
                    )
                    organismo = cursor.fetchone()
                    organismos.append(organismo)
                    resultado["organismos_creados"] += 1
                    item["organismo_id"] = organismo["id"]

                datos_proceso = _datos_boe(convocatoria, codigo)
                cursor.execute(
                    """
                    INSERT INTO procesos (
                        organismo_id,codigo_externo,identificador_estable,denominacion,plazas,
                        sistema_selectivo,turno,estado,fecha_convocatoria,ultima_publicacion_at,fuente_principal_id,
                        es_oportunidad,ambito_administrativo,datos_json,updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,'EN_CURSO',%s,%s::date::timestamptz,9,TRUE,'SI',%s,NOW())
                    RETURNING id
                    """,
                    (
                        organismo["id"], codigo, estable, convocatoria.get("denominacion"),
                        convocatoria.get("plazas"), convocatoria.get("sistema_selectivo"),
                        convocatoria.get("turno"), convocatoria.get("fecha_boe"), convocatoria.get("fecha_boe"),
                        Jsonb(datos_proceso),
                    ),
                )
                proceso_id = cursor.fetchone()["id"]
                resultado["insertados"] += 1
                item["proceso_id"] = proceso_id
                item["estado_importacion"] = "INSERTADA"

                if _insertar_publicacion_boe(cursor, proceso_id=proceso_id, convocatoria=convocatoria, codigo=codigo):
                    resultado["publicaciones_creadas"] += 1

            resultado["detalle"].append(item)

        if aplicar:
            connection.commit()
        else:
            connection.rollback()

    return resultado