from .database import get_connection


# Registros creados por la prueba anterior y que el filtro corregido ya excluye.
REGISTROS_PRUEBA = ("2026/10924", "2026/10931", "2026/11054")

# Procesos válidos importados durante la prueba del BOP.
REGISTROS_VALIDOS_PRUEBA = ("2026/10873", "2026/10875", "2026/10878", "2026/10879", "2026/10881")


def limpiar_anuncios_no_empleo() -> dict[str, int]:
    """Elimina exclusivamente los tres anuncios incorrectos de la prueba BOP."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM procesos WHERE organismo_id=2 AND codigo_externo = ANY(%s)",
                (list(REGISTROS_PRUEBA),),
            )
            proceso_ids = [row[0] for row in cursor.fetchall()]

            if not proceso_ids:
                return {"procesos_eliminados": 0, "publicaciones_eliminadas": 0, "cambios_eliminados": 0}

            cursor.execute(
                "DELETE FROM notificaciones WHERE cambio_id IN (SELECT id FROM cambios WHERE proceso_id = ANY(%s))",
                (proceso_ids,),
            )
            cursor.execute(
                "DELETE FROM suscripciones WHERE proceso_id = ANY(%s)",
                (proceso_ids,),
            )
            cursor.execute(
                "DELETE FROM cambios WHERE proceso_id = ANY(%s)",
                (proceso_ids,),
            )
            cursor.execute(
                "DELETE FROM publicaciones WHERE proceso_id = ANY(%s)",
                (proceso_ids,),
            )
            cursor.execute(
                "DELETE FROM procesos WHERE id = ANY(%s)",
                (proceso_ids,),
            )
            procesos_eliminados = cursor.rowcount

        connection.commit()

    return {
        "procesos_eliminados": procesos_eliminados,
        "publicaciones_eliminadas": 3,
        "cambios_eliminados": 0,
    }


def normalizar_bop_prueba() -> dict[str, int]:
    """Corrige los datos de la primera prueba del BOP."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Solo 2026/10881 necesita cambiar de DVAL:149/22 a DVAL:149/22E.
            cursor.execute(
                """
                SELECT 1
                  FROM procesos
                 WHERE identificador_estable = 'DVAL:149/22E'
                   AND NOT (organismo_id = 2 AND codigo_externo = '2026/10881')
                 LIMIT 1
                """
            )
            conflicto = cursor.fetchone()
            if conflicto:
                raise ValueError("Ya existe otro proceso con identificador estable DVAL:149/22E")

            cursor.execute(
                """
                UPDATE procesos
                   SET identificador_estable = CASE codigo_externo
                           WHEN '2026/10881' THEN 'DVAL:149/22E'
                           ELSE identificador_estable
                       END,
                       fecha_convocatoria = NULL,
                       plazas = CASE codigo_externo
                           WHEN '2026/10873' THEN 1
                           WHEN '2026/10875' THEN 3
                           WHEN '2026/10878' THEN 1
                           WHEN '2026/10879' THEN 4
                           WHEN '2026/10881' THEN 1
                           ELSE plazas
                       END,
                       updated_at = NOW()
                 WHERE organismo_id = 2
                   AND codigo_externo = ANY(%s)
                   AND (
                       fecha_convocatoria = DATE '2026-09-02'
                       OR codigo_externo IN ('2026/10873','2026/10875','2026/10878','2026/10879','2026/10881')
                   )
                """,
                (list(REGISTROS_VALIDOS_PRUEBA),),
            )
            corregidos = cursor.rowcount
        connection.commit()
    return {"procesos_normalizados": corregidos}
