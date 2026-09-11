from __future__ import annotations

from datetime import datetime

from .gva_estatal_source import clasificar_oportunidad

LEGACY_ALIASES: dict[int, str] = {
    219621: "GVA:110135",
    219862: "GVA:110206",
    220197: "GVA:110235",
    220291: "GVA:110202",
}


def _fecha_iso_a_sql(valor: str | None) -> str | None:
    if not valor:
        return None
    return datetime.strptime(valor, "%d/%m/%Y").date().isoformat()


def identificador_canonico(referencia: int) -> str:
    return LEGACY_ALIASES.get(referencia, f"GVAESTATAL:{referencia}")


def construir_registro(tarjeta: dict, detalle: dict) -> dict:
    clasificado = clasificar_oportunidad(tarjeta, detalle)
    referencia = int(clasificado["referencia"])
    codigos = clasificado.get("codigos_administrativos") or []
    codigo = codigos[0] if len(codigos) == 1 else None
    via = clasificado.get("via")
    es_legacy = referencia in LEGACY_ALIASES

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
        "preservar_campos_existentes": es_legacy,
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
        "organismo_gva": clasificado.get("organismo_gva"),
        "datos_json": {
            "fuente_descubrimiento": "administracion.gob.es",
            "referencia_estatal": referencia,
            "url_estatal": clasificado.get("url"),
            "organo_estatal": clasificado.get("organo") or clasificado.get("organo_detalle"),
            "via_estatal": via,
            "codigos_administrativos": codigos,
            "fecha_publicacion_busqueda": clasificado.get("fecha_publicacion_busqueda"),
            "url_publicacion_oficial": clasificado.get("publicacion_oficial_url"),
            "fecha_publicacion_oficial": clasificado.get("publicacion_oficial_fecha"),
        },
    }
