from typing import Any

from .database import get_connection


# Tipos que no forman parte del catálogo de empleo útil para el opositor.
# Se excluyen aquí, en la capa de datos, para que no reaparezcan en la interfaz
# aunque hayan sido importados desde una fuente oficial.
TIPOS_EXCLUIDOS = (
    "Promoción interna",
    "Libre designación",
    "Concurso general de méritos",
    "Concurso de traslados",
    "Comisiones de servicio",
    "Difícil cobertura",
    "Anuncio difícil cobertura",
)


def listar_procesos(
    *,
    organismo_id: int | None = None,
    estado: str | None = None,
    limite: int = 100,
) -> list[dict[str, Any]]:
    """Lista procesos incluidos en el catálogo público, con filtros básicos."""
    limite = max(1, min(limite, 200))
    placeholders = ", ".join(["%s"] * len(TIPOS_EXCLUIDOS))

    query = f"""
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
        WHERE p.tipo_proceso NOT IN ({placeholders})
    """
    params: list[Any] = list(TIPOS_EXCLUIDOS)

    if organismo_id is not None:
        query += " AND p.organismo_id = %s"
        params.append(organismo_id)
    if estado is not None:
        query += " AND p.estado = %s"
        params.append(estado)

    query += " ORDER BY COALESCE(p.fecha_examen, p.fecha_convocatoria) DESC NULLS LAST, p.id DESC LIMIT %s"
    params.append(limite)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    return [dict(zip(columns, row)) for row in rows]


def obtener_proceso(proceso_id: int) -> dict[str, Any] | None:
    placeholders = ", ".join(["%s"] * len(TIPOS_EXCLUIDOS))

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
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
                  AND p.tipo_proceso NOT IN ({placeholders})
                """,
                (proceso_id, *TIPOS_EXCLUIDOS),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [description.name for description in cursor.description]

    return dict(zip(columns, row))
