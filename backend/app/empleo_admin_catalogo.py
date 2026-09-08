from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from auth import UsuarioAutenticado
from .database import get_connection
from . import empleo_admin as _empleo_admin
from .empleo_admin import _admin_empleo
from .temario_extractor import extraer_temario_oficial as _extraer_temario_oficial_unicode

# El router principal de gestión está definido en empleo_admin.py y su endpoint
# de extracción resuelve extraer_temario_oficial en tiempo de ejecución. Sustituimos
# aquí la implementación antigua por el extractor Unicode específico, sin duplicar
# rutas ni alterar el resto del Centro de gestión.
_empleo_admin.extraer_temario_oficial = _extraer_temario_oficial_unicode

router = APIRouter(prefix="/admin/gestion", tags=["admin-empleo"])


@router.get("/convocatorias")
def admin_convocatorias(_: UsuarioAutenticado = Depends(_admin_empleo)) -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id, p.organismo_id, o.nombre AS organismo_nombre,
                   p.codigo_externo, p.denominacion, p.grupo, p.tipo_proceso,
                   p.sistema_selectivo, p.turno, p.plazas, p.estado,
                   p.origen_dato, p.revision_estado, p.fecha_convocatoria,
                   p.fecha_apertura, p.fecha_cierre, p.fecha_examen,
                   p.lugar_examen, p.updated_at
            FROM procesos p
            LEFT JOIN organismos o ON o.id = p.organismo_id
            WHERE p.es_oportunidad = TRUE
            ORDER BY COALESCE(p.fecha_examen, p.fecha_convocatoria, p.updated_at) DESC NULLS LAST, p.id DESC
            LIMIT 300
            """
        )
        return cursor.fetchall()
