from __future__ import annotations

import io
import os
import re
from typing import Any

import httpx
import psycopg
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pypdf import PdfReader

from auth import UsuarioAutenticado, usuario_actual
from .database import get_connection

REVISION_ESTADOS = ("PENDIENTE_REVISION", "PUBLICADA", "DESCARTADA")
ORIGENES = ("AUTOMATICO", "MANUAL")
TEMARIO_ESTADOS = ("PENDIENTE_REVISION", "VERIFICADO", "DESCARTADO")
TEMARIO_ORIGENES = ("AUTOMATICO", "MANUAL")

router = APIRouter(prefix="/admin/gestion", tags=["admin-empleo"])


class ProcesoAdminRequest(BaseModel):
    organismo_id: int
    denominacion: str = Field(min_length=1)
    codigo_externo: str | None = None
    identificador_estable: str | None = None
    cuerpo_escala: str | None = None
    grupo: str | None = None
    subgrupo: str | None = None
    tipo_proceso: str | None = None
    sistema_selectivo: str | None = None
    turno: str | None = None
    plazas: int | None = Field(default=None, ge=0)
    estado: str = "EN_CURSO"
    anio_oep: int | None = None
    anio_convocatoria: int | None = None
    fecha_convocatoria: str | None = None
    fecha_apertura: str | None = None
    fecha_cierre: str | None = None
    fecha_examen: str | None = None
    lugar_examen: str | None = None
    fuente_principal_id: int | None = None
    datos_json: dict[str, Any] | None = None
    es_oportunidad: bool = True
    revision_estado: str = "PENDIENTE_REVISION"
    observaciones_internas: str | None = None


class RevisionRequest(BaseModel):
    estado: str
    observaciones: str | None = None


class TemarioRequest(BaseModel):
    contenido_texto: str = Field(min_length=1)
    origen: str = "MANUAL"
    estado: str = "PENDIENTE_REVISION"
    fuente_url: str | None = None
    fuente_publicacion_id: int | None = None
    observaciones: str | None = None


class TemaRequest(BaseModel):
    numero: int | None = None
    titulo: str | None = None
    contenido_texto: str = Field(min_length=1)


class TemasRequest(BaseModel):
    temas: list[TemaRequest]


def _admin_empleo(usuario: UsuarioAutenticado = Depends(usuario_actual)) -> UsuarioAutenticado:
    """Autoriza Empleo contra el registro administrativo central de Tu Coach."""
    database_url = os.getenv("TUCOACH_DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(
            status_code=503,
            detail="Autorización administrativa central no configurada.",
        )

    try:
        with psycopg.connect(database_url, connect_timeout=10) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT activo
                    FROM public.admin_users
                    WHERE user_id = %s
                    LIMIT 1
                    """,
                    (usuario.id,),
                )
                admin = cursor.fetchone()
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se ha podido verificar la autorización administrativa.",
        ) from exc

    if not admin or not bool(admin["activo"]):
        raise HTTPException(status_code=403, detail="No autorizado para la gestión de Empleo.")
    return usuario


def listar_pendientes_revision(limite: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id, p.organismo_id, o.nombre AS organismo_nombre,
                   p.codigo_externo, p.denominacion, p.grupo, p.tipo_proceso,
                   p.sistema_selectivo, p.turno, p.plazas, p.estado,
                   p.origen_dato, p.revision_estado, p.fecha_apertura,
                   p.fecha_cierre, p.fecha_examen, p.updated_at
            FROM procesos p
            LEFT JOIN organismos o ON o.id = p.organismo_id
            WHERE p.revision_estado = 'PENDIENTE_REVISION'
            ORDER BY p.updated_at DESC
            LIMIT %s
            """,
            (limite,),
        )
        return cursor.fetchall()

