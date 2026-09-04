"""Reglas de acceso del módulo de empleo.

La integración real adaptará esta capacidad al mecanismo central de NetReto.
No se modifica aquí la lógica de autenticación o suscripciones del proyecto principal.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class EmploymentAccess:
    user_id: UUID
    authenticated: bool
    subscribed: bool
    employment_access: bool


def tiene_employment_access(*, authenticated: bool, subscribed: bool) -> bool:
    """Regla inicial: autenticación + suscripción activa."""
    return bool(authenticated and subscribed)
