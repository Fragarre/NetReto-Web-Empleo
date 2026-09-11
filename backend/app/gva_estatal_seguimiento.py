from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database import get_connection
from .gva_estatal_persist import _resolver_fuente_dogv
from .gva_estatal_source import DETALLE, _get, _limpio, _sin, nuevo_cliente


ESTADOS_TERMINALES = {
    "finalizado", "finalitzado", "finalitzat",
    "cancelado", "cancel·lado", "cancel·lat",
    "desistido", "desistit", "anulado", "anul·lat",
}


def _tokens_identidad(texto: str) -> set[str]:
    """Extrae identificadores fuertes de una convocatoria.

    No se usan palabras genéricas ni años sueltos. Así evitamos enlazar un
    seguimiento erróneo que el agregador estatal haya asociado a otra convocatoria.
    """
    n = _sin(texto).upper()
    tokens: set[str] = set()
    for codigo in re.findall(r"\b[A-C]\d-\d{2}(?:-\d{2})?\b", n):
        tokens.add(f"CODIGO:{codigo}")
    for numero in re.findall(r"\bORDEN\s+(\d{1,3}/\d{4})\b", n):
        tokens.add(f"ORDEN:{numero}")
    for numero in re.findall(r"\bCONVOCATORIA\s+(\d{1,3}/\d{2,4})\b", n):
        tokens.add(f"CONVOCATORIA:{numero}")
    for numero in re.findall(r"\bBOLSA\s+([0-9]{2,5}-?[A-Z])\b", n):
        tokens.add(f"BOLSA:{numero}")
    return tokens


def _fila_seccion(soup: BeautifulSoup, nombre: str):
    objetivo = _sin(nombre).strip()
    for fila in soup.select("div.detalle-content-row"):
        texto = _sin(_limpio(fila.get_text(" ", strip=True)))
        if texto.startswith(objetivo):
            return fila
    return None


def _signatura_dogv(url: str) -> str | None:
    m = re.search(r"/pdf/(\d{4})_(\d+)_", url, re.I)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    query = parse_qs(urlparse(url).query)
    valor = (query.get("signatura") or [None])[0]
    if valor:
        m = re.fullmatch(r"(\d{4})/(\d+)", valor.strip())
        if m:
            return f"{m.group(1)}_{m.group(2)}"
    return None


def _fecha_seguimiento(texto: str, url: str) -> str | None:
    m = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", texto)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"/datos/(\d{4})/(\d{2})/(\d{2})/", url, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _tipo_seguimiento(texto: str) -> str:
    n = _sin(texto)
    if "correccion" in n or "modificacion" in n:
        return "MODIFICACION"
    if "admitid" in n or "excluid" in n:
        return "ADMITIDOS"
    if "tribunal" in n:
        return "TRIBUNAL"
    if "examen" in n or "prueba" in n and "fecha" in n:
        return "EXAMEN"
    if "resultado" in n or "calificacion" in n or "meritos" in n:
        return "RESULTADO"
    if "nombramiento" in n:
        return "NOMBRAMIENTO"
    if "adjudic" in n:
        return "ADJUDICACION"
    if "lista" in n or "bolsa" in n:
        return "LISTA"
    return "SEGUIMIENTO_OFICIAL"


def extraer_seguimientos_validos(html: str) -> dict[str, Any]:
    """Extrae solo seguimientos DOGV vinculables inequívocamente a la ficha.

    La identidad se toma de la disposición primaria de la propia ficha. Un
    seguimiento se acepta únicamente si comparte al menos un identificador fuerte
    (código, orden, convocatoria o bolsa). Los enlaces dudosos se devuelven como
    rechazados y nunca se publican automáticamente.
    """
    soup = BeautifulSoup(html, "html.parser")
    disposiciones = _fila_seccion(soup, "Disposiciones")
    seguimiento = _fila_seccion(soup, "Seguimiento")
    texto_primario = _limpio(disposiciones.get_text(" ", strip=True)) if disposiciones else ""
    identidad = _tokens_identidad(texto_primario)

    validos: list[dict[str, Any]] = []
    rechazados: list[dict[str, Any]] = []
    vistos: set[str] = set()

    if seguimiento is None:
        return {"identidad": sorted(identidad), "validos": [], "rechazados": []}

    for enlace in seguimiento.find_all("a", href=True):
        url = str(enlace.get("href") or "").strip()
        if "dogv.gva.es" not in url.lower():
            continue
        signatura = _signatura_dogv(url)
        if not signatura or signatura in vistos:
            continue
        vistos.add(signatura)
        dd = enlace.find_parent("dd")
        texto = _limpio(dd.get_text(" ", strip=True) if dd else enlace.get_text(" ", strip=True))
        tokens = _tokens_identidad(texto)
        comunes = sorted(identidad & tokens)
        item = {
            "signatura": signatura,
            "url": url,
            "fecha_publicacion": _fecha_seguimiento(texto, url),
            "tipo": _tipo_seguimiento(texto),
            "titulo": texto.replace(url, "").strip(),
            "tokens": sorted(tokens),
            "coincidencias_identidad": comunes,
        }
        if identidad and comunes:
            validos.append(item)
        else:
            item["motivo"] = "identidad_no_coincidente" if identidad else "identidad_primaria_insuficiente"
            rechazados.append(item)

    return {"identidad": sorted(identidad), "validos": validos, "rechazados": rechazados}


