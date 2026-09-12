from __future__ import annotations

from datetime import date, timedelta
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


def _fecha_url_dogv(url: str | None) -> str | None:
    m = re.search(r"/datos/(\d{4})/(\d{2})/(\d{2})/", str(url or ""), re.I)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _leer_diario(client: httpx.Client, fecha: str) -> dict[str, Any]:
    respuesta = client.get(
        f"{DOGV_API}/dogv",
        params={"date": fecha, "lang": "es_es"},
    )
    respuesta.raise_for_status()
    return respuesta.json()


def _detalle_disposicion(client: httpx.Client, resumen: dict[str, Any]) -> dict[str, Any]:
    dogv_id = int(resumen["id"])
    respuesta = client.get(
        f"{DOGV_API}/disposicion/{dogv_id}",
        params={"lang": "es_es"},
    )
    respuesta.raise_for_status()
    detalle = respuesta.json()
    texto_identidad = " ".join(
        str(x or "") for x in (detalle.get("titulo"), detalle.get("texto"))
    )
    return {
        "ok": True,
        "id_dogv": dogv_id,
        "codigo_insercion": detalle.get("codigoInsercion") or resumen.get("codigoInsercion"),
        "cve": detalle.get("cve"),
        "numero_dogv": detalle.get("numeroDogv"),
        "fecha_publicacion": detalle.get("fechaPublicacion") or resumen.get("fechaPublicacion"),
        "titulo": detalle.get("titulo") or resumen.get("titulo"),
        "organismo": detalle.get("organismo") or resumen.get("organismo"),
        "tokens_dogv": sorted(_tokens_identidad(texto_identidad)),
    }


def _obtener_disposicion_dogv(
    client: httpx.Client,
    *,
    signatura: str,
    fecha_publicacion: str | None,
) -> dict[str, Any]:
    """Localiza una disposición por código de inserción exacto.

    Primero usa la fecha asociada al enlace. Si el portal estatal ha fechado el
    enlace de forma incorrecta, busca el mismo código exacto en una ventana de
    cinco días a cada lado. No se usa similitud de títulos ni otra heurística.
    """
    if not fecha_publicacion:
        return {"ok": False, "motivo": "fecha_publicacion_ausente"}

    codigo = _codigo_insercion(signatura)
    base = date.fromisoformat(fecha_publicacion)
    fechas = [base]
    for distancia in range(1, 6):
        fechas.extend((base - timedelta(days=distancia), base + timedelta(days=distancia)))

    for indice, fecha in enumerate(fechas):
        fecha_iso = fecha.isoformat()
        diario = _leer_diario(client, fecha_iso)
        coincidencias = [
            item for item in (diario.get("disposiciones") or [])
            if str(item.get("codigoInsercion") or "").strip() == codigo
        ]
        if len(coincidencias) == 1:
            salida = _detalle_disposicion(client, coincidencias[0])
            salida["fecha_consultada"] = fecha_iso
            salida["fecha_resuelta_por"] = "FECHA_EXACTA" if indice == 0 else "CODIGO_EXACTO_VENTANA_5_DIAS"
            return salida
        if len(coincidencias) > 1:
            return {
                "ok": False,
                "motivo": "codigo_insercion_no_unico_en_fecha",
                "codigo_insercion": codigo,
                "fecha_publicacion": fecha_iso,
                "coincidencias": len(coincidencias),
            }

    return {
        "ok": False,
        "motivo": "codigo_insercion_no_localizado_en_ventana",
        "codigo_insercion": codigo,
        "fecha_publicacion": fecha_publicacion,
        "ventana_dias": 5,
    }


def _coincide_identidad(identidad: set[str], tokens: set[str]) -> tuple[bool, list[str]]:
    comunes = sorted(identidad & tokens)
    especificos = {
        token for token in identidad
        if token.startswith("CONVOCATORIA:")
        or token.startswith("ORDEN:")
        or token.startswith("BOLSA:")
    }
    codigos = {token for token in identidad if token.startswith("CODIGO:")}
    comunes_especificos = especificos & tokens
    comunes_codigo = codigos & tokens
    ok = bool(comunes_especificos) and (not codigos or bool(comunes_codigo))
    return ok, comunes


