"""Generación idempotente de eventos generales de nuevas oportunidades.

No resuelve destinatarios ni envía correo. El criterio de visibilidad reutiliza
exactamente el mismo predicado que el catálogo público de Empleo.
"""

from __future__ import annotations

from typing import Iterable

from .database import get_connection
from .procesos import _condiciones_catalogo


TIPO_NUEVA_OPORTUNIDAD = "NUEVA_OPORTUNIDAD"


def ids_oportunidades_visibles() -> set[int]:
    """Devuelve los ids que cumplen ahora mismo el filtro del catálogo público."""
    catalogo_sql, params = _condiciones_catalogo()
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT p.id
            FROM procesos p
            JOIN organismos o ON o.id = p.organismo_id
            WHERE {catalogo_sql}
            """,
            tuple(params),
        )
        return {int(row[0]) for row in cursor.fetchall()}


def registrar_nuevas_oportunidades(proceso_ids: Iterable[int]) -> list[int]:
    """Crea como máximo un evento NUEVA_OPORTUNIDAD por proceso.

    Devuelve únicamente los ids de evento creados en esta llamada.
    """
    ids = sorted({int(proceso_id) for proceso_id in proceso_ids})
    if not ids:
        return []

    creados: list[int] = []
    with get_connection() as connection, connection.cursor() as cursor:
        for proceso_id in ids:
            cursor.execute(
                """
                INSERT INTO empleo_eventos_notificacion (proceso_id, tipo)
                VALUES (%s, %s)
                ON CONFLICT (proceso_id, tipo) DO NOTHING
                RETURNING id
                """,
                (proceso_id, TIPO_NUEVA_OPORTUNIDAD),
            )
            row = cursor.fetchone()
            if row is not None:
                creados.append(int(row[0]))
        connection.commit()
    return creados
