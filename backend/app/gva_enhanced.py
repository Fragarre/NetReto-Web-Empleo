from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from . import gva_clean as base
from .database import get_connection

_BASE_PARSEAR_DETALLE = base.parsear_detalle


def _tipo_convocatoria(texto: str) -> str | None:
    normal = base._sin_acentos(texto)
    m = re.search(r"convocatoria\s+(.{1,120}?)(?:\s+prueba\s+|\s+grupo\s+|\s+titulacion\s+|\s+enlace a organismo\s+)", normal, re.I)
    if not m:
        return None
    valor = base._normalizar(m.group(1))
    patrones = (("bolsa de trabajo", "Bolsa de trabajo"), ("oposicion", "Oposición"), ("promocion interna", "Promoción interna"), ("contratacion laboral temporal", "Contratación laboral temporal"), ("contratacion laboral indefinida", "Contratación laboral indefinida"), ("proceso de estabilizacion", "Proceso de estabilización"), ("acto unico telematico", "Acto único telemático"), ("acte unic telematic", "Acto único telemático"), ("anuncio dificil cobertura", "Anuncio difícil cobertura"), ("concurso general de meritos", "Concurso general de méritos"), ("concurso-oposicion", "Concurso-oposición"), ("concurso", "Concurso"), ("cobertura interina", "Cobertura interina"), ("comision de servicio", "Comisión de servicio"), ("libre designacion", "Libre designación"), ("seleccion personal directivo", "Selección personal directivo"), ("procesos especiales", "Procesos especiales"), ("otros", "Otros"))
    for patron, nombre in patrones:
        if patron in valor:
            return nombre
    return base._normalizar(m.group(1))


def _turno(texto: str) -> str | None:
    normal = base._sin_acentos(texto)
    candidatos = []
    for patron, valor in (("promocion interna", "PROMOCION_INTERNA"), ("turno libre", "TURNO_LIBRE"), ("discapacidad intelectual", "DISCAPACIDAD_INTELECTUAL"), ("discapacidad", "DISCAPACIDAD")):
        posicion = normal.find(patron)
        if posicion >= 0:
            candidatos.append((posicion, valor))
    return min(candidatos, key=lambda x: x[0])[1] if candidatos else None


def _es_incluido(tipo: str | None) -> bool:
    normal = base._sin_acentos(tipo or "")
    return any(patron in normal for patron in ("oposicion", "bolsa de trabajo", "promocion interna", "contratacion laboral", "proceso de estabilizacion", "acto unico telematico", "acte unic telematic", "anuncio dificil cobertura", "concurso general de meritos", "concurso-oposicion", "concurso", "cobertura interina", "comision de servicio", "libre designacion", "seleccion personal directivo"))


def _extraer_organismo(texto: str) -> str | None:
    normal = base._normalizar(texto)
    m_etapa = re.search(r"\bEtapa actual\s*:", normal, re.I)
    if not m_etapa:
        return None
    previo = normal[:m_etapa.start()]
    m_atras = list(re.finditer(r"\bAtrás\b", previo, re.I))
    bloque = previo[m_atras[-1].end():] if m_atras else previo[-2500:]
    patrones = (r"Conselleria\s+[^:]{1,180}", r"Labora\s+[^:]{1,180}", r"Ag[eè]ncia\s+[^:]{1,180}", r"Institut\s+[^:]{1,180}", r"Instituto\s+[^:]{1,180}", r"Turisme Comunitat Valenciana", r"Generalitat Valenciana")
    candidatos = []
    for patron in patrones:
        for m in re.finditer(patron, bloque, re.I):
            candidatos.append((m.start(), base._normalizar(m.group(0))))
    return max(candidatos, key=lambda x: x[0])[1] if candidatos else None


