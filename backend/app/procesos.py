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
    "Acto único telemático",
)

PATRONES_TITULO_EXCLUIDOS = (
    "promoción interna", "promocion interna", "promoció interna", "promocio interna",
    "concurso de traslados", "concurso de traslado", "libre designación", "libre designacion",
    "comisiones de servicio", "comissions de servei", "acto único telemático",
    "acto unico telematico", "acte unic telematic", "acte únic telemàtic",
    "concurs de mèrits per a la provisió", "concurs de merits per a la provisio",
    "concurso de méritos para la provisión", "concurso de meritos para la provision",
)


def _condiciones_exclusion() -> tuple[str, list[Any]]:
    placeholders_tipo = ", ".join(["%s"] * len(TIPOS_EXCLUIDOS))
    condiciones = ["p.es_oportunidad = TRUE", "p.ambito_administrativo = 'SI'"]
    condiciones.append(f"p.tipo_proceso NOT IN ({placeholders_tipo})")
    condiciones.append("COALESCE(UPPER(p.turno), '') <> 'PROMOCION_INTERNA'")
    # El cierre de inscripción no finaliza el proceso selectivo.
    condiciones.append("COALESCE(LOWER(p.estado), '') NOT IN ('cerrado', 'finalizado')")
    params: list[Any] = list(TIPOS_EXCLUIDOS)
    for patron in PATRONES_TITULO_EXCLUIDOS:
        condiciones.append("POSITION(%s IN LOWER(COALESCE(p.denominacion, ''))) = 0")
        params.append(patron)
    return " AND ".join(condiciones), params


SELECT_FIELDS = """
       p.id, p.organismo_id, o.nombre AS organismo_nombre,
       p.codigo_externo, p.identificador_estable, p.denominacion,
       p.cuerpo_escala, p.grupo, p.subgrupo, p.tipo_proceso,
       p.sistema_selectivo, p.turno, p.plazas, p.estado,
       p.es_oportunidad, p.ambito_administrativo,
       p.coaching_disponible, p.coaching_convocatoria_id,
       p.anio_oep, p.anio_convocatoria, p.fecha_convocatoria,
       p.fecha_apertura, p.fecha_cierre, p.fecha_examen,
       p.lugar_examen, p.ultima_publicacion_at,
       p.fuente_principal_id, p.datos_json,
       (
           SELECT pub.url FROM publicaciones pub
           WHERE pub.proceso_id = p.id AND pub.url IS NOT NULL AND TRIM(pub.url) <> ''
           ORDER BY CASE
               WHEN UPPER(TRIM(COALESCE(pub.tipo, ''))) IN ('CONVOCATORIA', 'BASES') THEN 0
               WHEN LOWER(COALESCE(pub.tipo, '')) LIKE CONCAT('%%', 'convoc', '%%') THEN 1
               WHEN LOWER(COALESCE(pub.titulo, '')) LIKE CONCAT('%%', 'convoc', '%%') THEN 2
               ELSE 3 END,
             pub.fecha_publicacion ASC NULLS LAST, pub.id ASC
           LIMIT 1
       ) AS url_oficial
"""


def listar_procesos(*, organismo_id: int | None = None, estado: str | None = None, limite: int = 100) -> list[dict[str, Any]]:
    """Lista oportunidades administrativas confirmadas del catálogo público."""
    limite = max(1, min(limite, 200))
    exclusion_sql, params = _condiciones_exclusion()
    query = f"SELECT {SELECT_FIELDS} FROM procesos p JOIN organismos o ON o.id=p.organismo_id WHERE {exclusion_sql}"
    if organismo_id is not None:
        query += " AND p.organismo_id = %s"; params.append(organismo_id)
    if estado is not None:
        query += " AND p.estado = %s"; params.append(estado)
    query += " ORDER BY COALESCE(p.fecha_examen,p.fecha_convocatoria,p.fecha_apertura) DESC NULLS LAST,p.id DESC LIMIT %s"
    params.append(limite)
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(query, tuple(params)); rows=cursor.fetchall(); columns=[d.name for d in cursor.description]
    return [dict(zip(columns,row)) for row in rows]


def obtener_proceso(proceso_id: int) -> dict[str, Any] | None:
    exclusion_sql, exclusion_params = _condiciones_exclusion()
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(f"SELECT {SELECT_FIELDS} FROM procesos p JOIN organismos o ON o.id=p.organismo_id WHERE p.id=%s AND {exclusion_sql}", (proceso_id,*exclusion_params))
        row=cursor.fetchone()
        if row is None: return None
        columns=[d.name for d in cursor.description]
    return dict(zip(columns,row))
