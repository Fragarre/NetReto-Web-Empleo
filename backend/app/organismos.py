from typing import Any

from .database import get_connection


def listar_organismos(*, solo_activos: bool = True) -> list[dict[str, Any]]:
    query = """
        SELECT id, nombre, tipo, provincia, municipio, activo,
               created_at, updated_at
        FROM organismos
    """
    params: tuple[Any, ...] = ()
    if solo_activos:
        query += " WHERE activo = %s"
        params = (True,)
    query += " ORDER BY nombre"

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    return [dict(zip(columns, row)) for row in rows]


def obtener_organismo(organismo_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, nombre, tipo, provincia, municipio, activo,
                       created_at, updated_at
                FROM organismos
                WHERE id = %s
                """,
                (organismo_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [description.name for description in cursor.description]

    return dict(zip(columns, row))


def listar_fuentes(*, organismo_id: int | None = None, solo_activas: bool = True) -> list[dict[str, Any]]:
    query = """
        SELECT f.id, f.organismo_id, f.nombre, f.tipo, f.url,
               f.prioridad, f.activa, f.created_at, f.updated_at,
               o.nombre AS organismo_nombre
        FROM fuentes f
        LEFT JOIN organismos o ON o.id = f.organismo_id
    """
    conditions: list[str] = []
    params: list[Any] = []

    if solo_activas:
        conditions.append("f.activa = %s")
        params.append(True)
    if organismo_id is not None:
        conditions.append("f.organismo_id = %s")
        params.append(organismo_id)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY f.prioridad, f.nombre"

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    return [dict(zip(columns, row)) for row in rows]
