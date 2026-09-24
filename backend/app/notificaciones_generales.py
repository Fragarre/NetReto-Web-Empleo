"""Generación idempotente de eventos generales de nuevas oportunidades.

No resuelve destinatarios ni envía correo. El criterio de visibilidad reutiliza
exactamente el mismo predicado que el catálogo público de Empleo.
"""

from __future__ import annotations

import os
from typing import Iterable
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .database import get_connection
from .procesos import _condiciones_catalogo


TIPO_NUEVA_OPORTUNIDAD = "NUEVA_OPORTUNIDAD"
ESTADOS_COMERCIALES = ("active", "trialing", "past_due")
EMAIL_PRUEBA = "fragarre@outlook.es"


def destinatarios_generales() -> list[tuple[UUID, str]]:
    """Usuarios comerciales activos más la excepción explícita de prueba."""
    database_url = os.getenv("TUCOACH_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("TUCOACH_DATABASE_URL no está configurada")

    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT DISTINCT p.id AS user_id, p.email
                FROM public.profiles p
                JOIN public.subscriptions s ON s.user_id = p.id
                WHERE p.activo = TRUE
                  AND p.email IS NOT NULL
                  AND TRIM(p.email) <> ''
                  AND s.proveedor = 'STRIPE'
                  AND s.status = ANY(%s)
                UNION
                SELECT p.id AS user_id, p.email
                FROM public.profiles p
                WHERE p.activo = TRUE
                  AND LOWER(TRIM(COALESCE(p.email, ''))) = %s
                """,
                (list(ESTADOS_COMERCIALES), EMAIL_PRUEBA),
            )
            return [(row["user_id"], str(row["email"]).strip()) for row in cursor.fetchall()]


def preparar_envios_eventos(evento_ids: Iterable[int]) -> int:
    """Crea una sola fila PENDIENTE por evento y usuario destinatario."""
    eventos = sorted({int(evento_id) for evento_id in evento_ids})
    if not eventos:
        return 0

    destinatarios = destinatarios_generales()
    if not destinatarios:
        return 0

    creados = 0
    with get_connection() as connection, connection.cursor() as cursor:
        for evento_id in eventos:
            for user_id, email in destinatarios:
                cursor.execute(
                    """
                    INSERT INTO empleo_envios_notificacion
                        (evento_id, user_id, email, estado)
                    VALUES (%s, %s, %s, 'PENDIENTE')
                    ON CONFLICT (evento_id, user_id) DO NOTHING
                    """,
                    (evento_id, user_id, email),
                )
                creados += cursor.rowcount
        connection.commit()
    return creados


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
