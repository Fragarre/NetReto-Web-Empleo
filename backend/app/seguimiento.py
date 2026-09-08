from typing import Any
from uuid import UUID
from datetime import datetime

from .database import get_connection


CAMPOS_CAMBIO_RELEVANTES = (
    "fecha_apertura",
    "fecha_cierre",
    "fecha_examen",
    "lugar_examen",
    "estado",
    "plazas",
    "turno",
    "etapa_actual",
    "tipo_proceso",
    "url_oficial",
)


def suscripciones_usuario(user_id: UUID) -> list[dict[str, Any]]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.id, s.proceso_id, s.activa, s.created_at, s.updated_at,
                       p.identificador_estable, p.denominacion, p.organismo_id,
                       o.nombre AS organismo_nombre, p.tipo_proceso, p.plazas,
                       p.estado, p.anio_convocatoria, p.fecha_apertura,
                       p.fecha_cierre, p.fecha_examen, p.ultima_publicacion_at,
                       (
                           SELECT pub.url
                           FROM publicaciones pub
                           WHERE pub.proceso_id = p.id
                             AND pub.url IS NOT NULL
                             AND TRIM(pub.url) <> ''
                           ORDER BY
                             CASE
                               WHEN POSITION('convoc' IN LOWER(COALESCE(pub.tipo, ''))) > 0 THEN 0
                               WHEN POSITION('convoc' IN LOWER(COALESCE(pub.titulo, ''))) > 0 THEN 1
                               ELSE 2
                             END,
                             pub.fecha_publicacion ASC NULLS LAST,
                             pub.id ASC
                           LIMIT 1
                       ) AS url_oficial
                FROM suscripciones s
                JOIN procesos p ON p.id = s.proceso_id
                JOIN organismos o ON o.id = p.organismo_id
                WHERE s.user_id = %s
                  AND s.activa = TRUE
                  AND p.es_oportunidad = TRUE
                  AND p.ambito_administrativo = 'SI'
                ORDER BY s.created_at DESC, s.id DESC
                """,
                (str(user_id),),
            )
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def suscripcion_usuario_proceso(user_id: UUID, proceso_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.id, s.proceso_id, s.activa, s.created_at, s.updated_at
                FROM suscripciones s
                JOIN procesos p ON p.id = s.proceso_id
                WHERE s.user_id = %s
                  AND s.proceso_id = %s
                  AND p.es_oportunidad = TRUE
                  AND p.ambito_administrativo = 'SI'
                """,
                (str(user_id), proceso_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [description.name for description in cursor.description]
    return dict(zip(columns, row))


def suscribirse(user_id: UUID, proceso_id: int) -> dict[str, Any]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT es_oportunidad, ambito_administrativo FROM procesos WHERE id = %s",
                (proceso_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Proceso no encontrado")
            if not row[0] or row[1] != "SI":
                raise ValueError("El proceso no está disponible como convocatoria administrativa")

            cursor.execute(
                """
                INSERT INTO suscripciones (user_id, proceso_id, activa)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (user_id, proceso_id)
                DO UPDATE SET activa = TRUE, updated_at = now()
                RETURNING id, proceso_id, activa, created_at, updated_at
                """,
                (str(user_id), proceso_id),
            )
            row = cursor.fetchone()
            connection.commit()
            columns = [description.name for description in cursor.description]
    return dict(zip(columns, row))


def cancelar_suscripcion(user_id: UUID, proceso_id: int) -> bool:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE suscripciones
                SET activa = FALSE, updated_at = now()
                WHERE user_id = %s AND proceso_id = %s AND activa = TRUE
                RETURNING id
                """,
                (str(user_id), proceso_id),
            )
            changed = cursor.fetchone() is not None
            connection.commit()
    return changed


def cambios_usuario(user_id: UUID, *, limite: int = 100) -> list[dict[str, Any]]:
    """Devuelve únicamente novedades sustantivas de convocatorias seguidas.

    Se excluyen cambios técnicos de captura o normalización (por ejemplo,
    denominación, navegación y enriquecimiento inicial) aunque hayan quedado
    registrados históricamente como significativos.
    """
    limite = max(1, min(limite, 200))
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM (
                    SELECT
                        pub.id AS id,
                        pub.proceso_id,
                        p.identificador_estable,
                        p.denominacion,
                        o.nombre AS organismo_nombre,
                        'PUBLICACION'::text AS novedad_tipo,
                        pub.tipo,
                        NULL::text AS campo,
                        pub.titulo AS resumen,
                        pub.fecha_publicacion::timestamptz AS detectado_at,
                        TRUE AS significativo,
                        pub.url
                    FROM publicaciones pub
                    JOIN suscripciones s ON s.proceso_id = pub.proceso_id
                    JOIN procesos p ON p.id = pub.proceso_id
                    JOIN organismos o ON o.id = p.organismo_id
                    WHERE s.user_id = %s
                      AND s.activa = TRUE
                      AND p.es_oportunidad = TRUE
                      AND p.ambito_administrativo = 'SI'
                      AND COALESCE(LOWER(pub.tipo), '') NOT IN ('navegacion', 'navegación')
                      AND COALESCE(LOWER(pub.titulo), '') <> 'navegación'

                    UNION ALL

                    SELECT
                        c.id AS id,
                        c.proceso_id,
                        p.identificador_estable,
                        p.denominacion,
                        o.nombre AS organismo_nombre,
                        'CAMBIO'::text AS novedad_tipo,
                        c.tipo,
                        c.campo,
                        c.resumen,
                        c.detectado_at,
                        c.significativo,
                        pub.url
                    FROM cambios c
                    JOIN suscripciones s ON s.proceso_id = c.proceso_id
                    JOIN procesos p ON p.id = c.proceso_id
                    JOIN organismos o ON o.id = p.organismo_id
                    LEFT JOIN publicaciones pub ON pub.id = c.publicacion_id
                    WHERE s.user_id = %s
                      AND s.activa = TRUE
                      AND p.es_oportunidad = TRUE
                      AND p.ambito_administrativo = 'SI'
                      AND c.significativo = TRUE
                      AND c.valor_anterior IS NOT NULL
                      AND LOWER(COALESCE(c.campo, '')) = ANY(%s)
                      AND LOWER(COALESCE(c.valor_anterior, '')) <> 'navegación'
                      AND LOWER(COALESCE(c.valor_anterior, '')) <> 'navegacion'
                ) novedades
                ORDER BY detectado_at DESC NULLS LAST, id DESC
                LIMIT %s
                """,
                (str(user_id), str(user_id), list(CAMPOS_CAMBIO_RELEVANTES), limite),
            )
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def estado_novedades_usuario(user_id: UUID) -> dict[str, Any]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ultima_novedad_vista_at, updated_at
                FROM seguimiento_estado_usuario
                WHERE user_id = %s
                """,
                (str(user_id),),
            )
            row = cursor.fetchone()
            if row is None:
                return {"ultima_novedad_vista_at": None, "updated_at": None}
            return {"ultima_novedad_vista_at": row[0], "updated_at": row[1]}


def marcar_novedades_vistas(user_id: UUID, hasta: datetime | None) -> dict[str, Any]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO seguimiento_estado_usuario
                    (user_id, ultima_novedad_vista_at, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    ultima_novedad_vista_at = CASE
                        WHEN seguimiento_estado_usuario.ultima_novedad_vista_at IS NULL THEN EXCLUDED.ultima_novedad_vista_at
                        WHEN EXCLUDED.ultima_novedad_vista_at IS NULL THEN seguimiento_estado_usuario.ultima_novedad_vista_at
                        WHEN EXCLUDED.ultima_novedad_vista_at > seguimiento_estado_usuario.ultima_novedad_vista_at THEN EXCLUDED.ultima_novedad_vista_at
                        ELSE seguimiento_estado_usuario.ultima_novedad_vista_at
                    END,
                    updated_at = now()
                RETURNING ultima_novedad_vista_at, updated_at
                """,
                (str(user_id), hasta),
            )
            row = cursor.fetchone()
            connection.commit()
    return {"ultima_novedad_vista_at": row[0], "updated_at": row[1]}


