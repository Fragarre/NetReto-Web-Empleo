from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from typing import Any

from psycopg.rows import dict_row
from xml.sax.saxutils import escape

import httpx

from .ambito_administrativo import clasificar_ambito_administrativo
from .database import get_connection
from .bop_valencia_municipios import (
    _clasificar_anuncio,
    _es_seguimiento_selectivo_claro,
    _extraer_codigo_proceso,
    _familia_perfil,
    _sin as _sin_valencia,
)

ENDPOINT = (
    "https://sede.diputacionalicante.es/wp-content/themes/"
    "Desarrollo-Diputacion/webservices/wseConsultaAjax.php"
)


def _valor(registro: dict[str, Any], campo: str) -> str:
    valor = registro.get(campo)
    if isinstance(valor, list):
        return str(valor[0]).strip() if valor else ""
    return str(valor or "").strip()


def _param(desde: date, hasta: date) -> str:
    # Contrato real observado en DevTools: param es XML.
    return (
        "<raiz><entrada><registro>"
        f"<desde>{escape(desde.strftime('%d/%m/%Y'))}</desde>"
        f"<hasta>{escape(hasta.strftime('%d/%m/%Y'))}</hasta>"
        "<texto></texto>"
        "<tipoorganismo>4</tipoorganismo>"
        "<publicante></publicante>"
        "</registro></entrada></raiz>"
    )


def _normalizar(registro: dict[str, Any]) -> dict[str, Any]:
    extracto = _valor(registro, "extracto")
    definicion = _valor(registro, "definicion")
    denominacion = _valor(registro, "denominacion")
    publicante = _valor(registro, "ampliacion") or definicion
    edicto = _valor(registro, "edicto")
    anyo = _valor(registro, "anyo")
    numero_bop = _valor(registro, "nBop")
    ubicacion = _valor(registro, "ubicacion")

    texto_clasificacion = " ".join(x for x in (extracto, definicion, denominacion, publicante) if x)
    return {
        "referencia": f"BOPAL:{anyo}:{edicto}" if anyo and edicto else "",
        "anyo": anyo,
        "numero_bop": numero_bop,
        "fecha_publicacion": _valor(registro, "fechaPublica"),
        "edicto": edicto,
        "extracto": extracto,
        "organismo": publicante,
        "denominacion": denominacion,
        "seccion": _valor(registro, "desecun"),
        "url_documento": ubicacion,
        "ambito_administrativo": clasificar_ambito_administrativo(
            {"denominacion": texto_clasificacion, "cuerpo_escala": None, "grupo": None}
        ),
    }



def _sin(texto: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(ch) != "Mn"
    )


def _es_candidato_empleo(registro: dict[str, Any]) -> bool:
    return registro.get("ambito_administrativo") == "SI"


def seleccionar_proceso_seguimiento(
    hallazgo: dict[str, Any],
    candidatos: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    """Matching conservador puro, equivalente al patrón BOP Valencia.

    No accede a BD: recibe candidatos ya obtenidos por la capa de persistencia.
    """
    familia = _familia_perfil(hallazgo.get("extracto") or "")
    if not familia:
        return None, "SIN_FAMILIA"
    if not _es_seguimiento_selectivo_claro(hallazgo.get("extracto") or ""):
        return None, "NO_ES_CONTINUIDAD_SELECTIVA"

    municipio = _sin_valencia(hallazgo.get("denominacion") or "")
    codigo = _extraer_codigo_proceso(hallazgo.get("extracto") or "")
    compatibles = [
        p for p in candidatos
        if _sin_valencia(p.get("municipio") or "") == municipio
        and _familia_perfil(p.get("denominacion") or "") == familia
    ]
    if not compatibles:
        return None, "SIN_COINCIDENCIA"

    if codigo:
        por_codigo = [
            p for p in compatibles
            if _extraer_codigo_proceso(p.get("denominacion") or "") == codigo
        ]
        if len(por_codigo) == 1:
            return por_codigo[0], "CODIGO_EXACTO"
        if len(por_codigo) > 1:
            return None, "CODIGO_AMBIGUO"
        return None, "CODIGO_SIN_COINCIDENCIA"

    if len(compatibles) == 1:
        return compatibles[0], "UNICO_HITO_SELECTIVO"
    return None, "AMBIGUO_SIN_CODIGO"


def consultar_bop_alicante(
    *,
    dias_solape: int = 7,
    hasta: date | None = None,
    max_items: int = 500,
) -> dict[str, Any]:
    """SOLO_REVISION del BOP Alicante: consulta, normaliza y clasifica; no usa BD."""
    hasta = hasta or date.today()
    desde = hasta - timedelta(days=max(dias_solape, 0))

    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION",
        "fuente": "Boletín Oficial de la Provincia de Alicante",
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "descubiertos": 0,
        "administrativos": 0,
        "revision": 0,
        "resumen_clases": {},
        "candidatas_nuevas": 0,
        "seguimientos": 0,
        "errores": [],
        "detalle": [],
    }

    try:
        with httpx.Client(
            timeout=45,
            follow_redirects=True,
            headers={"User-Agent": "TuCoach-Empleo/1.0", "Accept": "application/json"},
        ) as client:
            respuesta = client.get(ENDPOINT, params={"nemo": "BOP_EDI", "param": _param(desde, hasta), "usuario": "-"})
            respuesta.raise_for_status()
            payload = respuesta.json()
    except Exception as exc:
        resultado["errores"].append(f"{type(exc).__name__}: {exc}")
        return resultado

    registros = payload.get("bop", {}).get("registro", [])
    if not isinstance(registros, list):
        resultado["errores"].append("Respuesta BOP Alicante sin bop.registro[]")
        return resultado

    normalizados = [_normalizar(r) for r in registros[:max_items] if isinstance(r, dict)]
    administrativos = [r for r in normalizados if _es_candidato_empleo(r)]
    for r in administrativos:
        r["clase"] = _clasificar_anuncio(r["extracto"])

    conteo = Counter(r["clase"] for r in administrativos)
    resultado["descubiertos"] = len(normalizados)
    resultado["administrativos"] = len(administrativos)
    resultado["revision"] = sum(1 for r in administrativos if r["clase"] == "REVISION")
    resultado["resumen_clases"] = dict(sorted(conteo.items()))
    resultado["candidatas_nuevas"] = conteo.get("NUEVA_CONVOCATORIA", 0)
    resultado["seguimientos"] = conteo.get("SEGUIMIENTO", 0)
    resultado["detalle"] = administrativos
    return resultado


