from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

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
    permitidos = {
        x.strip().lower()
        for x in os.getenv("EMPLOYMENT_ADMIN_EMAILS", "").split(",")
        if x.strip()
    }
    if not permitidos:
        raise HTTPException(status_code=503, detail="Centro de gestión no configurado.")
    if usuario.email.lower() not in permitidos:
        raise HTTPException(status_code=403, detail="No autorizado para la gestión de Empleo.")
    return usuario


def listar_pendientes_revision(limite: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor() as cursor:
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
        return [dict(row) for row in cursor.fetchall()]


def actualizar_revision(
    proceso_id: int,
    estado: str,
    usuario_id: str | None = None,
    observaciones: str | None = None,
) -> dict[str, Any]:
    if estado not in REVISION_ESTADOS:
        raise ValueError("Estado de revisión no válido")
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM procesos WHERE id=%s", (proceso_id,))
        if cursor.fetchone() is None:
            raise ValueError("Proceso no encontrado")
        cursor.execute(
            """
            UPDATE procesos
            SET revision_estado=%s,
                revisado_at=CASE WHEN %s IN ('PUBLICADA','DESCARTADA') THEN NOW() ELSE revisado_at END,
                revisado_por=CASE WHEN %s IN ('PUBLICADA','DESCARTADA') THEN %s::uuid ELSE revisado_por END,
                observaciones_internas=COALESCE(%s, observaciones_internas),
                updated_at=NOW()
            WHERE id=%s
            RETURNING id, revision_estado, revisado_at, revisado_por, observaciones_internas
            """,
            (estado, estado, estado, usuario_id, observaciones, proceso_id),
        )
        return dict(cursor.fetchone())


def guardar_temario(
    proceso_id: int,
    contenido_texto: str,
    origen: str = "MANUAL",
    estado: str = "PENDIENTE_REVISION",
    fuente_url: str | None = None,
    fuente_publicacion_id: int | None = None,
    observaciones: str | None = None,
) -> dict[str, Any]:
    if origen not in TEMARIO_ORIGENES:
        raise ValueError("Origen de temario no válido")
    if estado not in TEMARIO_ESTADOS:
        raise ValueError("Estado de temario no válido")
    if not contenido_texto.strip():
        raise ValueError("El temario no puede estar vacío")
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO temarios_empleo
              (proceso_id, contenido_texto, origen, estado, fuente_url,
               fuente_publicacion_id, fecha_extraccion, observaciones, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,NOW(),%s,NOW())
            ON CONFLICT (proceso_id) DO UPDATE SET
              contenido_texto=EXCLUDED.contenido_texto,
              origen=EXCLUDED.origen,
              estado=EXCLUDED.estado,
              fuente_url=EXCLUDED.fuente_url,
              fuente_publicacion_id=EXCLUDED.fuente_publicacion_id,
              fecha_extraccion=NOW(),
              observaciones=EXCLUDED.observaciones,
              updated_at=NOW()
            RETURNING *
            """,
            (proceso_id, contenido_texto.strip(), origen, estado, fuente_url,
             fuente_publicacion_id, observaciones),
        )
        return dict(cursor.fetchone())


def obtener_temario(proceso_id: int) -> dict[str, Any] | None:
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM temarios_empleo WHERE proceso_id=%s", (proceso_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        temario = dict(row)
        cursor.execute(
            "SELECT id, numero, titulo, contenido_texto, orden FROM temas_empleo WHERE temario_id=%s ORDER BY orden",
            (temario["id"],),
        )
        temario["temas"] = [dict(r) for r in cursor.fetchall()]
        return temario


def guardar_temas(proceso_id: int, temas: list[dict[str, Any]]) -> dict[str, Any]:
    temario = obtener_temario(proceso_id)
    if temario is None:
        raise ValueError("Primero debe existir el temario")
    normalizados = []
    for i, tema in enumerate(temas, start=1):
        contenido = str(tema.get("contenido_texto") or "").strip()
        if not contenido:
            raise ValueError(f"El tema {i} no puede estar vacío")
        normalizados.append((tema.get("numero"), tema.get("titulo"), contenido, i))
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM temas_empleo WHERE temario_id=%s", (temario["id"],))
        for numero, titulo, contenido, orden in normalizados:
            cursor.execute(
                "INSERT INTO temas_empleo(temario_id,numero,titulo,contenido_texto,orden) VALUES(%s,%s,%s,%s,%s)",
                (temario["id"], numero, titulo, contenido, orden),
            )
    return obtener_temario(proceso_id) or temario


def _row_proceso(cursor, proceso_id: int) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT p.*, o.nombre AS organismo_nombre
        FROM procesos p LEFT JOIN organismos o ON o.id=p.organismo_id
        WHERE p.id=%s
        """,
        (proceso_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def crear_proceso_manual(payload: ProcesoAdminRequest, usuario: UsuarioAutenticado) -> dict[str, Any]:
    if payload.revision_estado not in REVISION_ESTADOS:
        raise ValueError("Estado de revisión no válido")
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM organismos WHERE id=%s", (payload.organismo_id,))
        if cursor.fetchone() is None:
            raise ValueError("Organismo no encontrado")
        cursor.execute(
            """
            INSERT INTO procesos (
              organismo_id,codigo_externo,identificador_estable,denominacion,cuerpo_escala,
              grupo,subgrupo,tipo_proceso,sistema_selectivo,turno,plazas,estado,anio_oep,
              anio_convocatoria,fecha_convocatoria,fecha_apertura,fecha_cierre,fecha_examen,
              lugar_examen,fuente_principal_id,datos_json,es_oportunidad,origen_dato,
              revision_estado,revisado_at,revisado_por,observaciones_internas,updated_at
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'MANUAL',%s,NULL,NULL,%s,NOW()
            ) RETURNING id
            """,
            (
                payload.organismo_id,payload.codigo_externo,payload.identificador_estable,payload.denominacion,
                payload.cuerpo_escala,payload.grupo,payload.subgrupo,payload.tipo_proceso,payload.sistema_selectivo,
                payload.turno,payload.plazas,payload.estado,payload.anio_oep,payload.anio_convocatoria,
                payload.fecha_convocatoria,payload.fecha_apertura,payload.fecha_cierre,payload.fecha_examen,
                payload.lugar_examen,payload.fuente_principal_id,Jsonb(payload.datos_json or {}),payload.es_oportunidad,
                payload.revision_estado,payload.observaciones_internas,
            ),
        )
        proceso_id = cursor.fetchone()["id"]
        if payload.revision_estado in ("PUBLICADA", "DESCARTADA"):
            cursor.execute(
                "UPDATE procesos SET revisado_at=NOW(), revisado_por=%s::uuid WHERE id=%s",
                (str(usuario.id), proceso_id),
            )
        return _row_proceso(cursor, proceso_id) or {"id": proceso_id}


def actualizar_proceso_manual(proceso_id: int, payload: ProcesoAdminRequest, usuario: UsuarioAutenticado) -> dict[str, Any]:
    if payload.revision_estado not in REVISION_ESTADOS:
        raise ValueError("Estado de revisión no válido")
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM procesos WHERE id=%s", (proceso_id,))
        if cursor.fetchone() is None:
            raise ValueError("Proceso no encontrado")
        cursor.execute(
            """
            UPDATE procesos SET organismo_id=%s,codigo_externo=%s,identificador_estable=%s,denominacion=%s,
              cuerpo_escala=%s,grupo=%s,subgrupo=%s,tipo_proceso=%s,sistema_selectivo=%s,turno=%s,plazas=%s,
              estado=%s,anio_oep=%s,anio_convocatoria=%s,fecha_convocatoria=%s,fecha_apertura=%s,fecha_cierre=%s,
              fecha_examen=%s,lugar_examen=%s,fuente_principal_id=%s,datos_json=%s,es_oportunidad=%s,
              origen_dato='MANUAL',revision_estado=%s,
              revisado_at=CASE WHEN %s IN ('PUBLICADA','DESCARTADA') THEN NOW() ELSE revisado_at END,
              revisado_por=CASE WHEN %s IN ('PUBLICADA','DESCARTADA') THEN %s::uuid ELSE revisado_por END,
              observaciones_internas=%s,updated_at=NOW()
            WHERE id=%s
            """,
            (
                payload.organismo_id,payload.codigo_externo,payload.identificador_estable,payload.denominacion,
                payload.cuerpo_escala,payload.grupo,payload.subgrupo,payload.tipo_proceso,payload.sistema_selectivo,
                payload.turno,payload.plazas,payload.estado,payload.anio_oep,payload.anio_convocatoria,
                payload.fecha_convocatoria,payload.fecha_apertura,payload.fecha_cierre,payload.fecha_examen,
                payload.lugar_examen,payload.fuente_principal_id,Jsonb(payload.datos_json or {}),payload.es_oportunidad,
                payload.revision_estado,payload.revision_estado,payload.revision_estado,str(usuario.id),
                payload.observaciones_internas,proceso_id,
            ),
        )
        return _row_proceso(cursor, proceso_id) or {"id": proceso_id}


@router.get("/me")
def admin_me(usuario: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    return {"id": str(usuario.id), "email": usuario.email, "admin": True}


@router.get("/pendientes")
def admin_pendientes(limite: int = 100, _: UsuarioAutenticado = Depends(_admin_empleo)) -> list[dict[str, Any]]:
    return listar_pendientes_revision(limite=limite)


@router.get("/procesos/{proceso_id}")
def admin_proceso(proceso_id: int, _: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    with get_connection() as connection, connection.cursor() as cursor:
        row = _row_proceso(cursor, proceso_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Proceso no encontrado")
    return row


@router.post("/procesos")
def admin_crear_proceso(payload: ProcesoAdminRequest, usuario: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return crear_proceso_manual(payload, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/procesos/{proceso_id}")
def admin_actualizar_proceso(proceso_id: int, payload: ProcesoAdminRequest, usuario: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return actualizar_proceso_manual(proceso_id, payload, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/procesos/{proceso_id}/revision")
def admin_revision(proceso_id: int, payload: RevisionRequest, usuario: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return actualizar_revision(proceso_id, payload.estado, usuario_id=str(usuario.id), observaciones=payload.observaciones)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/procesos/{proceso_id}/temario")
def admin_temario(proceso_id: int, _: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    return obtener_temario(proceso_id) or {"proceso_id": proceso_id, "temario": None}


@router.put("/procesos/{proceso_id}/temario")
def admin_guardar_temario(payload: TemarioRequest, proceso_id: int, _: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return guardar_temario(proceso_id, payload.contenido_texto, origen=payload.origen, estado=payload.estado,
                               fuente_url=payload.fuente_url, fuente_publicacion_id=payload.fuente_publicacion_id,
                               observaciones=payload.observaciones)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/procesos/{proceso_id}/temario/temas")
def admin_guardar_temas(payload: TemasRequest, proceso_id: int, _: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return guardar_temas(proceso_id, [x.model_dump() for x in payload.temas])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
