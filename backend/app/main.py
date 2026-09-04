from typing import Any
import hmac
import os

from fastapi import FastAPI, Header, HTTPException, Query

from .database import get_connection
from .gva_fix import importar_gva_robusto
from .organismos import listar_fuentes, listar_organismos, obtener_organismo
from .procesos import listar_procesos, obtener_proceso

app = FastAPI(title="NetReto Empleo API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "netreto-empleo"}


@app.get("/organismos")
def organismos(solo_activos: bool = Query(True)) -> list[dict[str, Any]]:
    return listar_organismos(solo_activos=solo_activos)


@app.get("/organismos/{organismo_id}")
def organismo(organismo_id: int) -> dict[str, Any]:
    resultado = obtener_organismo(organismo_id)
    if resultado is None:
        raise HTTPException(status_code=404, detail="Organismo no encontrado")
    return resultado


@app.get("/fuentes")
def fuentes(
    organismo_id: int | None = Query(None),
    solo_activas: bool = Query(True),
) -> list[dict[str, Any]]:
    return listar_fuentes(organismo_id=organismo_id, solo_activas=solo_activas)


@app.get("/procesos")
def procesos(
    organismo_id: int | None = Query(default=None),
    estado: str | None = Query(default=None),
    limite: int = Query(default=100, ge=1, le=200),
) -> list[dict[str, Any]]:
    return listar_procesos(
        organismo_id=organismo_id,
        estado=estado,
        limite=limite,
    )


@app.get("/procesos/{proceso_id}")
def proceso(proceso_id: int) -> dict[str, Any]:
    resultado = obtener_proceso(proceso_id)
    if resultado is None:
        raise HTTPException(status_code=404, detail="Proceso no encontrado")
    return resultado


@app.get("/admin/debug/publicaciones")
def debug_publicaciones(
    x_import_secret: str | None = Header(default=None),
    proceso_id: int = Query(..., ge=1),
) -> list[dict[str, Any]]:
    """Consulta temporal protegida para revisar publicaciones durante las pruebas."""
    secreto = os.getenv("EMPLOYMENT_IMPORT_SECRET")
    if not secreto or not x_import_secret or not hmac.compare_digest(x_import_secret, secreto):
        raise HTTPException(status_code=403, detail="No autorizado")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, proceso_id, fuente_id, referencia, tipo, titulo,
                       fecha_publicacion, url, contenido_hash, detectada_at
                FROM publicaciones
                WHERE proceso_id = %s
                ORDER BY id
            """, (proceso_id,))
            columnas = ["id", "proceso_id", "fuente_id", "referencia", "tipo", "titulo", "fecha_publicacion", "url", "contenido_hash", "detectada_at"]
            return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]


@app.post("/admin/debug/limpiar-publicaciones")
def debug_limpiar_publicaciones(
    x_import_secret: str | None = Header(default=None),
    ids: str = Query(...),
) -> dict[str, Any]:
    """Eliminación temporal y explícita de filas de publicaciones de prueba."""
    secreto = os.getenv("EMPLOYMENT_IMPORT_SECRET")
    if not secreto or not x_import_secret or not hmac.compare_digest(x_import_secret, secreto):
        raise HTTPException(status_code=403, detail="No autorizado")
    try:
        ids_lista = [int(valor.strip()) for valor in ids.split(",") if valor.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="ids debe ser una lista de enteros") from exc
    if not ids_lista:
        raise HTTPException(status_code=400, detail="Debe indicar al menos un id")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM publicaciones WHERE id = ANY(%s) ORDER BY id", (ids_lista,))
            encontrados = [fila[0] for fila in cursor.fetchall()]
            if encontrados != sorted(set(ids_lista)):
                raise HTTPException(status_code=404, detail={"encontrados": encontrados, "solicitados": sorted(set(ids_lista))})
            cursor.execute("SELECT DISTINCT publicacion_id FROM cambios WHERE publicacion_id = ANY(%s)", (ids_lista,))
            vinculados = [fila[0] for fila in cursor.fetchall()]
            if vinculados:
                raise HTTPException(status_code=409, detail={"publicaciones_con_cambios": vinculados})
            cursor.execute("DELETE FROM publicaciones WHERE id = ANY(%s) RETURNING id", (ids_lista,))
            eliminados = [fila[0] for fila in cursor.fetchall()]
        connection.commit()
    return {"eliminados": eliminados}


@app.post("/admin/import/gva")
def importar_gva_endpoint(
    x_import_secret: str | None = Header(default=None),
    max_paginas: int = Query(default=1, ge=1, le=10),
    max_detalles: int | None = Query(default=10, ge=1, le=100),
) -> dict[str, Any]:
    """Importación manual protegida por un secreto de administración."""
    secreto = os.getenv("EMPLOYMENT_IMPORT_SECRET")
    if not secreto or not x_import_secret or not hmac.compare_digest(x_import_secret, secreto):
        raise HTTPException(status_code=403, detail="No autorizado")

    try:
        return importar_gva_robusto(max_paginas=max_paginas, max_detalles=max_detalles)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en importación GVA: {exc}") from exc
