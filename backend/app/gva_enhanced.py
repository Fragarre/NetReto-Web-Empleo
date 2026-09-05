from __future__ import annotations

import re
from typing import Any

from . import gva_clean as base
from .database import get_connection


def _tipo_convocatoria(texto: str) -> str | None:
    normal = base._sin_acentos(texto)
    m = re.search(r"convocatoria\s+(.{1,120}?)(?:\s+prueba\s+|\s+grupo\s+|\s+titulacion\s+|\s+enlace a organismo\s+)", normal, re.I)
    if not m:
        return None
    valor = base._normalizar(m.group(1))
    patrones = (
        ("bolsa de trabajo", "Bolsa de trabajo"), ("oposicion", "Oposición"),
        ("promocion interna", "Promoción interna"), ("contratacion laboral temporal", "Contratación laboral temporal"),
        ("contratacion laboral indefinida", "Contratación laboral indefinida"), ("proceso de estabilizacion", "Proceso de estabilización"),
        ("acto unico telematico", "Acto único telemático"), ("acte unic telematic", "Acto único telemático"),
        ("anuncio dificil cobertura", "Anuncio difícil cobertura"), ("concurso general de meritos", "Concurso general de méritos"),
        ("concurso-oposicion", "Concurso-oposición"), ("concurso", "Concurso"),
        ("cobertura interina", "Cobertura interina"), ("comision de servicio", "Comisión de servicio"),
        ("libre designacion", "Libre designación"), ("seleccion personal directivo", "Selección personal directivo"),
        ("procesos especiales", "Procesos especiales"), ("otros", "Otros"),
    )
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
    return any(patron in normal for patron in (
        "oposicion", "bolsa de trabajo", "promocion interna", "contratacion laboral",
        "proceso de estabilizacion", "acto unico telematico", "acte unic telematic",
        "anuncio dificil cobertura", "concurso general de meritos", "concurso-oposicion",
        "concurso", "cobertura interina", "comision de servicio", "libre designacion",
        "seleccion personal directivo",
    ))


def _extraer_organismo(texto: str) -> str | None:
    """Extrae el organismo mostrado junto al encabezado del proceso.

    La página contiene mucha navegación repetida antes del detalle. Por eso
    no se toma la primera aparición de 'Conselleria', sino la última mención
    de un organismo inmediatamente antes de 'Etapa actual:'.
    """
    normal = base._normalizar(texto)
    m_etapa = re.search(r"\bEtapa actual\s*:", normal, re.I)
    if not m_etapa:
        return None
    previo = normal[:m_etapa.start()]
    patrones = (
        r"Conselleria\s+[^:]{1,180}",
        r"Labora\s+[^:]{1,180}",
        r"Ag[eè]ncia\s+[^:]{1,180}",
        r"Institut\s+[^:]{1,180}",
        r"Instituto\s+[^:]{1,180}",
        r"Turisme Comunitat Valenciana",
        r"Generalitat Valenciana",
    )
    candidatos = []
    for patron in patrones:
        for m in re.finditer(patron, previo, re.I):
            valor = base._normalizar(m.group(0))
            candidatos.append((m.start(), valor))
    if not candidatos:
        return None
    return max(candidatos, key=lambda x: x[0])[1]


def _resolver_organismo(titulo: str, organismo: str | None) -> tuple[int | None, str]:
    # Solo título + organismo intervienen en esta decisión. El resto de la
    # página contiene navegación institucional que no sirve como evidencia.
    evidencia = base._sin_acentos(f"{titulo} {organismo or ''}")
    externos = (
        "administracion de justicia", "tramitacion procesal", "gestion procesal",
        "auxilio judicial", "orden pjc/", "ministerio de justicia",
        "secretaria de estado de justicia",
    )
    if any(marca in evidencia for marca in externos):
        return None, "organismo_externo"
    org_normal = base._sin_acentos(organismo or "")
    if not org_normal:
        return None, "organismo_no_identificado"
    if (
        "generalitat valenciana" in org_normal
        or org_normal.startswith("conselleria ")
        or "labora" in org_normal
        or "agencia valenciana" in org_normal
        or "institut valencia" in org_normal
        or "instituto valenciano" in org_normal
        or "turisme comunitat valenciana" in org_normal
    ):
        return base.GVA_ORGANISMO_ID, "generalitat_valenciana"
    return None, "organismo_no_pertenece_a_generalitat"


def parsear_detalle(url: str, html: str, id_emp: int) -> dict[str, Any]:
    proceso = base.parsear_detalle(url, html, id_emp)
    titulo = proceso["denominacion"]
    organismo = _extraer_organismo(proceso["publicacion"]["contenido_texto"])
    organismo_id, motivo = _resolver_organismo(titulo, organismo)
    proceso["tipo_proceso"] = _tipo_convocatoria(proceso["publicacion"]["contenido_texto"]) or proceso.get("tipo_proceso")
    proceso["turno"] = _turno(f"{titulo} {proceso['publicacion']['contenido_texto']}")
    proceso["organismo_id"] = organismo_id
    proceso["datos_json"] = {
        **(proceso.get("datos_json") or {}),
        "organismo_detectado": organismo,
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
    """Elimina únicamente registros antiguos con denominación Navegación."""
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
