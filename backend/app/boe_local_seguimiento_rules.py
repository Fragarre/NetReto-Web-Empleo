from __future__ import annotations

import re
import unicodedata
from typing import Any

from . import bop_valencia_municipios as _municipios


_BUSCAR_PROCESO_ORIGINAL = _municipios._buscar_proceso_seguimiento


_STOPWORDS = {
    "administracion", "administrativa", "administrativo", "administrativa", "personal",
    "plaza", "plazas", "proces", "proceso", "selectiu", "selectivo", "seleccion",
    "ajuntament", "ayuntamiento", "convocatoria", "cobertura", "propiedad", "general",
    "escala", "subescala", "turno", "torn", "libre", "lliure", "persona", "personas",
    "tecnico", "tecnica", "tecnic", "tecnica", "auxiliar", "grupo", "subgrupo",
}


def _sin(texto: str | None) -> str:
    valor = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in valor if unicodedata.category(c) != "Mn")


def _palabras_significativas(texto: str | None) -> set[str]:
    palabras = set(re.findall(r"[a-z0-9]+", _sin(texto)))
    return {p for p in palabras if len(p) >= 4 and p not in _STOPWORDS}


def _mismo_municipio(hallazgo: dict[str, Any], organismo: dict[str, Any]) -> bool:
    municipio = _sin(hallazgo.get("municipio_detectado")).strip()
    if not municipio:
        return False
    candidatos = {
        _sin(organismo.get("municipio")).strip(),
        _sin(organismo.get("nombre")).replace("ayuntamiento de ", "").replace("ajuntament de ", "").strip(),
    }
    return municipio in candidatos


def _turno_compatible(titulo: str, turno: str | None) -> bool | None:
    nt = _sin(titulo)
    np = _sin(turno)
    titulo_discapacidad = any(x in nt for x in ("discapacidad", "diversidad funcional", "diversitat funcional"))
    proceso_discapacidad = any(x in np for x in ("discapacidad", "diversidad funcional", "diversitat funcional"))
    titulo_libre = bool(re.search(r"\b(turno|torn)\s+(libre|lliure)\b", nt))
    proceso_libre = "libre" in np or "lliure" in np

    if titulo_discapacidad:
        return proceso_discapacidad
    if titulo_libre:
        return proceso_libre and not proceso_discapacidad
    return None


def _candidatos_boe_local(cursor, hallazgo: dict[str, Any]) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT p.id,p.identificador_estable,p.denominacion,p.turno,p.plazas,p.estado,p.datos_json,
               o.nombre AS organismo_nombre,o.municipio AS organismo_municipio
        FROM procesos p
        JOIN organismos o ON o.id=p.organismo_id
        WHERE p.identificador_estable LIKE 'BOELOCAL:%'
          AND p.es_oportunidad=TRUE
          AND p.ambito_administrativo='SI'
          AND COALESCE(o.provincia,'')='Valencia'
        ORDER BY p.id
        """
    )
    resultado = []
    for row in cursor.fetchall():
        proceso = dict(row)
        organismo = {
            "nombre": proceso.pop("organismo_nombre", None),
            "municipio": proceso.pop("organismo_municipio", None),
        }
        if _mismo_municipio(hallazgo, organismo):
            resultado.append(proceso)
    return resultado


def _elegir_por_contenido(candidatos: list[dict[str, Any]], hallazgo: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if not candidatos:
        return None, "SIN_PROCESO_BOE_LOCAL_MUNICIPIO"

    titulo = hallazgo.get("titulo") or ""

    # Cuando las bases originaron varios procesos en el mismo BOE (p. ej. libre y discapacidad),
    # el turno explícito permite resolverlos sin depender solo de la denominación.
    compatibles_turno = []
    hubo_turno_explicito = False
    for proceso in candidatos:
        compatible = _turno_compatible(titulo, proceso.get("turno"))
        if compatible is not None:
            hubo_turno_explicito = True
            if compatible:
                compatibles_turno.append(proceso)
    if hubo_turno_explicito:
        if len(compatibles_turno) == 1:
            return compatibles_turno[0], "BOE_LOCAL_MUNICIPIO_TURNO"
        if not compatibles_turno:
            return None, "BOE_LOCAL_TURNO_INCOMPATIBLE"
        candidatos = compatibles_turno

    if len(candidatos) == 1:
        return candidatos[0], "BOE_LOCAL_MUNICIPIO_UNICO"

    palabras_titulo = _palabras_significativas(titulo)
    puntuados: list[tuple[int, dict[str, Any]]] = []
    for proceso in candidatos:
        palabras_proceso = _palabras_significativas(proceso.get("denominacion"))
        puntuacion = len(palabras_titulo & palabras_proceso)
        puntuados.append((puntuacion, proceso))

    puntuados.sort(key=lambda x: x[0], reverse=True)
    if puntuados and puntuados[0][0] > 0:
        mejores = [p for puntos, p in puntuados if puntos == puntuados[0][0]]
        if len(mejores) == 1:
            return mejores[0], "BOE_LOCAL_MUNICIPIO_DENOMINACION"

    # Seguridad: si no hay evidencia suficiente, no se vincula automáticamente.
    return None, "BOE_LOCAL_AMBIGUO"


def buscar_proceso_seguimiento_extendido(cursor, hallazgo: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    proceso, motivo = _BUSCAR_PROCESO_ORIGINAL(cursor, hallazgo)
    if proceso is not None:
        return proceso, motivo

    if not _municipios._es_seguimiento_selectivo_claro(hallazgo.get("titulo") or ""):
        return None, motivo

    candidatos = _candidatos_boe_local(cursor, hallazgo)
    proceso_boe, motivo_boe = _elegir_por_contenido(candidatos, hallazgo)
    if proceso_boe is not None:
        return proceso_boe, motivo_boe
    return None, motivo_boe if candidatos else motivo


def aplicar_reglas_seguimiento_boe_local() -> None:
    _municipios._buscar_proceso_seguimiento = buscar_proceso_seguimiento_extendido
