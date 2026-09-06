from typing import Any

from .database import get_connection


# Tipos que no forman parte del catálogo de empleo útil para el opositor.
TIPOS_EXCLUIDOS = (
    "Promoción interna",
    "Libre designación",
    "Concurso general de méritos",
    "Concurso de traslados",
    "Comisiones de servicio",
    "Difícil cobertura",
    "Anuncio difícil cobertura",
)

# Algunas fuentes clasifican de forma demasiado genérica procesos que por su
# denominación son inequívocamente de una categoría excluida. Se controlan
# también por título para evitar que reaparezcan en el catálogo.
PATRONES_TITULO_EXCLUIDOS = (
    "%promoción interna%",
    "%promocion interna%",
    "%promoció interna%",
    "%promocio interna%",
    "%concurso de traslados%",
    "%concurso de traslado%",
    "%libre designación%",
    "%libre designacion%",
    "%comisiones de servicio%",
    "%comissions de servei%",
)


def _condiciones_exclusion() -> tuple[str, list[Any]]:
    placeholders_tipo = ", ".join(["%s"] * len(TIPOS_EXCLUIDOS))
    condiciones = ["p.es_oportunidad = TRUE"]
    condiciones.append(f"p.tipo_proceso NOT IN ({placeholders_tipo})")
    params: list[Any] = list(TIPOS_EXCLUIDOS)
    for patron in PATRONES_TITULO_EXCLUIDOS:
        condiciones.append("LOWER(COALESCE(p.denominacion, '')) NOT LIKE %s")
        params.append(patron)
    return " AND ".join(condiciones), params


def listar_procesos(
    *,
    organismo_id: int | None = None,
    estado: str | None = None,
    limite: int = 100,
) -> list[dict[str, Any]]:
    """Lista oportunidades incluidas en el catálogo público."""
    limite = max(1, min(limite, 200))
    exclusion_sql, params = _condiciones_exclusion()

    query = f"""
        SELECT p.id, p.organismo_id, o.nombre AS organismo_nombre,
               p.codigo_externo, p.identificador_estable, p.denominacion,
               p.cuerpo_escala, p.grupo, p.subgrupo, p.tipo_proceso,
               p.sistema_selectivo, p.turno, p.plazas, p.estado,
               p.es_oportunidad,
               p.anio_oep, p.anio_convocatoria, p.fecha_convocatoria,
               p.fecha_apertura, p.fecha_cierre, p.fecha_examen,
               p.lugar_examen, p.ultima_publicacion_at,
               p.fuente_principal_id, p.datos_json,
               p.created_at, p.updated_at
        FROM procesos p
        JOIN organismos o ON o.id = p.organismo_id
        WHERE {exclusion_sql}
    """

    if organismo_id is not None:
        query += " AND p.organismo_id = %s"
        params.append(organismo_id)
    if estado is not None:
        query += " AND p.estado = %s"
        params.append(estado)

    query += " ORDER BY COALESCE(p.fecha_examen, p.fecha_convocatoria, p.fecha_apertura) DESC NULLS LAST, p.id DESC LIMIT %s"
    params.append(limite)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    return [dict(zip(columns, row)) for row in rows]


def obtener_proceso(proceso_id: int) -> dict[str, Any] | None:
    exclusion_sql, exclusion_params = _condiciones_exclusion()

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT p.id, p.organismo_id, o.nombre AS organismo_nombre,
                       p.codigo_externo, p.identificador_estable, p.denominacion,
                       p.cuerpo_escala, p.grupo, p.subgrupo, p.tipo_proceso,
                       p.sistema_selectivo, p.turno, p.plazas, p.estado,
                       p.es_oportunidad,
                       p.anio_oep, p.anio_convocatoria, p.fecha_convocatoria,
                       p.fecha_apertura, p.fecha_cierre, p.fecha_examen,
                       p.lugar_examen, p.ultima_publicacion_at,
                       p.fuente_principal_id, p.datos_json,
                       p.created_at, p.updated_at
                FROM procesos p
                JOIN organismos o ON o.id = p.organismo_id
                WHERE p.id = %s
                  AND {exclusion_sql}
                """,
                (proceso_id, *exclusion_params),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [description.name for description in cursor.description]

    return dict(zip(columns, row))