def _descubrir_dogv_en_fechas(
    client: httpx.Client,
    *,
    identidad: set[str],
    fechas_semilla: set[str],
) -> list[dict[str, Any]]:
    """Busca seguimientos directamente en DOGV alrededor de fechas conocidas.

    La prueba sirve para comprobar que DOGV puede descubrir publicaciones que el
    portal estatal enlaza mal. Solo acepta coincidencias por identificadores
    fuertes exactos; nunca por similitud textual.
    """
    if not fechas_semilla:
        return []

    fechas: set[date] = set()
    for valor in fechas_semilla:
        try:
            base = date.fromisoformat(valor)
        except ValueError:
            continue
        for desplazamiento in range(-5, 6):
            fechas.add(base + timedelta(days=desplazamiento))

    encontrados: dict[str, dict[str, Any]] = {}
    for fecha in sorted(fechas):
        diario = _leer_diario(client, fecha.isoformat())
        for resumen in diario.get("disposiciones") or []:
            titulo = str(resumen.get("titulo") or "")
            tokens_resumen = _tokens_identidad(titulo)
            comunes_resumen = identidad & tokens_resumen
            if not comunes_resumen:
                continue
            detalle = _detalle_disposicion(client, resumen)
            tokens_detalle = set(detalle.get("tokens_dogv") or [])
            ok, comunes = _coincide_identidad(identidad, tokens_detalle)
            if not ok:
                continue
            codigo = str(detalle.get("codigo_insercion") or "").replace("/", "_")
            if not codigo:
                continue
            encontrados[codigo] = {
                "signatura": codigo,
                "fecha_publicacion": detalle.get("fecha_publicacion"),
                "titulo": detalle.get("titulo"),
                "id_dogv": detalle.get("id_dogv"),
                "cve": detalle.get("cve"),
                "tokens_dogv": detalle.get("tokens_dogv") or [],
                "coincidencias_identidad": comunes,
            }
    return [encontrados[k] for k in sorted(encontrados)]


def diagnosticar_seguimiento_dogv() -> dict[str, Any]:
    """Audita, sin escribir, el seguimiento GVA actual contra el DOGV oficial."""
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
            identidad |= _tokens_identidad(str(proceso.get("denominacion") or ""))
            actuales = {x["signatura"] for x in extraido.get("validos") or []}
            datos = proceso.get("datos_json") or {}
            estado = datos.get("seguimiento_gva") if isinstance(datos.get("seguimiento_gva"), dict) else {}
            vistos = {str(x) for x in (estado.get("vistos") or [])}

            validados: list[dict[str, Any]] = []
            fechas_semilla: set[str] = set()
            for item in extraido.get("validos") or []:
                fecha_url = _fecha_url_dogv(item.get("url"))
                fecha_validacion = fecha_url or item.get("fecha_publicacion")
                if fecha_validacion:
                    fechas_semilla.add(str(fecha_validacion))
                auditoria: dict[str, Any] = {
                    "signatura": item["signatura"],
                    "fecha_publicacion_extraida": item.get("fecha_publicacion"),
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
                ok_identidad, comunes = _coincide_identidad(identidad, tokens_dogv)
                auditoria["coincidencias_identidad_dogv"] = comunes
                auditoria["validacion_dogv"] = bool(oficial.get("ok") and ok_identidad)
                validados.append(auditoria)

            for item in extraido.get("rechazados") or []:
                fecha_url = _fecha_url_dogv(item.get("url"))
                fecha_semilla = fecha_url or item.get("fecha_publicacion")
                if fecha_semilla:
                    fechas_semilla.add(str(fecha_semilla))

            descubrimiento_directo: list[dict[str, Any]] = []
            try:
                descubrimiento_directo = _descubrir_dogv_en_fechas(
                    dogv,
                    identidad=identidad,
                    fechas_semilla=fechas_semilla,
                )
            except Exception as exc:
                descubrimiento_directo = [{
                    "error": f"{type(exc).__name__}: {exc}",
                }]

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
                "descubrimiento_dogv_directo": descubrimiento_directo,
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
            "descubrimientos_dogv_directos": sum(
                sum(1 for y in x["descubrimiento_dogv_directo"] if not y.get("error"))
                for x in salida
            ),
            "vistos_huerfanos": sum(len(x["vistos_huerfanos"]) for x in salida),
            "actuales_no_vistos": sum(len(x["actuales_no_vistos"]) for x in salida),
            "rechazados": sum(len(x["rechazados"]) for x in salida),
        },
    }
