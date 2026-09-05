from typing import Any
import hmac
import os

from fastapi import FastAPI, Header, HTTPException, Query

from .bop_valencia_patch import diagnosticar_bop, importar_bop_valencia
from .bop_valencia_cleanup import limpiar_anuncios_no_empleo, normalizar_bop_prueba
from .gva_enhanced import importar_gva_robusto, limpiar_gva_navegacion
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
def fuentes(organismo_id: int | None = Query(None), solo_activas: bool = Query(True)) -> list[dict[str, Any]]:
    return listar_fuentes(organismo_id=organismo_id, solo_activas=solo_activas)

@app.get("/procesos")
def procesos(organismo_id: int | None = Query(default=None), estado: str | None = Query(default=None), limite: int = Query(default=100, ge=1, le=200)) -> list[dict[str, Any]]:
    return listar_procesos(organismo_id=organismo_id, estado=estado, limite=limite)

@app.get("/procesos/{proceso_id}")
def proceso(proceso_id: int) -> dict[str, Any]:
    resultado = obtener_proceso(proceso_id)
    if resultado is None:
        raise HTTPException(status_code=404, detail="Proceso no encontrado")
    return resultado

def _validar_import_secret(x_import_secret: str | None) -> None:
    secreto = os.getenv("EMPLOYMENT_IMPORT_SECRET")
    if not secreto or not x_import_secret or not hmac.compare_digest(x_import_secret, secreto):
        raise HTTPException(status_code=403, detail="No autorizado")

@app.post("/admin/import/gva")
def importar_gva_endpoint(
    x_import_secret: str | None = Header(default=None),
    max_paginas: int = Query(default=3, ge=1, le=10),
    max_detalles: int | None = Query(default=None, ge=1, le=300),
) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try:
        return importar_gva_robusto(max_paginas=max_paginas, max_detalles=max_detalles)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en importación GVA: {exc}") from exc

@app.post("/admin/cleanup/gva-navegacion")
def cleanup_gva_navegacion(x_import_secret: str | None = Header(default=None)) -> dict[str, int]:
    _validar_import_secret(x_import_secret)
    try:
        return limpiar_gva_navegacion()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en limpieza GVA: {exc}") from exc

@app.get("/admin/debug/bop")
def debug_bop(x_import_secret: str | None = Header(default=None), fecha: str | None = Query(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try:
        import httpx
        headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
            return diagnosticar_bop(client, fecha=fecha)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en diagnóstico BOP: {exc}") from exc

@app.post("/admin/import/bop-valencia")
def importar_bop_valencia_endpoint(x_import_secret: str | None = Header(default=None), historico: bool = Query(default=False), dias: int = Query(default=250, ge=1, le=730)) -> dict[str, Any]:
    """Importa los anuncios de empleo de la Diputación publicados en el BOP."""
    _validar_import_secret(x_import_secret)
    try:
        return importar_bop_valencia(historico=historico, dias=dias)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en importación BOP Valencia: {exc}") from exc

@app.post("/admin/cleanup/bop-valencia-no-empleo")
def cleanup_bop_valencia_no_empleo(x_import_secret: str | None = Header(default=None)) -> dict[str, int]:
    _validar_import_secret(x_import_secret)
    try:
        return limpiar_anuncios_no_empleo()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en limpieza BOP Valencia: {exc}") from exc

@app.post("/admin/cleanup/bop-valencia-normalizar-prueba")
def cleanup_bop_valencia_normalizar_prueba(x_import_secret: str | None = Header(default=None)) -> dict[str, int]:
    _validar_import_secret(x_import_secret)
    try:
        return normalizar_bop_prueba()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en normalización BOP Valencia: {exc}") from exc
