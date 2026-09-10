from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import hmac
import os
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from psycopg.rows import dict_row

from auth import UsuarioAutenticado
from .database import get_connection
from . import empleo_admin as _empleo_admin
from .empleo_admin import _admin_empleo
from .temario_extractor import extraer_temario_oficial as _extraer_temario_oficial_unicode
from .ambito_administrativo import AMBITOS
from . import bop_valencia as _bop
from . import bop_valencia_patch as _bop_patch
from .ayuntamiento_valencia import importar_ayuntamiento_valencia
from .bop_valencia_municipios import descubrir_municipales_bop, importar_municipales_bop
from .boe_local_diagnostico import diagnosticar_boe_local
from .boe_local_extractor import extraer_convocatorias_boe_local
from .boe_local_import import previsualizar_importacion_boe_local

_empleo_admin.extraer_temario_oficial = _extraer_temario_oficial_unicode
router = APIRouter(prefix="/admin/gestion", tags=["admin-empleo"])


class AmbitoAdministrativoRequest(BaseModel):
    ambito_administrativo: str


def _validar_import_secret(x_import_secret: str | None) -> None:
    secreto = os.getenv("EMPLOYMENT_IMPORT_SECRET")
    if not secreto or not x_import_secret or not hmac.compare_digest(x_import_secret, secreto):
        raise HTTPException(status_code=403, detail="No autorizado")