def _referencia_estatal(datos_json: dict[str, Any] | None) -> int | None:
    datos = datos_json or {}
    fuente = datos.get("fuente_estatal") if isinstance(datos.get("fuente_estatal"), dict) else {}
    valor = fuente.get("referencia_estatal") or datos.get("referencia_estatal")
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _cargar_procesos_activos() -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, identificador_estable, denominacion, estado, datos_json
            FROM procesos
            WHERE organismo_id=1
              AND es_oportunidad=TRUE
              AND ambito_administrativo='SI'
            ORDER BY id
            """
        )
        filas = list(cursor.fetchall())
    salida: list[dict[str, Any]] = []
    for fila in filas:
        if str(fila.get("estado") or "").strip().lower() in ESTADOS_TERMINALES:
            continue
        ref = _referencia_estatal(fila.get("datos_json"))
        if ref is None:
            continue
        fila = dict(fila)
        fila["referencia_estatal"] = ref
        salida.append(fila)
    return salida


def _obtener_html(client, referencia: int) -> str:
    respuesta = _get(client, DETALLE, params={"idConvocatoria": referencia, "idioma": "es"})
    return respuesta.text


def _referencia_publicacion(referencia_estatal: int, signatura: str) -> str:
    return f"GVAESTATAL:{referencia_estatal}:SEGUIMIENTO:{signatura}"


def planificar_seguimientos(
    procesos: list[dict[str, Any]],
    resultados: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    acciones: list[dict[str, Any]] = []
    for proceso in procesos:
        proceso_id = int(proceso["id"])
        ref = int(proceso["referencia_estatal"])
        extraido = resultados[proceso_id]
        datos = proceso.get("datos_json") or {}
        estado = datos.get("seguimiento_gva") if isinstance(datos.get("seguimiento_gva"), dict) else None
        actuales = {x["signatura"] for x in extraido["validos"]}

        if not estado or not estado.get("inicializado"):
            acciones.append({
                "accion": "BASELINE",
                "proceso_id": proceso_id,
                "identificador_estable": proceso.get("identificador_estable"),
                "referencia_estatal": ref,
                "vistos": sorted(actuales),
                "rechazados": extraido["rechazados"],
            })
            continue

        vistos = {str(x) for x in (estado.get("vistos") or [])}
        nuevos = [x for x in extraido["validos"] if x["signatura"] not in vistos]
        if not nuevos:
            acciones.append({
                "accion": "SIN_CAMBIOS",
                "proceso_id": proceso_id,
                "identificador_estable": proceso.get("identificador_estable"),
                "referencia_estatal": ref,
                "rechazados": extraido["rechazados"],
            })
            continue

        acciones.append({
            "accion": "PUBLICAR",
            "proceso_id": proceso_id,
            "identificador_estable": proceso.get("identificador_estable"),
            "referencia_estatal": ref,
            "nuevos": nuevos,
            "vistos": sorted(vistos | actuales),
            "rechazados": extraido["rechazados"],
        })

    return {
        "acciones": acciones,
        "resumen": {
            "procesos": len(procesos),
            "baseline": sum(a["accion"] == "BASELINE" for a in acciones),
            "sin_cambios": sum(a["accion"] == "SIN_CAMBIOS" for a in acciones),
            "procesos_con_novedades": sum(a["accion"] == "PUBLICAR" for a in acciones),
            "publicaciones_nuevas": sum(len(a.get("nuevos") or []) for a in acciones),
            "seguimientos_rechazados": sum(len(a.get("rechazados") or []) for a in acciones),
        },
    }


def _guardar_estado(cursor, proceso_id: int, referencia_estatal: int, vistos: list[str]) -> None:
    estado = {
        "version": 1,
        "inicializado": True,
        "fuente": "administracion.gob.es",
        "referencia_estatal": referencia_estatal,
        "vistos": vistos,
    }
    cursor.execute(
        """
        UPDATE procesos
        SET datos_json = COALESCE(datos_json, '{}'::jsonb) || %s
        WHERE id=%s
        """,
        (Jsonb({"seguimiento_gva": estado}), proceso_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"No se pudo guardar el estado de seguimiento GVA del proceso {proceso_id}")


def _insertar_publicacion(cursor, *, fuente_dogv_id: int, proceso_id: int, referencia_estatal: int, item: dict[str, Any]) -> bool:
    referencia = _referencia_publicacion(referencia_estatal, item["signatura"])
    cursor.execute(
        """
        INSERT INTO publicaciones (
            proceso_id, fuente_id, referencia, tipo, titulo,
            fecha_publicacion, url, datos_json, detectada_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (fuente_id, referencia, url) DO NOTHING
        """,
        (
            proceso_id,
            fuente_dogv_id,
            referencia,
            item.get("tipo") or "SEGUIMIENTO_OFICIAL",
            item.get("titulo"),
            item.get("fecha_publicacion"),
            item["url"],
            Jsonb({
                "origen": "DOGV",
                "descubierta_via": "administracion.gob.es",
                "referencia_estatal": referencia_estatal,
                "signatura": item["signatura"],
                "coincidencias_identidad": item.get("coincidencias_identidad") or [],
            }),
        ),
    )
    return cursor.rowcount == 1


def actualizar_seguimientos_gva(*, aplicar: bool = False) -> dict[str, Any]:
    """Revisa las fichas de las oportunidades GVA activas ya conocidas.

    Primera ejecución: crea una línea base silenciosa con los seguimientos ya
    existentes. No genera novedades históricas. Ejecuciones posteriores: solo
    publica enlaces DOGV nuevos y vinculados inequívocamente al proceso.
    """
    procesos = _cargar_procesos_activos()
    resultados: dict[int, dict[str, Any]] = {}
    with nuevo_cliente() as client:
        for proceso in procesos:
            html = _obtener_html(client, int(proceso["referencia_estatal"]))
            resultados[int(proceso["id"])] = extraer_seguimientos_validos(html)

    plan = planificar_seguimientos(procesos, resultados)
    if not aplicar:
        return {"modo": "SOLO_REVISION", **plan}

    publicaciones_creadas = 0
    baseline_creados = 0
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        fuente_dogv_id = _resolver_fuente_dogv(cursor)
        for accion in plan["acciones"]:
            if accion["accion"] == "SIN_CAMBIOS":
                continue
            if accion["accion"] == "BASELINE":
                _guardar_estado(
                    cursor,
                    int(accion["proceso_id"]),
                    int(accion["referencia_estatal"]),
                    list(accion["vistos"]),
                )
                baseline_creados += 1
                continue
            if accion["accion"] != "PUBLICAR":
                raise RuntimeError(f"Acción de seguimiento GVA inesperada: {accion['accion']}")

            fechas_creadas: list[str] = []
            for item in accion["nuevos"]:
                if _insertar_publicacion(
                    cursor,
                    fuente_dogv_id=fuente_dogv_id,
                    proceso_id=int(accion["proceso_id"]),
                    referencia_estatal=int(accion["referencia_estatal"]),
                    item=item,
                ):
                    publicaciones_creadas += 1
                    if item.get("fecha_publicacion"):
                        fechas_creadas.append(str(item["fecha_publicacion"]))
            _guardar_estado(
                cursor,
                int(accion["proceso_id"]),
                int(accion["referencia_estatal"]),
                list(accion["vistos"]),
            )
            if fechas_creadas:
                fecha_max = max(fechas_creadas)
                cursor.execute(
                    """
                    UPDATE procesos
                    SET ultima_publicacion_at = GREATEST(
                        COALESCE(ultima_publicacion_at, %s::date::timestamptz),
                        %s::date::timestamptz
                    ), updated_at=NOW()
                    WHERE id=%s
                    """,
                    (fecha_max, fecha_max, int(accion["proceso_id"])),
                )

    return {
        "modo": "APLICADO",
        **plan,
        "baseline_creados": baseline_creados,
        "publicaciones_creadas": publicaciones_creadas,
    }
