from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from .database import get_connection

REVISION_ESTADOS = ("PENDIENTE_REVISION", "PUBLICADA", "DESCARTADA")
ORIGENES = ("AUTOMATICO", "MANUAL")
TEMARIO_ESTADOS = ("PENDIENTE_REVISION", "VERIFICADO", "DESCARTADO")
TEMARIO_ORIGENES = ("AUTOMATICO", "MANUAL")


def listar_pendientes_revision(limite: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.id, p.organismo_id, o.nombre AS organismo_nombre,
                   p.codigo_externo, p.denominacion, p.grupo, p.tipo_proceso,
                   p.sistema_selectivo, p.turno, p.plazas, p.estado,
                   p.origen_dato, p.revision_estado, p.fecha_apertura,
                   p.fecha_cierre, p.fecha_examen, p.updated_at
            FROM procesos p
            LEFT JOIN organismos o ON o.id = p.organismo_id
            WHERE p.revision_estado = 'PENDIENTE_REVISION'
            ORDER BY p.updated_at DESC
            LIMIT %s
            """,
            (limite,),
        )
        return [dict(row) for row in cursor.fetchall()]


def actualizar_revision(
    proceso_id: int,
    estado: str,
    usuario_id: str | None = None,
    observaciones: str | None = None,
) -> dict[str, Any]:
    if estado not in REVISION_ESTADOS:
        raise ValueError("Estado de revisión no válido")
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM procesos WHERE id=%s", (proceso_id,))
        if cursor.fetchone() is None:
            raise ValueError("Proceso no encontrado")
        cursor.execute(
            """
            UPDATE procesos
            SET revision_estado=%s,
                revisado_at=CASE WHEN %s IN ('PUBLICADA','DESCARTADA') THEN NOW() ELSE revisado_at END,
                revisado_por=CASE WHEN %s IN ('PUBLICADA','DESCARTADA') THEN %s::uuid ELSE revisado_por END,
                observaciones_internas=COALESCE(%s, observaciones_internas),
                updated_at=NOW()
            WHERE id=%s
            RETURNING id, revision_estado, revisado_at, revisado_por, observaciones_internas
            """,
            (estado, estado, estado, usuario_id, observaciones, proceso_id),
        )
        row = cursor.fetchone()
        return dict(row)


def guardar_temario(
    proceso_id: int,
    contenido_texto: str,
    origen: str = "MANUAL",
    estado: str = "PENDIENTE_REVISION",
    fuente_url: str | None = None,
    fuente_publicacion_id: int | None = None,
    observaciones: str | None = None,
) -> dict[str, Any]:
    if origen not in TEMARIO_ORIGENES:
        raise ValueError("Origen de temario no válido")
    if estado not in TEMARIO_ESTADOS:
        raise ValueError("Estado de temario no válido")
    if not contenido_texto.strip():
        raise ValueError("El temario no puede estar vacío")
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO temarios_empleo
              (proceso_id, contenido_texto, origen, estado, fuente_url,
               fuente_publicacion_id, fecha_extraccion, observaciones, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,NOW(),%s,NOW())
            ON CONFLICT (proceso_id) DO UPDATE SET
              contenido_texto=EXCLUDED.contenido_texto,
              origen=EXCLUDED.origen,
              estado=EXCLUDED.estado,
              fuente_url=EXCLUDED.fuente_url,
              fuente_publicacion_id=EXCLUDED.fuente_publicacion_id,
              fecha_extraccion=NOW(),
              observaciones=EXCLUDED.observaciones,
              updated_at=NOW()
            RETURNING *
            """,
            (proceso_id, contenido_texto.strip(), origen, estado, fuente_url,
             fuente_publicacion_id, observaciones),
        )
        return dict(cursor.fetchone())


def obtener_temario(proceso_id: int) -> dict[str, Any] | None:
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM temarios_empleo WHERE proceso_id=%s", (proceso_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        temario = dict(row)
        cursor.execute(
            "SELECT id, numero, titulo, contenido_texto, orden FROM temas_empleo WHERE temario_id=%s ORDER BY orden",
            (temario["id"],),
        )
        temario["temas"] = [dict(r) for r in cursor.fetchall()]
        return temario


def guardar_temas(proceso_id: int, temas: list[dict[str, Any]]) -> dict[str, Any]:
    temario = obtener_temario(proceso_id)
    if temario is None:
        raise ValueError("Primero debe existir el temario")
    normalizados = []
    for i, tema in enumerate(temas, start=1):
        contenido = str(tema.get("contenido_texto") or "").strip()
        if not contenido:
            raise ValueError(f"El tema {i} no puede estar vacío")
        normalizados.append((tema.get("numero"), tema.get("titulo"), contenido, i))
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM temas_empleo WHERE temario_id=%s", (temario["id"],))
        for numero, titulo, contenido, orden in normalizados:
            cursor.execute(
                "INSERT INTO temas_empleo(temario_id,numero,titulo,contenido_texto,orden) VALUES(%s,%s,%s,%s,%s)",
                (temario["id"], numero, titulo, contenido, orden),
            )
        return obtener_temario(proceso_id) or temario
