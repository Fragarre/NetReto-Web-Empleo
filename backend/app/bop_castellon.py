from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
from psycopg.rows import dict_row

from .ambito_administrativo import clasificar_ambito_administrativo
from .bop_valencia_municipios import _clasificar_anuncio, _sin
from .bop_alicante import seleccionar_proceso_seguimiento
from .database import get_connection

PORTAL = "https://bop.dipcas.es/PortalBOP/"
DESCARGA = "https://bop.dipcas.es/PortalBOP/api/descargarAnuncio"


def _texto(elemento) -> str:
    return " ".join(elemento.get_text(" ", strip=True).split())


def _extraer_sumario(html: str) -> dict[str, Any]:
    """Extrae el sumario visible del BOP; no persiste ni consulta BD."""
    soup = BeautifulSoup(html, "html.parser")
    texto = _texto(soup)
    numero = None
    fecha = None
    import re
    m = re.search(r"Sumario\s+BOP.*?(\d+)\s*\|?\s*(\d{2}/\d{2}/\d{4})", texto, re.I)
    if m:
        numero, fecha = m.group(1), m.group(2)

    anuncios: list[dict[str, Any]] = []
    organismo = ""
    for nodo in soup.find_all(["a", "div", "span", "td", "li"]):
        t = _texto(nodo)
        if not t:
            continue
        enlace = nodo if nodo.name == "a" else nodo.find("a", href=True)
        href = enlace.get("href") if enlace else None
        if href and "descargarAnuncio" in href and "idAnuncio=" in href:
            import re
            mid = re.search(r"idAnuncio=(\d+)", href)
            if not mid:
                continue
            # El enlace solo contiene "ui-button". En PrimeFaces aparecen
            # scripts auxiliares entre el botón y el texto visible del anuncio.
            titulo = ""
            siguiente = enlace.find_next()
            while siguiente is not None:
                if siguiente.name == "a" and siguiente.get("href") and "descargarAnuncio" in siguiente.get("href"):
                    break
                if siguiente.name in {"script", "style"}:
                    siguiente = siguiente.find_next()
                    continue
                candidato = _texto(siguiente)
                if (
                    candidato
                    and candidato != "ui-button"
                    and "PrimeFaces.cw(" not in candidato
                    and not candidato.startswith("$(function()")
                ):
                    titulo = candidato
                    break
                siguiente = siguiente.find_next()
            if not titulo:
                continue
            # El organismo válido es la cabecera estructural más próxima
            # al enlace dentro de su cadena de contenedores. Un div exterior
            # puede contener un titulo2 (Diputación) y varios titulo3
            # municipales, por lo que no debemos tomar la primera cabecera
            # de un ancestro amplio.
            organismo_anuncio = organismo
            if not organismo_anuncio:
                for contenedor in enlace.find_parents("div"):
                    cabeceras = contenedor.find_all(
                        "span",
                        class_=lambda clases: clases
                        and ("titulo2" in clases.split() or "titulo3" in clases.split()),
                        recursive=False,
                    )
                    if cabeceras:
                        organismo_anuncio = _texto(cabeceras[0])
                        break

            anuncios.append({
                "id_anuncio": mid.group(1),
                "referencia": f"BOPCS:{mid.group(1)}",
                "numero_bop": numero,
                "fecha_publicacion": fecha,
                "organismo": organismo_anuncio,
                "titulo": titulo,
                "url_documento": str(httpx.URL(DESCARGA).copy_add_param("idAnuncio", mid.group(1)).copy_add_param("idioma", "es")),
            })
            continue

        normal = t.upper()
        if (
            normal.startswith("AYUNTAMIENTO ")
            or normal.startswith("AJUNTAMENT ")
            or normal.startswith("DIPUTACIÓ PROVINCIAL")
            or normal.startswith("DIPUTACIÓN PROVINCIAL")
        ) and len(t) < 180:
            organismo = t

    # El DOM puede repetir nodos contenedores: identidad documental por idAnuncio.
    unicos: dict[str, dict[str, Any]] = {}
    for anuncio in anuncios:
        unicos.setdefault(anuncio["id_anuncio"], anuncio)
    return {"numero_bop": numero, "fecha_publicacion": fecha, "anuncios": list(unicos.values())}



def _boletines_disponibles(html: str) -> list[dict[str, Any]]:
    """Extrae fecha, número y componente JSF de «Boletines anteriores»."""
    import re
    soup = BeautifulSoup(html, "html.parser")
    meses = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
        "noviembre": 11, "diciembre": 12,
    }
    encontrados = {}
    patron = re.compile(r"N[º°]\s*(\d+).*?(\d{2})\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)\s+(\d{4})")
    for nodo in soup.find_all(id=True):
        m = patron.search(_texto(nodo))
        if not m:
            continue
        mes = meses.get(m.group(3).lower())
        if not mes:
            continue
        item = {
            "numero": m.group(1),
            "fecha": date(int(m.group(4)), mes, int(m.group(2))),
            "source": nodo.get("id"),
        }
        clave = (item["numero"], item["fecha"])
        previo = encontrados.get(clave)
        if previo is None or item["source"].count(":") > previo["source"].count(":"):
            encontrados[clave] = item
    return sorted(encontrados.values(), key=lambda x: x["fecha"], reverse=True)


