from typing import Any
from uuid import UUID

from .database import get_connection


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
                               WHEN LOWER(COALESCE(pub.tipo, '')) LIKE CONCAT('%', 'convoc', '%') THEN 0
                               WHEN LOWER(COALESCE(pub.titulo, '')) LIKE CONCAT('%', 'convoc', '%') THEN 1
                               ELSE 2
                             END,
                             pub.fecha_publicacion ASC NULLS LAST,
                             pub.id ASC
                           LIMIT 1
                       ) AS url_oficial
                FROM suscripciones s
                JOIN procesos p ON p.id = s.proceso_id
                JOIN organismos o ON o.id = p.organismo_id
                WHERE s.user_id = %s AND s.activa = TRUE AND p.es_oportunidad = TRUE
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
                WHERE s.user_id = %s AND s.proceso_id = %s
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
                "SELECT es_oportunidad FROM procesos WHERE id = %s",
                (proceso_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Proceso no encontrado")
            if not row[0]:
                raise ValueError("El proceso no está disponible como oportunidad")

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
    """Devuelve novedades útiles de convocatorias seguidas.

    Las publicaciones oficiales se muestran como novedades por sí mismas.
    Los cambios internos solo se muestran cuando modifican un valor ya existente;
    las altas iniciales desde NULL se consideran carga/enriquecimiento y no una novedad.
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
                      AND c.significativo = TRUE
                      AND c.valor_anterior IS NOT NULL
                ) novedades
                ORDER BY detectado_at DESC NULLS LAST, id DESC
                LIMIT %s
                """,
                (str(user_id), str(user_id), limite),
            )
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def preparar_notificaciones() -> dict[str, Any]:
    """Crea notificaciones pendientes para cambios significativos de procesos suscritos."""
    creadas = 0
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO notificaciones (suscripcion_id, cambio_id, estado)
                SELECT s.id, c.id, 'PENDIENTE'
                FROM suscripciones s
                JOIN cambios c ON c.proceso_id = s.proceso_id
                WHERE s.activa = TRUE
                  AND c.significativo = TRUE
                  AND c.valor_anterior IS NOT NULL
                ON CONFLICT (suscripcion_id, cambio_id) DO NOTHING
                RETURNING id
                """
            )
            creadas = len(cursor.fetchall())
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM notificaciones n
                JOIN suscripciones s ON s.id = n.suscripcion_id
                JOIN cambios c ON c.id = n.cambio_id
                WHERE n.estado = 'PENDIENTE'
                  AND s.activa = TRUE
                  AND c.significativo = TRUE
                  AND c.valor_anterior IS NOT NULL
                """
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
                       n.created_at,
                       s.user_id, s.proceso_id,
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
                ORDER BY n.created_at ASC, n.id ASC
                LIMIT %s
                """,
                (limite,),
            )
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]
