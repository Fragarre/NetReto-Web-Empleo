from typing import Any

from .database import get_connection


def preparar_notificaciones() -> dict[str, Any]:
    """Crea notificaciones pendientes para cambios significativos de procesos suscritos.

    La operación es idempotente gracias a la restricción UNIQUE de
    (suscripcion_id, cambio_id). No envía correo; únicamente deja preparada
    la cola para el futuro servicio de notificaciones.
    """
    creadas = 0
    existentes = 0

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
                """
            )
            pendientes = int(cursor.fetchone()[0])

    return {
        "creadas": creadas,
        "pendientes": pendientes,
        "envio": "no_realizado",
    }


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