def _view_state(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    nodo = soup.find("input", attrs={"name": "javax.faces.ViewState"})
    return nodo.get("value") if nodo else None


def _cargar_boletin_anterior(client: httpx.Client, html_inicial: str, source: str) -> str:
    """Reproduce la acción JSF/PrimeFaces observada en el navegador."""
    import xml.etree.ElementTree as ET
    view_state = _view_state(html_inicial)
    if not view_state:
        raise RuntimeError("El portal no expone javax.faces.ViewState")
    formulario = "busquedaBoletinesForm"
    datos = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": source,
        "javax.faces.partial.execute": "@all",
        "javax.faces.partial.render": formulario,
        source: source,
        formulario: formulario,
        "javax.faces.ViewState": view_state,
    }
    respuesta = client.post(
        PORTAL,
        data=datos,
        headers={
            "Accept": "application/xml, text/xml, */*; q=0.01",
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": PORTAL,
        },
    )
    respuesta.raise_for_status()
    raiz = ET.fromstring(respuesta.text)
    for update in raiz.findall(".//update"):
        if update.get("id") == formulario and update.text:
            return update.text
    raise RuntimeError("La respuesta JSF no contiene la actualización del formulario")


def _clasificar_anuncio_castellon(titulo: str) -> str:
    """Clasificación conservadora adaptada a las fórmulas observadas en BOP Castellón."""
    clase = _clasificar_anuncio(titulo)
    if clase != "SEGUIMIENTO":
        return clase

    n = _sin(titulo)
    # No promover a convocatoria inicial anuncios que describen hitos,
    # impugnaciones o correcciones de unas bases ya publicadas.
    bloqueos = (
        "recurso", "rectificacion", "correccion", "lista provisional",
        "lista definitiva", "listado provisional", "listado definitivo",
        "admitidos", "admitidas", "excluidos", "excluidas", "tribunal",
        "nombramiento", "emplazamiento", "alegacion", "abstencion",
    )
    if any(x in n for x in bloqueos):
        return clase

    nuevas_castellon = (
        "bases rectoras especificas para la provision en propiedad",
        "aprobacion bases provision en propiedad",
        "bases concurso oposicion libre",
    )
    return "NUEVA_CONVOCATORIA" if any(x in n for x in nuevas_castellon) else clase


