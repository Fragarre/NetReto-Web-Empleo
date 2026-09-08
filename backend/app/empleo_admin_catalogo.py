from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from psycopg.rows import dict_row

from auth import UsuarioAutenticado
from .database import get_connection
from . import empleo_admin as _empleo_admin
from .empleo_admin import _admin_empleo
from .temario_extractor import extraer_temario_oficial as _extraer_temario_oficial_unicode
from .ambito_administrativo import AMBITOS

_empleo_admin.extraer_temario_oficial = _extraer_temario_oficial_unicode

router = APIRouter(prefix="/admin/gestion", tags=["admin-empleo"])


class AmbitoAdministrativoRequest(BaseModel):
    ambito_administrativo: str


@router.get("/convocatorias")
def admin_convocatorias(_: UsuarioAutenticado = Depends(_admin_empleo)) -> list[dict[str, Any]]:
    # El Centro de gestión ve también REVISION/NO: solo el catálogo público exige SI.
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id, p.organismo_id, o.nombre AS organismo_nombre,
                   p.codigo_externo, p.denominacion, p.grupo, p.tipo_proceso,
                   p.sistema_selectivo, p.turno, p.plazas, p.estado,
                   p.es_oportunidad, p.ambito_administrativo,
                   p.origen_dato, p.revision_estado, p.fecha_convocatoria,
                   p.fecha_apertura, p.fecha_cierre, p.fecha_examen,
                   p.lugar_examen, p.updated_at
            FROM procesos p
            LEFT JOIN organismos o ON o.id = p.organismo_id
            WHERE p.es_oportunidad = TRUE
            ORDER BY
              CASE p.ambito_administrativo WHEN 'REVISION' THEN 0 WHEN 'SI' THEN 1 ELSE 2 END,
              COALESCE(p.fecha_examen, p.fecha_convocatoria, p.updated_at) DESC NULLS LAST,
              p.id DESC
            LIMIT 300
            """
        )
        return cursor.fetchall()


@router.patch("/procesos/{proceso_id}/ambito-administrativo")
def cambiar_ambito_administrativo(
    proceso_id: int,
    payload: AmbitoAdministrativoRequest,
    _: UsuarioAutenticado = Depends(_admin_empleo),
) -> dict[str, Any]:
    ambito = payload.ambito_administrativo.upper().strip()
    if ambito not in AMBITOS:
        raise HTTPException(status_code=400, detail="Ámbito administrativo no válido")
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            UPDATE procesos
            SET ambito_administrativo=%s, updated_at=NOW()
            WHERE id=%s
            RETURNING id, ambito_administrativo
            """,
            (ambito, proceso_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Proceso no encontrado")
        return row
