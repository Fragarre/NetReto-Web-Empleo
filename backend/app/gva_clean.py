from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

GVA_SEARCH_URL = "https://sede.gva.es/es/cercador-ocupacio-publica"
GVA_BASE_URL = "https://sede.gva.es"
GVA_ORGANISMO_ID = 1
GVA_FUENTE_ID = 1

TIPOS_INCLUIDOS = {
    "oposicion", "bolsa de trabajo", "promocion interna", "contratacion laboral temporal",
    "contratacion laboral indefinida", "proceso de estabilizacion", "acto unico telematico",
    "acte unic telematic", "anuncio dificil cobertura", "concurso general de meritos",
    "concurso-oposicion", "concurso", "cobertura interina", "comision de servicio",
    "libre designacion", "seleccion personal directivo", "procesos especiales",
}

def _normalizar(texto: str) -> str:
    return " ".join(texto.replace("\xa0", " ").split())

def _sin_acentos(texto: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")

def _fecha(valor: str | None) -> date | None:
    if not valor: return None
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", valor)
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None

def _anio(texto: str) -> int | None:
    normal = _sin_acentos(texto)
    for patron in (r"convocatoria\s+\d+[/-](\d{2})", r"convocatoria\s+\d{1,3}/(\d{4})"):
        m = re.search(patron, normal)
        if m:
            v = int(m.group(1)); return 2000 + v if v < 100 else v
    m = re.search(r"\b(2026|2027)\b", texto)
    return int(m.group(1)) if m else None

def _tipo_proceso(texto: str) -> str | None:
    m = re.search(r"Convocatoria\s*:?\s*(.*?)(?=\s+(?:Prueba|Grupo|Titulación|Titulacio|Enlace a organismo|Places|Plazas)\b|$)", texto, re.I)
    return _normalizar(m.group(1)) if m else None

def _plazas(texto: str) -> int | None:
    m = re.search(r"(?:Plazas|Places)\s*([\d.]+)", texto, re.I)
    if not m: return None
    try: return int(m.group(1).replace(".", ""))
    except ValueError: return None

def _grupo(texto: str) -> str | None:
    m = re.search(r"\bGrupo\s+([A-Z0-9/]+)", texto, re.I)
    return m.group(1).upper() if m else None

def _turno(texto: str) -> str | None:
    normal = _sin_acentos(texto); candidatos=[]
    for patron, valor in (("promocion interna","PROMOCION_INTERNA"),("turno libre","TURNO_LIBRE"),("discapacidad intelectual","DISCAPACIDAD_INTELECTUAL"),("discapacidad","DISCAPACIDAD")):
        p=normal.find(patron)
        if p>=0: candidatos.append((p,valor))
    return min(candidatos,key=lambda x:x[0])[1] if candidatos else None

def _estado(texto: str) -> str:
    normal=_sin_acentos(texto)
    if "plazo abierto" in normal or "abierto" in normal: return "ABIERTO"
    if "pendiente" in normal: return "PENDIENTE"
    if "cerrado" in normal or "tancat" in normal: return "CERRADO"
    return "EN_SEGUIMIENTO"

def _ultima_fecha_publicacion(texto: str) -> date | None:
    fechas=[_fecha(x) for x in re.findall(r"Fecha publicaci[oó]n\s*:\s*(\d{2}-\d{2}-\d{4})",texto,re.I)]
    fechas=[x for x in fechas if x]
    return max(fechas) if fechas else None

def _denominacion(soup: BeautifulSoup, texto: str) -> str:
    title=soup.title.get_text(" ",strip=True) if soup.title else ""
    title=_normalizar(title)
    title=re.sub(r"\s*-\s*Sede Electr[oó]nica\s*-\s*Generalitat Valenciana\s*$","",title,flags=re.I)
    if title and _sin_acentos(title)!="navegacion": return title
    h1=soup.find("h1")
    return _normalizar(h1.get_text(" ",strip=True)) if h1 else f"Proceso GVA {texto[:80]}"

def _es_incluido(tipo: str | None) -> bool:
    normal=_sin_acentos(tipo or "")
    return any(t in normal for t in TIPOS_INCLUIDOS)

def _es_del_ambito(anio_convocatoria: int | None, fecha_pub: date | None) -> bool:
    return anio_convocatoria in (2026,2027) or (fecha_pub is not None and fecha_pub>=date(2026,1,1))

def parsear_detalle(url: str, html: str, id_emp: int) -> dict[str, Any]:
    soup=BeautifulSoup(html,"html.parser"); texto=_normalizar(soup.get_text(" ",strip=True))
    denominacion=_denominacion(soup,texto); tipo=_tipo_proceso(texto); grupo=_grupo(texto); plazas=_plazas(texto)
    anio_convocatoria=_anio(denominacion+" "+texto)
    m_plazo=re.search(r"(?:Plazo|Termini)\s+(?:de la etapa actual\s*)?(?:Desde|Des de)\s+(\d{2}-\d{2}-\d{4})\s+(?:hasta|a)\s+(\d{2}-\d{2}-\d{4})",texto,re.I)
    fecha_apertura=_fecha(m_plazo.group(1)) if m_plazo else None; fecha_cierre=_fecha(m_plazo.group(2)) if m_plazo else None
    m_codigo=re.search(r"C[oó]digo GVA\s*:?\s*(\d+)",texto,re.I); codigo_gva=m_codigo.group(1) if m_codigo else str(id_emp)
    m_sia=re.search(r"C[oó]digo SIA\s*:?\s*([0-9]+)",texto,re.I); codigo_sia=m_sia.group(1) if m_sia else None
    organismo=None
    marker=re.search(r"Enlace a organismo\s+(.+?)\s+Plazas\b",texto,re.I)
    if marker: organismo=_normalizar(marker.group(1))
    fecha_pub=_ultima_fecha_publicacion(texto); hash_contenido=hashlib.sha256(html.encode("utf-8")).hexdigest()
    return {"codigo_externo":codigo_gva,"identificador_estable":f"GVA:{id_emp}","denominacion":denominacion,"grupo":grupo,"tipo_proceso":tipo,"turno":_turno(denominacion+" "+texto),"plazas":plazas,"estado":_estado(texto),"anio_convocatoria":anio_convocatoria,"fecha_apertura":fecha_apertura,"fecha_cierre":fecha_cierre,"ultima_publicacion_at":datetime.combine(fecha_pub,datetime.min.time(),tzinfo=timezone.utc) if fecha_pub else None,"datos_json":{"id_emp":id_emp,"codigo_gva":codigo_gva,"codigo_sia":codigo_sia,"organismo_detectado":organismo,"url_detalle":url},"publicacion":{"referencia":f"GVA:{id_emp}:{hash_contenido}","tipo":"DETALLE","titulo":denominacion,"fecha_publicacion":fecha_pub,"url":url,"contenido_hash":hash_contenido,"contenido_texto":texto,"datos_json":{"id_emp":id_emp,"codigo_gva":codigo_gva}}}

def descubrir_detalles(client: httpx.Client,max_paginas:int=3)->list[tuple[int,str]]:
    encontrados={}
    for pagina in range(1,max_paginas+1):
        respuesta=client.get(GVA_SEARCH_URL,params={"pagina":pagina,"tipoOrganismo":"1","plazos":"A","tamanyoPagina":"30"}); respuesta.raise_for_status()
        soup=BeautifulSoup(respuesta.text,"html.parser")
        for enlace in soup.select('a[href*="detall-ocupacio-publica"]'):
            href=enlace.get("href");
            if not href: continue
            match=re.search(r"id_emp=(\d+)",href)
            if match: encontrados[int(match.group(1))]=urljoin(GVA_BASE_URL,href)
    return sorted(encontrados.items())

# Importer remains unchanged in this restoration; organism validation is implemented in the next commit.