@router.get("/convocatorias")
def admin_convocatorias(_: UsuarioAutenticado = Depends(_admin_empleo)) -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""SELECT p.id,p.organismo_id,o.nombre AS organismo_nombre,p.codigo_externo,p.denominacion,p.grupo,p.tipo_proceso,p.sistema_selectivo,p.turno,p.plazas,p.estado,p.es_oportunidad,p.ambito_administrativo,p.origen_dato,p.revision_estado,p.fecha_convocatoria,p.fecha_apertura,p.fecha_cierre,p.fecha_examen,p.lugar_examen,p.updated_at FROM procesos p LEFT JOIN organismos o ON o.id=p.organismo_id WHERE p.es_oportunidad=TRUE ORDER BY CASE p.ambito_administrativo WHEN 'REVISION' THEN 0 WHEN 'SI' THEN 1 ELSE 2 END,COALESCE(p.fecha_examen,p.fecha_convocatoria,p.updated_at) DESC NULLS LAST,p.id DESC LIMIT 300""")
        return cursor.fetchall()


@router.patch("/procesos/{proceso_id}/ambito-administrativo")
def cambiar_ambito_administrativo(proceso_id: int, payload: AmbitoAdministrativoRequest, _: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    ambito = payload.ambito_administrativo.upper().strip()
    if ambito not in AMBITOS:
        raise HTTPException(status_code=400, detail="Ámbito administrativo no válido")
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("UPDATE procesos SET ambito_administrativo=%s,updated_at=NOW() WHERE id=%s RETURNING id,ambito_administrativo", (ambito, proceso_id))
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Proceso no encontrado")
        return row


@router.post("/import/ayuntamiento-valencia")
def importar_ayuntamiento_valencia_admin(x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try:
        stats = importar_ayuntamiento_valencia()
        if stats.get("administrativos", 0) > 0:
            with get_connection() as connection, connection.cursor() as cursor:
                cursor.execute("UPDATE organismos SET activo=TRUE,updated_at=NOW() WHERE id=3")
                connection.commit()
        return stats
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en importación Ayuntamiento de València: {exc}") from exc


@router.post("/diagnostico/bop-municipios")
def diagnostico_bop_municipios(hasta: date = Query(...), dias: int = Query(default=30, ge=1, le=45), x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try:
        return descubrir_municipales_bop(hasta=hasta, dias=dias)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en diagnóstico municipal BOP: {exc}") from exc


@router.post("/diagnostico/bop-municipios-crudo")
def diagnostico_bop_municipios_crudo(fecha: date = Query(...), buscar: str | None = Query(default=None), x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """SOLO LECTURA. Devuelve registros crudos y metadatos de paginación del BOP de una fecha."""
    _validar_import_secret(x_import_secret)
    headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
    try:
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
            _, html, error = _bop_patch._obtener_pagina(client, fecha)
        if error or not html:
            return {"fecha": fecha.isoformat(), "error": error or "Sin HTML", "html_len": len(html or ""), "registros_totales": 0, "municipales": 0, "coincidencias": []}

        soup = BeautifulSoup(html, "html.parser")
        texto = _bop._norm(soup.get_text(" ", strip=True))
        patron = re.compile(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d+)", re.I)
        registros = []
        for mr in patron.finditer(texto):
            inicio = max(texto.rfind("Anunci", 0, mr.start()), texto.rfind("Anuncio", 0, mr.start()))
            if inicio < 0:
                continue
            titulo = _bop._norm(texto[inicio:mr.start()]).rstrip(".") + "."
            n = _bop._sin(titulo)
            municipal = bool(re.search(r"\b(?:ajuntament|ayuntamiento)\b", n, re.I))
            registros.append({"registro": mr.group(1), "titulo": titulo[:1200], "municipal": municipal, "url": f"{_bop.DOWNLOAD_URL}?anuncioNumReg={mr.group(1)}&lang=es"})

        paginadores = []
        for tag in soup.find_all(True):
            ident = tag.get("id") or ""
            clases = " ".join(tag.get("class") or [])
            descriptor = f"{ident} {clases}".lower()
            if "paginator" in descriptor or "pagination" in descriptor:
                paginadores.append({
                    "tag": tag.name,
                    "id": ident or None,
                    "class": clases or None,
                    "texto": _bop._norm(tag.get_text(" ", strip=True))[:500],
                    "atributos": {k: str(v)[:300] for k, v in tag.attrs.items() if k in {"id", "class", "data-p", "data-widget", "role"}},
                })

        scripts_paginacion = []
        for script in soup.find_all("script"):
            contenido = script.string or script.get_text(" ", strip=True)
            bajo = contenido.lower()
            if any(x in bajo for x in ("paginator", "edictos", "rows", "first")):
                scripts_paginacion.append(_bop._norm(contenido)[:2500])

        consulta = _bop._sin(buscar or "").strip()
        coincidencias = [r for r in registros if not consulta or consulta in _bop._sin(r["titulo"])]
        return {
            "fecha": fecha.isoformat(),
            "error": None,
            "html_len": len(html),
            "registros_totales": len(registros),
            "municipales": sum(1 for r in registros if r["municipal"]),
            "buscar": buscar,
            "coincidencias": coincidencias[:100],
            "paginadores": paginadores[:20],
            "scripts_paginacion": scripts_paginacion[:20],
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en diagnóstico BOP municipal crudo: {exc}") from exc


@router.post("/diagnostico/boe-local")
def diagnostico_boe_local(hasta: date = Query(...), dias: int = Query(default=30, ge=1, le=45), x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """Diagnóstico de convocatorias administrativas locales CV desde BOE. SOLO LECTURA."""
    _validar_import_secret(x_import_secret)
    try:
        return diagnosticar_boe_local(hasta=hasta, dias=dias)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en diagnóstico BOE local: {exc}") from exc


@router.post("/diagnostico/boe-local-estructurado")
def diagnostico_boe_local_estructurado(hasta: date = Query(...), dias: int = Query(default=30, ge=1, le=45), x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """Previsualiza convocatorias locales CV estructuradas desde BOE. SOLO LECTURA."""
    _validar_import_secret(x_import_secret)
    try:
        return extraer_convocatorias_boe_local(hasta=hasta, dias=dias)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en extracción estructurada BOE local: {exc}") from exc


@router.post("/import/boe-local")
def previsualizar_importacion_boe_local_admin(hasta: date = Query(...), dias: int = Query(default=30, ge=1, le=45), aplicar: bool = Query(default=False), x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """Previsualiza por defecto; solo escribe convocatorias BOE nuevas seguras con aplicar=true."""
    _validar_import_secret(x_import_secret)
    try:
        return previsualizar_importacion_boe_local(hasta=hasta, dias=dias, aplicar=aplicar)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en importación BOE local: {exc}") from exc


@router.post("/import/bop-municipios")
def importar_bop_municipios_admin(hasta: date = Query(...), dias: int = Query(default=30, ge=1, le=45), aplicar: bool = Query(default=False), x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """Previsualiza por defecto; solo escribe con aplicar=true."""
    _validar_import_secret(x_import_secret)
    try:
        return importar_municipales_bop(hasta=hasta, dias=dias, aplicar=aplicar)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en importación municipal BOP: {exc}") from exc


@router.post("/import/bop-valencia-tramo")
def importar_bop_valencia_tramo(hasta: date = Query(...), dias: int = Query(default=30, ge=1, le=45), x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    desde = hasta - timedelta(days=dias - 1)

    def descubrir_tramo(client, historico: bool = False, dias: int = 1):
        fechas = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
        resultados = []
        vistos = set()

        def obtener(fecha: date):
            headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
            with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as c:
                return _bop_patch._obtener_pagina(c, fecha)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(obtener, fecha) for fecha in fechas]
            for future in as_completed(futures):
                _, html, _ = future.result()
                if not html:
                    continue
                for anuncio in _bop_patch._extraer_anuncios_pagina(html):
                    if anuncio["registro"] not in vistos:
                        vistos.add(anuncio["registro"])
                        resultados.append(anuncio)
        resultados.sort(key=lambda x: (x["fecha_publicacion"] or date.min, x["registro"]))
        return resultados

    original = _bop.descubrir_anuncios
    try:
        _bop.descubrir_anuncios = descubrir_tramo
        stats = _bop.importar_bop_valencia(historico=True, dias=dias)
        clasificados, cambios_tecnicos = _bop_patch._postprocesar_ambito_y_cambios()
        stats["ambito_administrativo_actualizados"] = clasificados
        stats["cambios_tecnicos_desactivados"] = cambios_tecnicos
        stats["tramo_desde"] = desde.isoformat()
        stats["tramo_hasta"] = hasta.isoformat()
        return stats
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en importación BOP Valencia por tramo: {exc}") from exc
    finally:
        _bop.descubrir_anuncios = original
