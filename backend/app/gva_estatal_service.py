from __future__ import annotations

from datetime import date
import re
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from . import gva_clean
from .gva_estatal_import import construir_registro
from .gva_estatal_persist import persistir_registros
from .gva_estatal_seguimiento import actualizar_seguimientos_gva
from .gva_estatal_source import (
    clasificar_oportunidad,
    descubrir_referencias,
    nuevo_cliente,
    obtener_detalle,
)


def _signatura_dogv(url: str | None) -> str | None:
    """Extrae la signatura estable YYYY_NUM de una URL PDF del DOGV."""
    m = re.search(r"/pdf/(\d{4})_(\d+)_", str(url or ""), re.I)
    return f"{m.group(1)}_{m.group(2)}" if m else None


def _descubrir_detalles_para_resolver(client: httpx.Client, max_paginas: int = 10) -> list[tuple[int, str]]:
    """Descubre fichas GVA sin limitarse a plazos de solicitud abiertos.

    Es una búsqueda auxiliar exclusiva para resolver el id_emp de una
    convocatoria ya identificada por la fuente estatal. No cambia el
    descubrimiento ordinario de convocatorias GVA, que sigue usando
    gva_clean.descubrir_detalles() con plazos=A.
    """
    encontrados: dict[int, str] = {}
    for pagina in range(1, max_paginas + 1):
        respuesta = client.get(
            gva_clean.GVA_SEARCH_URL,
            params={
                "pagina": pagina,
                "tipoOrganismo": "1",
                "tamanyoPagina": "30",
            },
        )
        respuesta.raise_for_status()
        soup = BeautifulSoup(respuesta.text, "html.parser")
        encontrados_pagina = 0
        for enlace in soup.select('a[href*="detall-ocupacio-publica"]'):
            href = enlace.get("href")
            if not href:
                continue
            m = re.search(r"id_emp=(\d+)", href)
            if not m:
                continue
            id_emp = int(m.group(1))
            encontrados[id_emp] = urljoin(gva_clean.GVA_BASE_URL, href)
            encontrados_pagina += 1

        # Si la página no contiene ninguna ficha, no quedan más resultados.
        if encontrados_pagina == 0:
            break

    return sorted(encontrados.items())


def _enriquecer_fichas_oficiales_gva(registros: list[dict[str, Any]], client) -> dict[str, Any]:
    """Relaciona una alta estatal con su ficha de Sede GVA sin heurísticas débiles.

    La correspondencia se acepta únicamente cuando el PDF DOGV primario de la
    convocatoria aparece en exactamente una ficha descubierta en la Sede GVA.
    Si no hay coincidencia o hay más de una, el proceso sigue siendo válido pero
    se deja sin ficha para volver a intentarlo en una ejecución posterior.

    Los fallos puntuales de red o timeout de la Sede GVA no bloquean la
    importación estatal: la ficha queda pendiente y se podrá resolver después.
    """
    pendientes: dict[str, list[dict[str, Any]]] = {}
    for registro in registros:
        datos = registro.get("datos_json") or {}
        if datos.get("url_detalle"):
            continue
        signatura = _signatura_dogv(datos.get("url_publicacion_oficial"))
        if signatura:
            pendientes.setdefault(signatura, []).append(registro)

    if not pendientes:
        return {
            "pendientes": 0,
            "resueltas": 0,
            "sin_coincidencia": 0,
            "ambiguas": 0,
            "errores_red": 0,
        }

    candidatas_por_signatura: dict[str, list[tuple[int, str]]] = {
        signatura: [] for signatura in pendientes
    }
    errores_red = 0

    try:
        detalles = _descubrir_detalles_para_resolver(client, max_paginas=10)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError):
        return {
            "pendientes": sum(len(v) for v in pendientes.values()),
            "resueltas": 0,
            "sin_coincidencia": 0,
            "ambiguas": 0,
            "errores_red": 1,
        }

    for id_emp, url in detalles:
        try:
            respuesta = client.get(url)
            respuesta.raise_for_status()
            html = respuesta.text
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError):
            errores_red += 1
            continue

        for signatura in pendientes:
            # Con dos coincidencias la signatura ya es ambigua; no necesita
            # seguir comparándose con más fichas, aunque sí continuamos el
            # recorrido para poder resolver las demás signaturas pendientes.
            if len(candidatas_por_signatura[signatura]) >= 2:
                continue
            if signatura in html:
                candidatas_por_signatura[signatura].append((int(id_emp), str(url)))

    resueltas = 0
    ambiguas = 0
    sin_coincidencia = 0

    for signatura, registros_signatura in pendientes.items():
        candidatas = candidatas_por_signatura.get(signatura) or []
        # Una coincidencia solo es concluyente si se pudieron revisar todas las
        # fichas candidatas sin errores de red. Con páginas omitidas no podemos
        # demostrar que no exista una segunda coincidencia.
        if len(candidatas) == 1 and errores_red == 0:
            id_emp, url = candidatas[0]
            for registro in registros_signatura:
                datos = dict(registro.get("datos_json") or {})
                datos["id_emp"] = id_emp
                datos["codigo_gva"] = str(id_emp)
                datos["url_detalle"] = url
                datos["ficha_gva_resuelta_por"] = "DOGV_SIGNATURA_EXACTA"
                registro["datos_json"] = datos
                resueltas += 1
        elif len(candidatas) > 1:
            ambiguas += len(registros_signatura)
        else:
            sin_coincidencia += len(registros_signatura)

    return {
        "pendientes": sum(len(v) for v in pendientes.values()),
        "resueltas": resueltas,
        "sin_coincidencia": sin_coincidencia,
        "ambiguas": ambiguas,
        "errores_red": errores_red,
    }