def preparar_notificaciones() -> dict[str, Any]:
    """Crea notificaciones solo para cambios sustantivos de procesos administrativos."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO notificaciones (suscripcion_id, cambio_id, estado)
                SELECT s.id, c.id, 'PENDIENTE'
                FROM suscripciones s
                JOIN cambios c ON c.proceso_id = s.proceso_id
                JOIN procesos p ON p.id = s.proceso_id
                WHERE s.activa = TRUE
                  AND p.es_oportunidad = TRUE
                  AND p.ambito_administrativo = 'SI'
                  AND c.significativo = TRUE
                  AND c.valor_anterior IS NOT NULL
                  AND LOWER(COALESCE(c.campo, '')) = ANY(%s)
                  AND LOWER(COALESCE(c.valor_anterior, '')) NOT IN ('navegación', 'navegacion')
                ON CONFLICT (suscripcion_id, cambio_id) DO NOTHING
                RETURNING id
                """,
                (list(CAMPOS_CAMBIO_RELEVANTES),),
            )
            creadas = len(cursor.fetchall())
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM notificaciones n
                JOIN suscripciones s ON s.id = n.suscripcion_id
                JOIN cambios c ON c.id = n.cambio_id
                JOIN procesos p ON p.id = s.proceso_id
                WHERE n.estado = 'PENDIENTE'
                  AND s.activa = TRUE
                  AND p.es_oportunidad = TRUE
                  AND p.ambito_administrativo = 'SI'
                  AND c.significativo = TRUE
                  AND c.valor_anterior IS NOT NULL
                  AND LOWER(COALESCE(c.campo, '')) = ANY(%s)
                  AND LOWER(COALESCE(c.valor_anterior, '')) NOT IN ('navegación', 'navegacion')
                """,
                (list(CAMPOS_CAMBIO_RELEVANTES),),
            )
            pendientes = int(cursor.fetchone()[0])
    return {"creadas": creadas, "pendientes": pendientes, "envio": "no_realizado"}


def listar_notificaciones_pendientes(*, limite: int = 100) -> list[dict[str, Any]]:
    limite = max(1, min(limite, 500))
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT n.id, n.suscripcion_id, n.cambio_id, n.estado,
                       n.created_at, s.user_id, s.proceso_id,
                       p.identificador_estable, p.denominacion,
                       c.tipo AS cambio_tipo, c.campo,
                       c.valor_anterior, c.valor_nuevo, c.resumen,
                       c.detectado_at
                FROM notificaciones n
                JOIN suscripciones s ON s.id = n.suscripcion_id
                JOIN procesos p ON p.id = s.proceso_id
                JOIN cambios c ON c.id = n.cambio_id
                WHERE n.estado = 'PENDIENTE'
                  AND s.activa = TRUE
                  AND p.es_oportunidad = TRUE
                  AND p.ambito_administrativo = 'SI'
                  AND LOWER(COALESCE(c.campo, '')) = ANY(%s)
                  AND LOWER(COALESCE(c.valor_anterior, '')) NOT IN ('navegación', 'navegacion')
                ORDER BY n.created_at ASC, n.id ASC
                LIMIT %s
                """,
                (list(CAMPOS_CAMBIO_RELEVANTES), limite),
            )
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]
