from __future__ import annotations

from datetime import datetime

from .gva_estatal_source import clasificar_oportunidad

# Correspondencias verificadas entre las referencias estatales y los cuatro
# procesos GVA ya existentes en producción. Se preserva el identificador actual
# para no romper suscripciones, URLs internas ni referencias históricas.
LEGACY_ALIASES: dict[int, str] = {
    219621: "GVA:110135",  # C2-01, convocatoria 70/26
    219862: "GVA:110206",  # C1-01, convocatoria 58/26
    220197: "GVA:110235",  # C1-07, convocatoria 68/26
    220291: "GVA:110202",  # A2-05, convocatoria 50/26
}


def _fecha_iso_a_sql(valor: str | None) -> str | None:
    if not valor:
        return None
    return datetime.strptime(valor, "%d/%m/%Y").date().isoformat()


def identificador_canonico(referencia: int) -> str:
    return LEGACY_ALIASES.get(referencia, f"GVAESTATAL:{referencia}")


def construir_registro(tarjeta: dict, detalle: dict) -> dict:
    """Construye un registro compatible con `procesos` sin escribir en BD.

    Esta función es deliberadamente pura: permite validar la nueva fuente y el
    mapeo de identificadores antes de habilitar cualquier escritura.
    """
    clasificado = clasificar_oportunidad(tarjeta, detalle)
    referencia = int(clasificado["referencia"])
    codigos = clasificado.get("codigos_administrativos") or []
    codigo = codigos[0] if len(codigos) == 1 else None
    via = clasificado.get("via")

    if via == "INGRESO_LIBRE":
        tipo_proceso = "Oposición"
        turno = "TURNO_LIBRE"
    elif via == "INTERINIDAD":
        tipo_proceso = "Bolsa de trabajo"
        turno = "TURNO_LIBRE"
    elif via == "CONTRATACION_FIJA":
        tipo_proceso = "Contratación laboral indefinida"
        turno = "TURNO_LIBRE"
    else:
        tipo_proceso = None
        turno = None

    return {
        "referencia_estatal": referencia,
        "identificador_estable": identificador_canonico(referencia),
        "denominacion": clasificado.get("titulo") or f"Convocatoria estatal {referencia}",
        "cuerpo_escala": codigo,
        "grupo": codigo.split("-", 1)[0] if codigo else None,
        "tipo_proceso": tipo_proceso,
        "turno": turno,
        "estado": "EN_CURSO",
        "fecha_apertura": _fecha_iso_a_sql(clasificado.get("fecha_apertura")),
        "fecha_cierre": _fecha_iso_a_sql(clasificado.get("fecha_cierre")),
        "ambito_administrativo": clasificado.get("ambito_administrativo"),
        "es_oportunidad": bool(clasificado.get("es_oportunidad")),
        "datos_json": {
            "fuente_descubrimiento": "administracion.gob.es",
            "referencia_estatal": referencia,
            "url_estatal": clasificado.get("url"),
            "organo_estatal": clasificado.get("organo") or clasificado.get("organo_detalle"),
            "via_estatal": via,
            "codigos_administrativos": codigos,
            "fecha_publicacion_busqueda": clasificado.get("fecha_publicacion_busqueda"),
        },
    }
