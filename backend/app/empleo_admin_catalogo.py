from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import hmac
import os
from typing import Any

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


@router.post("/import/ayuntamiento-valencia")
def importar_ayuntamiento_valencia_admin(
    x_import_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Importa las oposiciones administrativas del Ayuntamiento de València."""
    _validar_import_secret(x_import_secret)
    try:
        stats = importar_ayuntamiento_valencia()
        if stats.get("administrativos", 0) > 0:
            with get_connection() as connection, connection.cursor() as cursor:
                cursor.execute("UPDATE organismos SET activo=TRUE, updated_at=NOW() WHERE id=3")
                connection.commit()
        return stats
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en importación Ayuntamiento de València: {exc}") from exc


@router.post("/import/bop-valencia-tramo")
def importar_bop_valencia_tramo(
    hasta: date = Query(..., description="Último día del tramo, formato YYYY-MM-DD"),
    dias: int = Query(default=30, ge=1, le=45),
    x_import_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Importa un tramo histórico acotado del BOP de Valencia.

    Permite recorrer históricos largos mediante peticiones cortas para evitar
    timeouts del proxy/cliente. Es idempotente: las publicaciones ya existentes
    no se duplican.
    """
    _validar_import_secret(x_import_secret)
    desde = hasta - timedelta(days=dias - 1)

    def descubrir_tramo(client, historico: bool = False, dias: int = 1):
        fechas = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
        resultados: list[dict[str, Any]] = []
        vistos: set[str] = set()

        def obtener(fecha: date):
            import httpx
            headers = {
                "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
                "Accept-Language": "es-ES,es;q=0.9",
            }
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
