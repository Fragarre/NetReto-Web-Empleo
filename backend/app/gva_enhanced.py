from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from . import gva_clean as base
from .database import get_connection

_BASE_PARSEAR_DETALLE = base.parsear_detalle

TIPOS_INCLUIDOS = {
    "oposicion",
    "concurso-oposicion",
    "concurso oposicion",
    "bolsa de trabajo",
    "bolsa de empleo",
    "proceso selectivo",
    "seleccion",
    "selección",
}

PATRONES_BOLSA_NO_OPORTUNIDAD = (
    "consulta de baremo y posicion",
    "consulta de estados voluntarios",
    "consulta de estado voluntario",
    "inscripcion en las listas extraordinarias",
)


def _sin(s: str) -> str:
    return base._sin_acentos(s or "")


def _tipo_convocatoria(titulo: str) -> str:
    n = _sin(titulo).lower()
    if any(p in n for p in PATRONES_BOLSA_NO_OPORTUNIDAD):
        return "Consulta administrativa"
    return base._tipo_convocatoria(titulo)


def _turno(titulo: str) -> str | None:
    return base._turno(titulo)


def _es_incluido(titulo: str) -> bool:
    n = _sin(titulo).lower()
    if any(p in n for p in PATRONES_BOLSA_NO_OPORTUNIDAD):
        return False
    return base._es_incluido(titulo)


def _resolver_organismo(proceso: dict[str, Any]) -> tuple[int | None, str | None, str | None]:
    texto = " ".join(
        str(proceso.get(k) or "")
        for k in ("denominacion", "organismo", "organismo_texto", "datos_json")
    )
    n = _sin(texto).lower()
    if "generalitat valenciana" in n or "conselleria" in n:
        return base.GVA_ORGANISMO_ID, "generalitat_valenciana", None
    organismo_enlace = proceso.get("organismo_enlace")
    if organismo_enlace:
        host = urlparse(str(organismo_enlace)).netloc.lower()
        if host.endswith("gva.es"):
            return base.GVA_ORGANISMO_ID, "generalitat_valenciana", organismo_enlace
    return base.GVA_ORGANISMO_ID, "generalitat_valenciana", organismo_enlace


def parsear_detalle(url: str, html: str, id_emp: int) -> dict[str, Any]:
    proceso = _BASE_PARSEAR_DETALLE(url, html, id_emp)
    titulo = str(proceso.get("denominacion") or "")
    proceso["tipo_proceso"] = _tipo_convocatoria(titulo)
    proceso["turno"] = _turno(titulo)

    organismo_id, motivo, organismo_enlace = _resolver_organismo(proceso)
    organismo_texto = str(proceso.get("organismo") or proceso.get("datos_json", {}).get("organismo_detectado") or "")
    estado_etapa = proceso.get("estado")
    etapa_actual = proceso.get("etapa_actual")
    fecha_etapa = proceso.get("fecha_etapa")

    proceso["organismo_id"] = organismo_id
    if estado_etapa:
        proceso["estado"] = estado_etapa
    if fecha_etapa and etapa_actual and "base" in base._sin_acentos(etapa_actual):
        proceso["fecha_convocatoria"] = fecha_etapa

    proceso["datos_json"] = {
        **(proceso.get("datos_json") or {}),
        "organismo_detectado": organismo_texto,
        "organismo_enlace": organismo_enlace,
        "organismo_id_resuelto": organismo_id,
        "organismo_motivo": motivo,
        "etapa_actual": etapa_actual,
        "etapa_actual_fecha_publicacion": fecha_etapa.isoformat() if fecha_etapa else None,
    }
    return proceso


base._tipo_convocatoria = _tipo_convocatoria
base._turno = _turno
base._es_incluido = _es_incluido
base.parsear_detalle = parsear_detalle
importar_gva_robusto = base.importar_gva_robusto


def limpiar_gva_navegacion() -> dict[str, int]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM procesos WHERE organismo_id=%s AND denominacion='Navegación' AND datos_json->>'organismo_detectado'='Navegación'",
                (base.GVA_ORGANISMO_ID,),
            )
            ids = [row[0] for row in cursor.fetchall()]
            if not ids:
                connection.commit()
                return {"procesos_eliminados": 0, "publicaciones_eliminadas": 0, "cambios_eliminados": 0}
            cursor.execute("DELETE FROM cambios WHERE proceso_id=ANY(%s)", (ids,))
            cambios = cursor.rowcount
            cursor.execute("DELETE FROM publicaciones WHERE proceso_id=ANY(%s)", (ids,))
            publicaciones = cursor.rowcount
            cursor.execute("DELETE FROM procesos WHERE id=ANY(%s)", (ids,))
            procesos = cursor.rowcount
            connection.commit()
    return {"procesos_eliminados": procesos, "publicaciones_eliminadas": publicaciones, "cambios_eliminados": cambios}