def consultar_bop_castellon(*, desde: date | None = None, hasta: date | None = None) -> dict[str, Any]:
    """Fase 4: SOLO_REVISION sobre una ventana real de boletines oficiales."""
    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION",
        "fuente": "Boletín Oficial de la Provincia de Castellón",
        "hasta": (hasta or date.today()).isoformat(),
        "fecha_boletin": None,
        "numero_bop": None,
        "descubiertos": 0,
        "administrativos": 0,
        "revision": 0,
        "resumen_clases": {},
        "errores": [],
        "detalle": [],
        "muestra_extraida": [],
        "sin_organismo": [],
    }
    limite_hasta = hasta or date.today()
    limite_desde = desde or limite_hasta
    resultado["desde"] = limite_desde.isoformat()
    resultado["boletines_revisados"] = 0
    if limite_desde > limite_hasta:
        resultado["errores"].append("La fecha desde no puede ser posterior a hasta")
        return resultado
    try:
        with httpx.Client(timeout=45, follow_redirects=True, headers={"User-Agent": "TuCoach-Empleo/1.0"}) as client:
            respuesta = client.get(PORTAL)
            respuesta.raise_for_status()
            html_inicial = respuesta.text
            actual = _extraer_sumario(html_inicial)
            sumarios = []
            if actual["fecha_publicacion"]:
                fecha_actual = datetime.strptime(actual["fecha_publicacion"], "%d/%m/%Y").date()
                if limite_desde <= fecha_actual <= limite_hasta:
                    sumarios.append(actual)
            for boletin in _boletines_disponibles(html_inicial):
                if not (limite_desde <= boletin["fecha"] <= limite_hasta):
                    continue
                html_boletin = _cargar_boletin_anterior(client, html_inicial, boletin["source"])
                sumario = _extraer_sumario(html_boletin)
                fecha_esperada = boletin["fecha"].strftime("%d/%m/%Y")
                if sumario["fecha_publicacion"] != fecha_esperada:
                    raise RuntimeError(f"El portal devolvió {sumario['fecha_publicacion']!r} para BOP {boletin['numero']} ({fecha_esperada})")
                sumarios.append(sumario)
    except Exception as exc:
        resultado["errores"].append(f"{type(exc).__name__}: {exc}")
        return resultado
    anuncios_unicos = {}
    for sumario in sumarios:
        for anuncio in sumario["anuncios"]:
            anuncios_unicos.setdefault(anuncio["id_anuncio"], anuncio)
    anuncios = list(anuncios_unicos.values())
    resultado["boletines_revisados"] = len(sumarios)
    if sumarios:
        resultado["fecha_boletin"] = sumarios[0]["fecha_publicacion"]
        resultado["numero_bop"] = sumarios[0]["numero_bop"]
    resultado["muestra_extraida"] = [{"organismo": x["organismo"], "titulo": x["titulo"], "referencia": x["referencia"]} for x in anuncios[:20]]
    resultado["sin_organismo"] = [{"titulo": x["titulo"], "referencia": x["referencia"], "numero_bop": x["numero_bop"], "fecha_publicacion": x["fecha_publicacion"]} for x in anuncios if not x["organismo"]]
    candidatos = []
    for anuncio in anuncios:
        ambito = clasificar_ambito_administrativo({"denominacion": anuncio["titulo"], "cuerpo_escala": None, "grupo": None})
        if ambito != "SI":
            continue
        item = dict(anuncio)
        item["ambito_administrativo"] = ambito
        item["clase"] = _clasificar_anuncio_castellon(item["titulo"])
        candidatos.append(item)
    from collections import Counter
    conteo = Counter(x["clase"] for x in candidatos)
    resultado["descubiertos"] = len(anuncios)
    resultado["administrativos"] = len(candidatos)
    resultado["revision"] = conteo.get("REVISION", 0)
    resultado["resumen_clases"] = dict(sorted(conteo.items()))
    resultado["detalle"] = candidatos
    return resultado



def _fecha_bop(valor: str | None) -> date | None:
    if not valor:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return (
                date.fromisoformat(valor)
                if formato == "%Y-%m-%d"
                else datetime.strptime(valor, formato).date()
            )
        except ValueError:
            pass
    return None


def _municipio_organismo(organismo: str | None) -> str:
    """Obtiene el municipio de las cabeceras del sumario sin consultar BD."""
    import re

    texto = " ".join((organismo or "").split()).strip()
    patrones = (
        r"^AJUNTAMENT\s+D['’](.+)$",
        r"^AJUNTAMENT\s+DE\s+(.+)$",
        r"^AYUNTAMIENTO\s+D['’](.+)$",
        r"^AYUNTAMIENTO\s+DE\s+(.+)$",
    )
    for patron in patrones:
        m = re.match(patron, texto, re.I)
        if m:
            return m.group(1).strip()
    return texto


def preparar_importacion_bop_castellon(
    *,
    desde: date | None = None,
    hasta: date | None = None,
) -> dict[str, Any]:
    """Prepara la persistencia con lecturas de BD; nunca escribe."""
    revision = consultar_bop_castellon(desde=desde, hasta=hasta)
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
                        "municipio": _municipio_organismo(hallazgo.get("organismo")),
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

            municipio = _municipio_organismo(hallazgo.get("organismo"))
            cursor.execute(
                """
                SELECT p.id,p.denominacion,p.codigo_externo,p.fecha_convocatoria,o.municipio
                FROM procesos p
                JOIN organismos o ON o.id=p.organismo_id
                WHERE o.tipo='AYUNTAMIENTO'
                  AND LOWER(COALESCE(o.provincia,'')) IN ('castellón','castellon')
                  AND p.ambito_administrativo='SI'
                  AND p.estado='EN_CURSO'
                  AND p.fecha_convocatoria IS NOT NULL
                  AND p.fecha_convocatoria <= %s
                ORDER BY p.fecha_convocatoria DESC,p.id DESC
                """,
                (fecha_publicacion,),
            )
            candidatos = list(cursor.fetchall())
            hallazgo_matching = dict(hallazgo)
            hallazgo_matching["denominacion"] = municipio
            hallazgo_matching["extracto"] = hallazgo.get("titulo") or ""
            proceso, motivo = seleccionar_proceso_seguimiento(
                hallazgo_matching,
                candidatos,
            )
            if proceso:
                resultado["seguimientos_vinculados"] += 1
            else:
                resultado["seguimientos_revision"] += 1
            resultado["detalle"].append({
                "referencia": hallazgo["referencia"],
                "clase": clase,
                "municipio": municipio,
                "vinculacion": motivo,
                "proceso_id": proceso["id"] if proceso else None,
            })

        connection.rollback()
    return resultado