def _fecha_bop(valor: str | None) -> date | None:
    if not valor:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return date.fromisoformat(valor) if formato == "%Y-%m-%d" else __import__("datetime").datetime.strptime(valor, formato).date()
        except ValueError:
            pass
    return None


def preparar_importacion_bop_alicante(
    *,
    dias_solape: int = 7,
    hasta: date | None = None,
    max_items: int = 500,
) -> dict[str, Any]:
    """Prepara la persistencia usando solo lecturas de BD; nunca escribe."""
    revision = consultar_bop_alicante(
        dias_solape=dias_solape,
        hasta=hasta,
        max_items=max_items,
    )
    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION_BD",
        "fuente": revision["fuente"],
        "desde": revision["desde"],
        "hasta": revision["hasta"],
        "nuevas": 0,
        "existentes": 0,
        "seguimientos_vinculados": 0,
        "seguimientos_revision": 0,
        "excluidos": 0,
        "errores": list(revision["errores"]),
        "detalle": [],
    }
    if resultado["errores"]:
        return resultado

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        for hallazgo in revision["detalle"]:
            clase = hallazgo["clase"]
            if clase not in ("NUEVA_CONVOCATORIA", "SEGUIMIENTO"):
                resultado["excluidos"] += 1
                continue

            if clase == "NUEVA_CONVOCATORIA":
                cursor.execute(
                    "SELECT id FROM procesos WHERE identificador_estable=%s",
                    (hallazgo["referencia"],),
                )
                existente = cursor.fetchone()
                if existente:
                    resultado["existentes"] += 1
                    resultado["detalle"].append({
                        "referencia": hallazgo["referencia"],
                        "clase": clase,
                        "estado": "EXISTENTE",
                        "proceso_id": existente["id"],
                    })
                else:
                    resultado["nuevas"] += 1
                    resultado["detalle"].append({
                        "referencia": hallazgo["referencia"],
                        "clase": clase,
                        "estado": "NUEVO",
                        "municipio": hallazgo["denominacion"],
                    })
                continue

            fecha_publicacion = _fecha_bop(hallazgo.get("fecha_publicacion"))
            if fecha_publicacion is None:
                resultado["seguimientos_revision"] += 1
                resultado["detalle"].append({
                    "referencia": hallazgo["referencia"],
                    "clase": clase,
                    "vinculacion": "FECHA_INVALIDA",
                    "proceso_id": None,
                })
                continue

            cursor.execute(
                """
                SELECT p.id,p.denominacion,p.codigo_externo,p.fecha_convocatoria,o.municipio
                FROM procesos p
                JOIN organismos o ON o.id=p.organismo_id
                WHERE o.tipo='AYUNTAMIENTO'
                  AND LOWER(COALESCE(o.provincia,''))='alicante'
                  AND p.ambito_administrativo='SI'
                  AND p.estado='EN_CURSO'
                  AND p.fecha_convocatoria IS NOT NULL
                  AND p.fecha_convocatoria <= %s
                ORDER BY p.fecha_convocatoria DESC,p.id DESC
                """,
                (fecha_publicacion,),
            )
            proceso, motivo = seleccionar_proceso_seguimiento(hallazgo, list(cursor.fetchall()))
            if proceso:
                resultado["seguimientos_vinculados"] += 1
            else:
                resultado["seguimientos_revision"] += 1
            resultado["detalle"].append({
                "referencia": hallazgo["referencia"],
                "clase": clase,
                "vinculacion": motivo,
                "proceso_id": proceso["id"] if proceso else None,
            })

        connection.rollback()
    return resultado
