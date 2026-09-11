from __future__ import annotations

from copy import deepcopy
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database import get_connection
from .gva_estatal_import import LEGACY_ALIASES

FUENTE_GVA_ID = 1
FUENTE_GVA_URL_ESTATAL = "https://administracion.gob.es/pagFront/ofertasempleopublico/resultadosEmpleo.htm"


def _referencia_publicacion(referencia_estatal: int) -> str:
    return f"GVAESTATAL:{referencia_estatal}:CONVOCATORIA"


def _metadatos_estatales(registro: dict[str, Any]) -> dict[str, Any]:
    datos = registro.get("datos_json") or {}
    return {
        "fuente_descubrimiento": "administracion.gob.es",
        "referencia_estatal": registro.get("referencia_estatal"),
        "url_estatal": datos.get("url_estatal"),
        "via_estatal": datos.get("via_estatal"),
        "codigos_administrativos": datos.get("codigos_administrativos"),
    }


def _publicacion_oficial_valida(registro: dict[str, Any]) -> bool:
    datos = registro.get("datos_json") or {}
    url = str(datos.get("url_publicacion_oficial") or "")
    fecha = datos.get("fecha_publicacion_oficial")
    return bool(fecha and url.startswith("https://dogv.gva.es/"))


def planificar_persistencia(
    registros: list[dict[str, Any]],
    existentes_por_identificador: dict[str, dict[str, Any]],
    publicaciones_existentes: set[int] | None = None,
) -> dict[str, Any]:
    """Genera un plan de persistencia sin escribir en BD.

    El portal estatal se usa para descubrimiento. La publicación primaria que se
    persiste debe ser el PDF oficial del DOGV. Los registros existentes conservan
    todos sus campos funcionales.
    """
    publicaciones_existentes = publicaciones_existentes or set()
    legacy_ids = set(LEGACY_ALIASES.values())
    acciones: list[dict[str, Any]] = []

    for original in registros:
        registro = deepcopy(original)
        identificador = registro["identificador_estable"]
        referencia = int(registro["referencia_estatal"])
        existente = existentes_por_identificador.get(identificador)

        if not registro.get("es_oportunidad"):
            acciones.append({
                "accion": "REVISION",
                "identificador_estable": identificador,
                "referencia_estatal": referencia,
                "motivo": registro.get("motivo") or "no_publicable_automaticamente",
            })
            continue

        if not _publicacion_oficial_valida(registro):
            acciones.append({
                "accion": "BLOQUEAR",
                "identificador_estable": identificador,
                "referencia_estatal": referencia,
                "motivo": "publicacion_oficial_dogv_ausente",
            })
            continue

        metadatos = _metadatos_estatales(registro)
        crear_publicacion = referencia not in publicaciones_existentes

        if existente is not None:
            datos_existentes = existente.get("datos_json") or {}
            actualizar_metadatos = datos_existentes.get("fuente_estatal") != metadatos
            if not actualizar_metadatos and not crear_publicacion:
                acciones.append({
                    "accion": "SIN_CAMBIOS",
                    "identificador_estable": identificador,
                    "proceso_id": existente.get("id"),
                    "referencia_estatal": referencia,
                })
                continue
            acciones.append({
                "accion": "ENLAZAR_METADATOS",
                "identificador_estable": identificador,
                "proceso_id": existente.get("id"),
                "preservar_campos_funcionales": True,
                "actualizar_metadatos": actualizar_metadatos,
                "metadatos_estatales": metadatos,
                "crear_publicacion": crear_publicacion,
                "registro": registro,
            })
            continue

        if identificador in legacy_ids:
            acciones.append({
                "accion": "BLOQUEAR",
                "identificador_estable": identificador,
                "referencia_estatal": referencia,
                "motivo": "alias_legacy_ausente_en_bd",
            })
            continue

        acciones.append({
            "accion": "INSERTAR",
            "identificador_estable": identificador,
            "registro": registro,
            "crear_publicacion": True,
        })

    resumen = {
        "insertar": sum(a["accion"] == "INSERTAR" for a in acciones),
        "enlazar_metadatos": sum(
            a["accion"] == "ENLAZAR_METADATOS" and bool(a.get("actualizar_metadatos"))
            for a in acciones
        ),
        "publicaciones": sum(bool(a.get("crear_publicacion")) for a in acciones),
        "sin_cambios": sum(a["accion"] == "SIN_CAMBIOS" for a in acciones),
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


def _cargar_publicaciones_estatales(cursor, referencias: list[int]) -> set[int]:
    if not referencias:
        return set()
    refs = [_referencia_publicacion(r) for r in referencias]
    cursor.execute(
        "SELECT referencia FROM publicaciones WHERE fuente_id=%s AND referencia = ANY(%s)",
        (FUENTE_GVA_ID, refs),
    )
    salida: set[int] = set()
    for fila in cursor.fetchall():
        valor = fila.get("referencia") or ""
        partes = valor.split(":")
        if len(partes) >= 3 and partes[1].isdigit():
            salida.add(int(partes[1]))
    return salida


def _insertar_nuevo(cursor, registro: dict[str, Any]) -> int:
    datos_json = dict(registro.get("datos_json") or {})
    datos_json["fuente_principal_estatal"] = FUENTE_GVA_URL_ESTATAL
    datos_json["fuente_estatal"] = _metadatos_estatales(registro)
    fecha_publicacion = datos_json.get("fecha_publicacion_oficial")
    cursor.execute(
        """
        INSERT INTO procesos (
            organismo_id, codigo_externo, identificador_estable, denominacion,
            cuerpo_escala, grupo, tipo_proceso, turno, estado,
            anio_convocatoria, fecha_convocatoria, fecha_apertura, fecha_cierre,
            ultima_publicacion_at, fuente_principal_id, datos_json, es_oportunidad,
            origen_dato, revision_estado, ambito_administrativo, created_at, updated_at
        ) VALUES (
            1, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s::date::timestamptz, %s, %s, TRUE,
            'AUTOMATICO', 'PUBLICADA', 'SI', NOW(), NOW()
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
            int(fecha_publicacion[:4]) if fecha_publicacion else None,
            fecha_publicacion,
            registro.get("fecha_apertura"),
            registro.get("fecha_cierre"),
            fecha_publicacion,
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


def _insertar_publicacion(cursor, *, proceso_id: int, registro: dict[str, Any]) -> bool:
    referencia_estatal = int(registro["referencia_estatal"])
    datos = registro.get("datos_json") or {}
    url = datos.get("url_publicacion_oficial")
    fecha = datos.get("fecha_publicacion_oficial")
    if not url or not fecha or not str(url).startswith("https://dogv.gva.es/"):
        raise RuntimeError(f"Falta publicación oficial DOGV para la referencia {referencia_estatal}")

    referencia = _referencia_publicacion(referencia_estatal)
    cursor.execute(
        """
        INSERT INTO publicaciones (
            proceso_id, fuente_id, referencia, tipo, titulo,
            fecha_publicacion, url, datos_json, detectada_at
        ) VALUES (%s,%s,%s,'CONVOCATORIA',%s,%s,%s,%s,NOW())
        ON CONFLICT (fuente_id, referencia, url) DO NOTHING
        """,
        (
            proceso_id,
            FUENTE_GVA_ID,
            referencia,
            registro.get("denominacion"),
            fecha,
            url,
            Jsonb({
                "origen": "DOGV",
                "descubierta_via": "administracion.gob.es",
                "referencia_estatal": referencia_estatal,
                "via_estatal": datos.get("via_estatal"),
            }),
        ),
    )
    creada = cursor.rowcount == 1
    if creada:
        cursor.execute(
            """
            UPDATE procesos
            SET ultima_publicacion_at = GREATEST(
                    COALESCE(ultima_publicacion_at, %s::date::timestamptz),
                    %s::date::timestamptz
                ),
                updated_at = NOW()
            WHERE id=%s
            """,
            (fecha, fecha, proceso_id),
        )
    return creada


def persistir_registros(registros: list[dict[str, Any]], *, aplicar: bool = False) -> dict[str, Any]:
    identificadores = [r["identificador_estable"] for r in registros]
    referencias = [int(r["referencia_estatal"]) for r in registros]
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        _validar_fuente(cursor)
        existentes = _cargar_existentes(cursor, identificadores)
        publicaciones = _cargar_publicaciones_estatales(cursor, referencias)
        plan = planificar_persistencia(registros, existentes, publicaciones)

        if not aplicar:
            return plan

        if plan["resumen"]["bloquear"]:
            raise RuntimeError("Persistencia GVA bloqueada: el plan contiene anomalías")

        insertados = 0
        enlazados = 0
        publicaciones_creadas = 0
        ids_nuevos: dict[str, int] = {}

        for accion in plan["acciones"]:
            if accion["accion"] in {"REVISION", "SIN_CAMBIOS"}:
                continue

            if accion["accion"] == "INSERTAR":
                proceso_id = _insertar_nuevo(cursor, accion["registro"])
                ids_nuevos[accion["identificador_estable"]] = proceso_id
                insertados += 1
            elif accion["accion"] == "ENLAZAR_METADATOS":
                proceso_id = int(accion["proceso_id"])
                if accion.get("actualizar_metadatos"):
                    _enlazar_metadatos(cursor, accion)
                    enlazados += 1
            else:
                raise RuntimeError(f"Acción inesperada: {accion['accion']}")

            if accion.get("crear_publicacion"):
                if _insertar_publicacion(cursor, proceso_id=proceso_id, registro=accion["registro"]):
                    publicaciones_creadas += 1

        return {
            "modo": "APLICADO",
            "resumen_plan": plan["resumen"],
            "insertados": insertados,
            "enlazados": enlazados,
            "publicaciones_creadas": publicaciones_creadas,
            "ids_nuevos": ids_nuevos,
        }