def _extraer_enlace_organismo_html(html: str) -> str | None:
    """Extrae el href del enlace 'Enlace a organismo' de la ficha oficial."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for nodo in soup.find_all(string=re.compile(r"Enlace a organismo", re.I)):
        actual = nodo.parent
        if actual is None:
            continue
        candidatos = []
        if actual.name == "a":
            candidatos.append(actual)
        candidatos.extend(actual.find_all("a", href=True))
        if actual.parent is not None:
            candidatos.extend(actual.parent.find_all("a", href=True))
        for enlace in candidatos:
            href = (enlace.get("href") or "").strip()
            if href and not href.startswith("#"):
                return href
    return None


def _es_externo_por_texto(titulo: str, organismo_texto: str | None) -> bool:
    evidencia = base._sin_acentos(f"{titulo} {organismo_texto or ''}")
    externos = ("administracion de justicia", "tramitacion procesal", "gestion procesal", "auxilio judicial", "orden pjc/", "ministerio de justicia", "secretaria de estado de justicia", "istecdigital", "iislafe", "instituto de investigacion sanitaria la fe")
    return any(marca in evidencia for marca in externos)


def _es_generalitat_texto(organismo_texto: str | None) -> bool:
    normal = base._sin_acentos(organismo_texto or "")
    return bool(normal) and (
        normal.startswith("conselleria ")
        or "labora" in normal
        or "agencia valenciana" in normal
        or "institut valencia" in normal
        or "instituto valenciano" in normal
        or "turisme comunitat valenciana" in normal
        or "generalitat valenciana" in normal
    )


def _host(url: str | None) -> str:
    try:
        candidata = (url or "").strip()
        if not candidata:
            return ""
        if "://" not in candidata:
            candidata = f"https://{candidata}"
        return urlparse(candidata).netloc.lower().split(":", 1)[0].rstrip(".")
    except Exception:
        return ""


def _resolver_organismo(titulo: str, organismo_texto: str | None, organismo_enlace: str | None) -> tuple[int | None, str]:
    # La evidencia textual de la ficha tiene prioridad sobre el enlace cuando
    # este apunta a un portal transversal de la Generalitat (p.ej. cjusticia).
    # Primero descartamos procesos que son materialmente estatales/externos.
    if _es_externo_por_texto(titulo, organismo_texto):
        return None, "organismo_externo"

    if _es_generalitat_texto(organismo_texto):
        return base.GVA_ORGANISMO_ID, "generalitat_valenciana"

    host = _host(organismo_enlace)
    if host == "cjusticia.gva.es" or host.endswith(".istecdigital.es") or host.endswith(".iislafe.es"):
        return None, "organismo_externo"
    if host == "gva.es" or host.endswith(".gva.es"):
        return base.GVA_ORGANISMO_ID, "generalitat_valenciana"

    if organismo_texto:
        return None, "organismo_no_pertenece_a_generalitat"
    if organismo_enlace:
        return None, "organismo_externo"
    return None, "organismo_no_identificado"


def parsear_detalle(url: str, html: str, id_emp: int) -> dict[str, Any]:
    proceso = _BASE_PARSEAR_DETALLE(url, html, id_emp)
    texto = proceso["publicacion"]["contenido_texto"]
    titulo = proceso["denominacion"]
    organismo_texto = _extraer_organismo(texto)
    organismo_enlace = _extraer_enlace_organismo_html(html)
    organismo_id, motivo = _resolver_organismo(titulo, organismo_texto, organismo_enlace)
    proceso["tipo_proceso"] = _tipo_convocatoria(texto) or proceso.get("tipo_proceso")
    proceso["turno"] = _turno(titulo) or _turno(texto)
    proceso["organismo_id"] = organismo_id
    proceso["datos_json"] = {
        **(proceso.get("datos_json") or {}),
        "organismo_detectado": organismo_texto,
        "organismo_enlace": organismo_enlace,
        "organismo_id_resuelto": organismo_id,
        "organismo_motivo": motivo,
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
            cursor.execute("SELECT id FROM procesos WHERE organismo_id=%s AND denominacion='Navegación' AND datos_json->>'organismo_detectado'='Navegación'", (base.GVA_ORGANISMO_ID,))
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
