from typing import Any

from fastapi import FastAPI, HTTPException, Query

from .organismos import listar_fuentes, listar_organismos, obtener_organismo

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
