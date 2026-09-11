from __future__ import annotations

from copy import deepcopy
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database import get_connection
from .gva_estatal_import import LEGACY_ALIASES

FUENTE_GVA_ID = 1
FUENTE_GVA_URL_ESTATAL = "https://administracion.gob.es/pagFront/ofertasempleopublico/resultadosEmpleo.htm"


def planificar_persistencia(
    registros: list[dict[str, Any]],
    existentes_por_identificador: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Genera un plan de persistencia sin escribir en BD.

    Reglas de seguridad:
    - Los cuatro procesos GVA legacy jamás se sobrescriben con campos funcionales.
      Solo se propone añadir metadatos de la referencia estatal a datos_json.
    - Los nuevos registros inequívocos se proponen como INSERT.
    - Los registros no publicables se dejan en REVISION y nunca se insertan aquí.
    - Si ya existe un identificador nuevo, solo se propone actualización de metadatos;
      no se sustituyen denominación, fechas, estado ni clasificación.
    """
    legacy_ids = set(LEGACY_ALIASES.values())
    acciones: list[dict[str, Any]] = []

    for original in registros:
        registro = deepcopy(original)
        identificador = registro["identificador_estable"]
        existente = existentes_por_identificador.get(identificador)

        if not registro.get("es_oportunidad"):
            acciones.append({
                "accion": "REVISION",
                "identificador_estable": identificador,
                "referencia_estatal": registro.get("referencia_estatal"),
                "motivo": registro.get("motivo") or "no_publicable_automaticamente",
            })
            continue

        metadatos = {
            "fuente_descubrimiento": "administracion.gob.es",
            "referencia_estatal": registro.get("referencia_estatal"),
            "url_estatal": (registro.get("datos_json") or {}).get("url_estatal"),
            "via_estatal": (registro.get("datos_json") or {}).get("via_estatal"),
            "codigos_administrativos": (registro.get("datos_json") or {}).get("codigos_administrativos"),
        }

        if existente is not None:
            acciones.append({
                "accion": "ENLAZAR_METADATOS",
                "identificador_estable": identificador,
                "proceso_id": existente.get("id"),
                "preservar_campos_funcionales": True,
                "metadatos_estatales": metadatos,
            })
            continue

        if identificador in legacy_ids:
            acciones.append({
                "accion": "BLOQUEAR",
                "identificador_estable": identificador,
                "referencia_estatal": registro.get("referencia_estatal"),
                "motivo": "alias_legacy_ausente_en_bd",
            })
            continue

        acciones.append({
            "accion": "INSERTAR",
            "identificador_estable": identificador,
            "registro": registro,
        })

    resumen = {
        "insertar": sum(a["accion"] == "INSERTAR" for a in acciones),
        "enlazar_metadatos": sum(a["accion"] == "ENLAZAR_METADATOS" for a in acciones),
        "revision": sum(a["accion"] == "REVISION" for a in acciones),
        "bloquear": sum(a["accion"] == "BLOQUEAR" for a in acciones),
    }
    return {"modo": "SOLO_REVISION", "resumen": resumen, "acciones": acciones}


def _validar_fuente(cursor) -> None:
    cursor.execute("SELECT id, organismo_id, activa FROM fuentes WHERE id=%s", (FUENTE_GVA_ID,))
    fuente = cursor.fetchone()
    if fuente is None:
        raise RuntimeError("No existe la fuente GVA esperada (id=1)")
    if fuente.get("organismo_id") != 1:
        raise RuntimeError("La fuente GVA id=1 no pertenece al organismo GVA")
    if not fuente.get("activa"):
        raise RuntimeError("La fuente GVA id=1 está inactiva")


def _cargar_existentes(cursor, identificadores: list[str]) -> dict[str, dict[str, Any]]:
    if not identificadores:
        return {}
    cursor.execute(
        "SELECT id, identificador_estable, datos_json FROM procesos WHERE identificador_estable = ANY(%s)",
        (identificadores,),
    )
    return {fila["identificador_estable"]: fila for fila in cursor.fetchall()}


def _insertar_nuevo(cursor, registro: dict[str, Any]) -> int:
    datos_json = dict(registro.get("datos_json") or {})
    datos_json["fuente_principal_estatal"] = FUENTE_GVA_URL_ESTATAL
    cursor.execute(
        """
        INSERT INTO procesos (
            organismo_id, codigo_externo, identificador_estable, denominacion,
            cuerpo_escala, grupo, tipo_proceso, turno, estado,
            anio_convocatoria, fecha_apertura, fecha_cierre, fuente_principal_id,
            datos_json, es_oportunidad, origen_dato, revision_estado,
            ambito_administrativo, created_at, updated_at
        ) VALUES (
            1, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, TRUE, 'AUTOMATICO', 'PUBLICADA',
            'SI', NOW(), NOW()
        )
        ON CONFLICT (identificador_estable) DO NOTHING
        RETURNING id
        """,
        (
            str(registro.get("referencia_estatal")),
            registro["identificador_estable"],
            registro["denominacion"],
            registro.get("cuerpo_escala"),
            registro.get("grupo"),
            registro.get("tipo_proceso"),
            registro.get("turno"),
            registro.get("estado") or "EN_CURSO",
            int((registro.get("fecha_apertura") or "0000")[:4]) if registro.get("fecha_apertura") else None,
            registro.get("fecha_apertura"),
            registro.get("fecha_cierre"),
            FUENTE_GVA_ID,
            Jsonb(datos_json),
        ),
    )
    fila = cursor.fetchone()
    if fila is None:
        cursor.execute(
            "SELECT id FROM procesos WHERE identificador_estable=%s",
            (registro["identificador_estable"],),
        )
        fila = cursor.fetchone()
        if fila is None:
            raise RuntimeError(f"No se pudo insertar ni localizar {registro['identificador_estable']}")
    return int(fila["id"])


def _enlazar_metadatos(cursor, accion: dict[str, Any]) -> None:
    cursor.execute(
        """
        UPDATE procesos
        SET datos_json = COALESCE(datos_json, '{}'::jsonb) || %s,
            updated_at = CASE
                WHEN COALESCE(datos_json, '{}'::jsonb) @> %s::jsonb THEN updated_at
                ELSE NOW()
            END
        WHERE id=%s AND identificador_estable=%s
        """,
        (
            Jsonb({"fuente_estatal": accion["metadatos_estatales"]}),
            Jsonb({"fuente_estatal": accion["metadatos_estatales"]}),
            accion["proceso_id"],
            accion["identificador_estable"],
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"No se pudo enlazar de forma inequívoca {accion['identificador_estable']}")


def persistir_registros(registros: list[dict[str, Any]], *, aplicar: bool = False) -> dict[str, Any]:
    """Planifica y, solo con aplicar=True, persiste de forma transaccional e idempotente.

    Nunca modifica campos funcionales de registros ya existentes. Si el plan contiene
    un BLOQUEO, no se aplica ninguna escritura.
    """
    identificadores = [r["identificador_estable"] for r in registros]
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        _validar_fuente(cursor)
        existentes = _cargar_existentes(cursor, identificadores)
        plan = planificar_persistencia(registros, existentes)

        if not aplicar:
            return plan

        if plan["resumen"]["bloquear"]:
            raise RuntimeError("Persistencia GVA bloqueada: el plan contiene anomalías legacy")

        insertados = 0
        enlazados = 0
        ids_nuevos: dict[str, int] = {}

        for accion in plan["acciones"]:
            if accion["accion"] == "REVISION":
                continue
            if accion["accion"] == "INSERTAR":
                proceso_id = _insertar_nuevo(cursor, accion["registro"])
                ids_nuevos[accion["identificador_estable"]] = proceso_id
                insertados += 1
                continue
            if accion["accion"] == "ENLAZAR_METADATOS":
                _enlazar_metadatos(cursor, accion)
                enlazados += 1
                continue
            raise RuntimeError(f"Acción inesperada: {accion['accion']}")

        return {
            "modo": "APLICADO",
            "resumen_plan": plan["resumen"],
            "insertados": insertados,
            "enlazados": enlazados,
            "ids_nuevos": ids_nuevos,
        }
