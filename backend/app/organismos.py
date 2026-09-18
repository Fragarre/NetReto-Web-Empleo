from __future__ import annotations

import unicodedata
from typing import Any

from psycopg.rows import dict_row

from .database import get_connection


def _normalizar_identidad(value: str | None) -> str:
    texto = " ".join((value or "").strip().lower().split())
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def resolver_fuente(
    cursor,
    *,
    nombre: str,
    tipo: str,
    organismo_id: int | None = None,
    solo_activa: bool = True,
) -> dict[str, Any]:
    """Resuelve una fuente por identidad funcional, nunca por un ID histórico."""
    conditions = ["LOWER(TRIM(nombre)) = LOWER(TRIM(%s))", "UPPER(tipo) = UPPER(%s)"]
    params: list[Any] = [nombre, tipo]
    if organismo_id is None:
        conditions.append("organismo_id IS NULL")
    else:
        conditions.append("organismo_id = %s")
        params.append(organismo_id)
    if solo_activa:
        conditions.append("activa = TRUE")

    cursor.execute(
        f"""
        SELECT id, organismo_id, nombre, tipo, url, prioridad, activa
        FROM fuentes
        WHERE {" AND ".join(conditions)}
        ORDER BY id
        """,
        tuple(params),
    )
    rows = cursor.fetchall()
    if len(rows) != 1:
        estado = "no encontrada" if not rows else "ambigua"
        raise RuntimeError(f"Fuente {estado}: {nombre!r} / {tipo!r}")
    return dict(rows[0])


def resolver_organismo(
    cursor,
    *,
    tipo: str,
    provincia: str | None,
    municipio: str | None = None,
    nombre: str | None = None,
    solo_activo: bool = True,
) -> dict[str, Any] | None:
    """Resuelve sin enlazar automáticamente identidades ambiguas."""
    cursor.execute(
        """
        SELECT id, nombre, tipo, provincia, municipio, activo
        FROM organismos
        WHERE UPPER(tipo) = UPPER(%s)
        ORDER BY id
        """,
        (tipo,),
    )
    candidatos = []
    provincia_norm = _normalizar_identidad(provincia)
    municipio_norm = _normalizar_identidad(municipio)
    nombre_norm = _normalizar_identidad(nombre)
    for row in cursor.fetchall():
        item = dict(row)
        if solo_activo and not item["activo"]:
            continue
        if _normalizar_identidad(item.get("provincia")) != provincia_norm:
            continue
        if municipio is not None:
            if _normalizar_identidad(item.get("municipio")) != municipio_norm:
                continue
        elif nombre is not None:
            if _normalizar_identidad(item.get("nombre")) != nombre_norm:
                continue
        else:
            raise ValueError("resolver_organismo requiere municipio o nombre")
        candidatos.append(item)

    if not candidatos:
        return None
    if len(candidatos) > 1:
        raise RuntimeError(
            f"Organismo ambiguo: tipo={tipo!r}, provincia={provincia!r}, "
            f"municipio={municipio!r}, nombre={nombre!r}"
        )
    return candidatos[0]


def listar_organismos(*, solo_activos: bool = True) -> list[dict[str, Any]]:
    query = """
        SELECT id, nombre, tipo, provincia, municipio, activo,
               created_at, updated_at
        FROM organismos
    """
    params: tuple[Any, ...] = ()
    if solo_activos:
        query += " WHERE activo = %s"
        params = (True,)
    query += " ORDER BY nombre"

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    return [dict(zip(columns, row)) for row in rows]


def obtener_organismo(organismo_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, nombre, tipo, provincia, municipio, activo,
                       created_at, updated_at
                FROM organismos
                WHERE id = %s
                """,
                (organismo_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [description.name for description in cursor.description]

    return dict(zip(columns, row))


def listar_fuentes(*, organismo_id: int | None = None, solo_activas: bool = True) -> list[dict[str, Any]]:
    query = """
        SELECT f.id, f.organismo_id, f.nombre, f.tipo, f.url,
               f.prioridad, f.activa, f.created_at, f.updated_at,
               o.nombre AS organismo_nombre
        FROM fuentes f
        LEFT JOIN organismos o ON o.id = f.organismo_id
    """
    conditions: list[str] = []
    params: list[Any] = []

    if solo_activas:
        conditions.append("f.activa = %s")
        params.append(True)
    if organismo_id is not None:
        conditions.append("f.organismo_id = %s")
        params.append(organismo_id)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY f.prioridad, f.nombre"

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    return [dict(zip(columns, row)) for row in rows]
