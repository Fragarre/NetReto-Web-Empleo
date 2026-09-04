from typing import Any

from .database import get_connection


def listar_procesos(
    *,
    organismo_id: int | None = None,
    estado: str | None = None,
    limite: int = 100,
) -> list[dict[str, Any]]:
    """Lista procesos, con filtros básicos para el catálogo público."""
    limite = max(1, min(limite, 200))

    query = """
        SELECT p.id, p.organismo_id, o.nombre AS organismo_nombre,
               p.codigo_externo, p.identificador_estable, p.denominacion,
               p.cuerpo_escala, p.grupo, p.subgrupo, p.tipo_proceso,
               p.sistema_selectivo, p.turno, p.plazas, p.estado,
               p.anio_oep, p.anio_convocatoria, p.fecha_convocatoria,
               p.fecha_apertura, p.fecha_cierre, p.fecha_examen,
               p.lugar_examen, p.ultima_publicacion_at,
               p.fuente_principal_id, p.datos_json,
               p.created_at, p.updated_at
        FROM procesos p
        JOIN organismos o ON o.id = p.organismo_id
    """
    conditions: list[str] = []
    params: list[Any] = []

    if organismo_id is not None:
        conditions.append("p.organismo_id = %s")
        params.append(organismo_id)
    if estado is not None:
        conditions.append("p.estado = %s")
        params.append(estado)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY COALESCE(p.fecha_examen, p.fecha_convocatoria) DESC NULLS LAST, p.id DESC LIMIT %s"
    params.append(limite)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    return [dict(zip(columns, row)) for row in rows]


def obtener_proceso(proceso_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.id, p.organismo_id, o.nombre AS organismo_nombre,
                       p.codigo_externo, p.identificador_estable, p.denominacion,
                       p.cuerpo_escala, p.grupo, p.subgrupo, p.tipo_proceso,
                       p.sistema_selectivo, p.turno, p.plazas, p.estado,
                       p.anio_oep, p.anio_convocatoria, p.fecha_convocatoria,
                       p.fecha_apertura, p.fecha_cierre, p.fecha_examen,
                       p.lugar_examen, p.ultima_publicacion_at,
                       p.fuente_principal_id, p.datos_json,
                       p.created_at, p.updated_at
                FROM procesos p
                JOIN organismos o ON o.id = p.organismo_id
                WHERE p.id = %s
                """,
                (proceso_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [description.name for description in cursor.description]

    return dict(zip(columns, row))
