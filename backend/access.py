"""Control de acceso del módulo de empleo.

La identidad procede de Supabase Auth compartido con OpoCoach-Web.
La suscripción de pago se determina en la tabla central public.subscriptions;
la tabla public.suscripciones del módulo de empleo sigue reservada para alertas
sobre procesos concretos.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg.rows import dict_row

from app.database import get_connection

ESTADOS_CON_ACCESO = {"active", "trialing", "past_due"}


@dataclass(frozen=True)
class EmploymentAccess:
    user_id: UUID
    authenticated: bool
    subscribed: bool
    employment_access: bool


def obtener_acceso_employment(user_id: UUID) -> EmploymentAccess:
    """Consulta la suscripción de pago central asociada al usuario autenticado."""
    with get_connection() as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT status
                FROM public.subscriptions
                WHERE user_id = %s
                  AND proveedor = 'STRIPE'
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            fila = cur.fetchone()

    subscribed = bool(fila and fila["status"] in ESTADOS_CON_ACCESO)
    return EmploymentAccess(
        user_id=user_id,
        authenticated=True,
        subscribed=subscribed,
        employment_access=subscribed,
    )


def exigir_employment_access(user_id: UUID) -> EmploymentAccess:
    """Exige autenticación y suscripción de pago activa para el módulo de empleo."""
    acceso = obtener_acceso_employment(user_id)
    if not acceso.employment_access:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=403,
            detail="Esta función requiere una suscripción activa a OpoCoach.",
        )
    return acceso
