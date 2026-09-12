from __future__ import annotations

import re
from typing import Any

import httpx

from .gva_estatal_seguimiento import (
    _cargar_procesos_activos,
    _obtener_html,
    _tokens_identidad,
    extraer_seguimientos_validos,
)
from .gva_estatal_source import nuevo_cliente

DOGV_API = "https://dogv.gva.es/dogv-portal"


def _codigo_insercion(signatura: str) -> str:
    anio, numero = signatura.split("_", 1)
    return f"{anio}/{numero}"


def _fecha_oficial_desde_url(url: str | None) -> str | None:
    """Extrae la fecha inequívoca de una URL PDF DOGV, si está presente."""
    m = re.search(r"/datos/(\d{4})/(\d{2})/(\d{2})/", str(url or ""), re.I)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _obtener_disposicion_dogv(
    client: httpx.Client,
    *,
    signatura: str,
    fecha_publicacion: str | None,
) -> dict[str, Any]:
    """Localiza una disposición por código de inserción dentro del DOGV del día.

    Es deliberadamente determinista: fecha + codigoInsercion exacto. Si falta la
    fecha o no hay exactamente una coincidencia, no se intenta ninguna heurística.
    """
    if not fecha_publicacion:
        return {"ok": False, "motivo": "fecha_publicacion_ausente"}

    codigo = _codigo_insercion(signatura)
    respuesta = client.get(
        f"{DOGV_API}/dogv",
        params={"date": fecha_publicacion, "lang": "es_es"},
    )
    respuesta.raise_for_status()
    diario = respuesta.json()
    coincidencias = [
        item for item in (diario.get("disposiciones") or [])
        if str(item.get("codigoInsercion") or "").strip() == codigo
    ]
    if len(coincidencias) != 1:
        return {
            "ok": False,
            "motivo": "codigo_insercion_no_unico_en_fecha",
            "codigo_insercion": codigo,
            "fecha_publicacion": fecha_publicacion,
            "coincidencias": len(coincidencias),
        }

    resumen = coincidencias[0]
    dogv_id = int(resumen["id"])
    detalle_resp = client.get(
        f"{DOGV_API}/disposicion/{dogv_id}",
        params={"lang": "es_es"},
    )
    detalle_resp.raise_for_status()
    detalle = detalle_resp.json()
    texto_identidad = " ".join(
        str(x or "") for x in (detalle.get("titulo"), detalle.get("texto"))
    )
    return {
        "ok": True,
        "id_dogv": dogv_id,
        "codigo_insercion": detalle.get("codigoInsercion") or codigo,
        "cve": detalle.get("cve"),
        "numero_dogv": detalle.get("numeroDogv"),
        "fecha_publicacion": detalle.get("fechaPublicacion") or fecha_publicacion,
        "titulo": detalle.get("titulo") or resumen.get("titulo"),
        "organismo": detalle.get("organismo") or resumen.get("organismo"),
        "tokens_dogv": sorted(_tokens_identidad(texto_identidad)),
    }


def diagnosticar_seguimiento_dogv() -> dict[str, Any]:
    """Audita, sin escribir, el seguimiento GVA actual contra el DOGV oficial.

    administracion.gob.es aporta la asociación proceso -> seguimiento. El DOGV
    estructurado valida después cada publicación mediante fecha + código de
    inserción exactos. También señala signaturas guardadas históricamente que ya
    no aparecen entre los seguimientos válidos de la ficha estatal.
    """
    procesos = _cargar_procesos_activos()
    salida: list[dict[str, Any]] = []

    with nuevo_cliente() as estatal, httpx.Client(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={
            "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
            "Accept-Language": "es-ES,es;q=0.9",
        },
        follow_redirects=True,
    ) as dogv:
        for proceso in procesos:
            proceso_id = int(proceso["id"])
            referencia = int(proceso["referencia_estatal"])
            html = _obtener_html(estatal, referencia)
            extraido = extraer_seguimientos_validos(html)
            identidad = set(extraido.get("identidad") or [])
            actuales = {x["signatura"] for x in extraido.get("validos") or []}
            datos = proceso.get("datos_json") or {}
            estado = datos.get("seguimiento_gva") if isinstance(datos.get("seguimiento_gva"), dict) else {}
            vistos = {str(x) for x in (estado.get("vistos") or [])}

            validados: list[dict[str, Any]] = []
            for item in extraido.get("validos") or []:
                fecha_extraida = item.get("fecha_publicacion")
                fecha_url = _fecha_oficial_desde_url(item.get("url"))
                fecha_validacion = fecha_url or fecha_extraida
                auditoria: dict[str, Any] = {
                    "signatura": item["signatura"],
                    "fecha_publicacion_extraida": fecha_extraida,
                    "fecha_publicacion_url": fecha_url,
                    "fecha_validacion_dogv": fecha_validacion,
                    "titulo_estatal": item.get("titulo"),
                    "tokens_estatal": item.get("tokens") or [],
                    "coincidencias_identidad_estatal": item.get("coincidencias_identidad") or [],
                }
                try:
                    oficial = _obtener_disposicion_dogv(
                        dogv,
                        signatura=item["signatura"],
                        fecha_publicacion=fecha_validacion,
                    )
                except Exception as exc:
                    oficial = {"ok": False, "motivo": f"{type(exc).__name__}: {exc}"}
                auditoria["dogv"] = oficial
                tokens_dogv = set(oficial.get("tokens_dogv") or [])
                auditoria["coincidencias_identidad_dogv"] = sorted(identidad & tokens_dogv)
                auditoria["validacion_dogv"] = bool(
                    oficial.get("ok") and identidad and (identidad & tokens_dogv)
                )
                validados.append(auditoria)

            salida.append({
                "proceso_id": proceso_id,
                "identificador_estable": proceso.get("identificador_estable"),
                "denominacion": proceso.get("denominacion"),
                "referencia_estatal": referencia,
                "identidad": sorted(identidad),
                "seguimientos_validos_actuales": sorted(actuales),
                "vistos_guardados": sorted(vistos),
                "vistos_huerfanos": sorted(vistos - actuales),
                "actuales_no_vistos": sorted(actuales - vistos),
                "rechazados": extraido.get("rechazados") or [],
                "validaciones": validados,
            })

    return {
        "modo": "SOLO_DIAGNOSTICO",
        "escrituras_bd": False,
        "fuente_asociacion": "administracion.gob.es",
        "fuente_validacion": DOGV_API,
        "procesos": salida,
        "resumen": {
            "procesos": len(salida),
            "seguimientos_validos": sum(len(x["validaciones"]) for x in salida),
            "validaciones_dogv_ok": sum(
                sum(bool(v["validacion_dogv"]) for v in x["validaciones"])
                for x in salida
            ),
            "vistos_huerfanos": sum(len(x["vistos_huerfanos"]) for x in salida),
            "actuales_no_vistos": sum(len(x["actuales_no_vistos"]) for x in salida),
            "rechazados": sum(len(x["rechazados"]) for x in salida),
        },
    }
