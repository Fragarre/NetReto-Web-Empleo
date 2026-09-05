from typing import Any

from .database import get_connection


def listar_publicaciones(*, proceso_id: int, limite: int = 100) -> list[dict[str, Any]]:
    limite = max(1, min(limite, 200))
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, proceso_id, fuente_id, referencia, tipo, titulo,
                       fecha_publicacion, url, contenido_hash, datos_json,
                       detectada_at
                FROM publicaciones
                WHERE proceso_id = %s
                ORDER BY fecha_publicacion DESC NULLS LAST, id DESC
                LIMIT %s
                """,
                (proceso_id, limite),
            )
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def listar_cambios(*, proceso_id: int, limite: int = 100) -> list[dict[str, Any]]:
    limite = max(1, min(limite, 200))
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.id, c.proceso_id, c.publicacion_id, c.tipo, c.campo,
                       c.valor_anterior, c.valor_nuevo, c.resumen,
                       c.significativo, c.detectado_at
                FROM cambios c
                WHERE c.proceso_id = %s
                ORDER BY c.detectado_at DESC, c.id DESC
                LIMIT %s
                """,
                (proceso_id, limite),
            )
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]