def importar_gva_estatal(*, desde: date, hasta: date, aplicar: bool = False) -> dict[str, Any]:
    """Descubre convocatorias nuevas y revisa las fichas GVA ya conocidas.

    `aplicar=False` es el modo por defecto y no escribe en base de datos.
    Las fechas delimitan únicamente el descubrimiento de nuevas convocatorias;
    el seguimiento de procesos activos se hace por sus fichas ya persistidas.
    """
    if desde > hasta:
        raise ValueError("La fecha 'desde' no puede ser posterior a 'hasta'")

    registros: list[dict[str, Any]] = []
    revision: list[dict[str, Any]] = []
    excluidas = {
        "fuera_ambito_administrativo": 0,
        "promocion_interna": 0,
        "organismo_excluido": 0,
        "via_no_incluida": 0,
    }

    with nuevo_cliente() as client:
        tarjetas = descubrir_referencias(client, desde, hasta)
        for tarjeta in tarjetas:
            detalle = obtener_detalle(client, int(tarjeta["referencia"]))
            clasificada = clasificar_oportunidad(tarjeta, detalle)

            if clasificada.get("es_oportunidad"):
                registros.append(construir_registro(tarjeta, detalle))
                continue

            motivo = clasificada.get("motivo") or "revision"
            if motivo == "organismo_revision" or (
                clasificada.get("ambito_administrativo") == "SI"
                and motivo == "via_no_incluida"
            ):
                revision.append({
                    "referencia_estatal": clasificada.get("referencia"),
                    "titulo": clasificada.get("titulo"),
                    "organo": clasificada.get("organo") or clasificada.get("organo_detalle"),
                    "via": clasificada.get("via"),
                    "codigos_administrativos": clasificada.get("codigos_administrativos") or [],
                    "motivo": motivo,
                    "url": clasificada.get("url"),
                })
                continue

            excluidas[motivo] = excluidas.get(motivo, 0) + 1

        fichas_gva = _enriquecer_fichas_oficiales_gva(registros, client)

    persistencia = persistir_registros(registros, aplicar=aplicar)
    seguimiento = actualizar_seguimientos_gva(aplicar=aplicar)
    return {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "tarjetas_autonomico_cv": len(tarjetas),
        "oportunidades_incluidas": len(registros),
        "fichas_gva": fichas_gva,
        "revision_manual": len(revision),
        "excluidas": excluidas,
        "revision": revision,
        "persistencia": persistencia,
        "seguimiento": seguimiento,
    }
