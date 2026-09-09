from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import bop_valencia as _bop
from . import bop_valencia_patch as _bop_patch
from .ambito_administrativo import clasificar_ambito_administrativo
from .database import get_connection


def _sin(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def _reparar_mojibake_utf8(texto: str) -> str:
    if not texto or not any(m in texto for m in ("Ã", "Â", "â€", "â€™", "â€œ", "â€")):
        return texto
    for encoding in ("latin-1", "cp1252"):
        try:
            reparado = texto.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if reparado.count("Ã") + reparado.count("Â") < texto.count("Ã") + texto.count("Â"):
            return reparado
    return texto


def _es_emisor_ayuntamiento(titulo: str) -> bool:
    n = _sin(titulo).strip()
    return bool(re.match(r"^(?:anunci|anuncio)\s+(?:(?:de|del)\s+)?(?:l['’]\s*)?(?:ayuntamiento|ajuntament)\s+(?:de|del|d['’])\s+", n, re.I))


def _municipio_desde_titulo(titulo: str) -> str | None:
    if not _es_emisor_ayuntamiento(titulo): return None
    n = _sin(titulo)
    fin = r"(?=\s+sobre\b|\s+per\s+a\b|\s+para\b|[.,;:]|$)"
    for patron in (rf"(?:ayuntamiento|ajuntament)\s+de\s+(.+?){fin}", rf"(?:ayuntamiento|ajuntament)\s+del\s+(.+?){fin}", rf"(?:ayuntamiento|ajuntament)\s+d['’]\s*(.+?){fin}"):
        m = re.search(patron, n, re.I)
        if m: return " ".join(m.group(1).split()).strip()[:120]
    return None


def _es_perfil_administrativo(titulo: str) -> bool:
    return clasificar_ambito_administrativo({"denominacion": titulo, "cuerpo_escala": None, "grupo": None}) == "SI"


def _clasificar_anuncio(titulo: str) -> str:
    n = _sin(titulo)
    internos = ("libre designacion","lliure designacio","comision de servicios","comissio de serveis","concurso de traslados","concurs de trasllats","concurso especifico de meritos","concurs especific de merits","provision de puesto","provisio de lloc","provision del puesto","provisio del lloc","abierto a otras administraciones publicas","obert a altres administracions publiques","promocion interna","promocio interna","cesion de la bolsa","cessio de la borsa","conveni de collaboracio","convenio de colaboracion")
    if any(x in n for x in internos): return "EXCLUIDO_INTERNO"
    ruido = ("subvencion","subvencio","premio","premi","ayuda","ajuda","ordenanza fiscal","ordenanca fiscal","tasa","taxa","gestion tributaria","gestio tributaria","recaptacio","recaudacion")
    if any(x in n for x in ruido) and not any(x in n for x in ("administratiu","administrativo","auxiliar administratiu","auxiliar administrativo","tecnic d'administracio","tecnico de administracion")): return "RUIDO"
    bolsas = ("borsa d'ocupacio","borsa de treball","bolsa de empleo","bolsa de trabajo","funcionari interi","funcionario interino","funcionaria interina","nomenament interi")
    if any(x in n for x in bolsas): return "BOLSA_TEMPORAL"
    seguimiento = ("relacio provisional","relacion provisional","relacio definitiva","relacion definitiva","admeses","admesos","admitidos","admitidas","exclosos","excloses","excluidos","excluidas","tribunal","organ tecnic de seleccio","organo tecnico de seleccion","primer exercici","primer ejercicio","data de l'exercici","fecha del ejercicio","nomenament","nombramiento","persona aprovada","persones aprovades","resultats","resultados","proposta de nomenament","propuesta de nombramiento","correccio d'errors","correccion de errores","modificacio","modificacion","resolucio de recursos","resolucion de recursos","acumulacio de places","acumulacion de plazas")
    if any(x in n for x in seguimiento): return "SEGUIMIENTO"
    bases = ("aprovacio de les bases","aprobacion de las bases","bases de la convocatoria","bases i la convocatoria","bases y la convocatoria","bases reguladores del procediment selectiu","bases reguladoras del procedimiento selectivo")
    return "NUEVA_CONVOCATORIA" if any(x in n for x in bases) else "SEGUIMIENTO"


def _es_empleo_administrativo(titulo: str) -> bool:
    return _es_emisor_ayuntamiento(titulo) and _es_perfil_administrativo(titulo)


def _extraer_anuncios_municipales(html: str) -> list[dict[str, Any]]:
    html = _reparar_mojibake_utf8(html)
    texto = _bop._norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    patron = re.compile(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d+)", re.I)
    resultados, vistos = [], set()
    for mr in patron.finditer(texto):
        numero = mr.group(1)
        inicio = max(texto.rfind("Anunci",0,mr.start()), texto.rfind("Anuncio",0,mr.start()))
        if inicio < 0: continue
        titulo = _bop._norm(texto[inicio:mr.start()]).rstrip(".") + "."
        municipio = _municipio_desde_titulo(titulo)
        if not municipio or numero in vistos: continue
        vistos.add(numero)
        contexto = texto[inicio:mr.end()+150]
        fm = re.search(r"(?:Data publicaci[oó]|Fecha publicaci[oó]n)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", contexto, re.I)
        fecha = _bop._fecha(fm.group(1)) if fm else None
        resultados.append({"titulo":titulo,"url":f"{_bop.DOWNLOAD_URL}?anuncioNumReg={quote(numero)}&lang=es","registro":numero,"fecha_publicacion":fecha,"municipio":municipio})
    return resultados


def descubrir_municipales_bop(*, hasta: date | None=None, dias: int=30) -> dict[str, Any]:
    hasta = hasta or date.today(); desde = hasta-timedelta(days=max(0,dias-1))
    headers={"User-Agent":"NetReto-Empleo/0.1 (https://netexamenes.com)","Accept-Language":"es-ES,es;q=0.9"}
    hallazgos, errores, vistos = [], [], set()
    with httpx.Client(timeout=30,headers=headers,follow_redirects=True) as client:
        fecha=desde
        while fecha<=hasta:
            try: _,html,_=_bop_patch._obtener_pagina(client,fecha)
            except Exception as exc:
                errores.append({"fecha":fecha.isoformat(),"error":f"{type(exc).__name__}: {str(exc)[:180]}"}); fecha+=timedelta(days=1); continue
            if html:
                for a in _extraer_anuncios_municipales(html):
                    if not _es_empleo_administrativo(a["titulo"]) or a["registro"] in vistos: continue
                    vistos.add(a["registro"]); hallazgos.append({"registro":a["registro"],"fecha_publicacion":a["fecha_publicacion"].isoformat() if a["fecha_publicacion"] else None,"municipio_detectado":a["municipio"],"clase":_clasificar_anuncio(a["titulo"]),"titulo":a["titulo"],"url":a["url"]})
            fecha+=timedelta(days=1)
    hallazgos.sort(key=lambda x:(x["fecha_publicacion"] or "",x["registro"])); conteo=Counter(h["clase"] for h in hallazgos); candidatas=[h for h in hallazgos if h["clase"]=="NUEVA_CONVOCATORIA"]
    return {"desde":desde.isoformat(),"hasta":hasta.isoformat(),"descubiertos":len(hallazgos),"resumen_clases":dict(sorted(conteo.items())),"candidatas_nuevas":len(candidatas),"dias_con_error":len(errores),"errores":errores,"hallazgos":hallazgos}


def _nombre_municipio(slug: str) -> str:
    return " ".join(p.capitalize() for p in slug.split())


def _extraer_plazas(titulo: str) -> int | None:
    n = _sin(titulo)
    palabras = {
        "una": 1, "un": 1,
        "dues": 2, "dos": 2,
        "tres": 3,
        "quatre": 4, "cuatro": 4,
        "cinc": 5, "cinco": 5,
        "sis": 6, "seis": 6,
        "set": 7, "siete": 7,
        "huit": 8, "vuit": 8, "ocho": 8,
        "nou": 9, "nueve": 9,
        "deu": 10, "diez": 10,
    }
    # Valenciano y castellano: plaça/places, plaza/plazas. _sin elimina los acentos.
    m = re.search(r"\b(\d+|una|un|dues|dos|tres|quatre|cuatro|cinc|cinco|sis|seis|set|siete|huit|vuit|ocho|nou|nueve|deu|diez)\s+(?:placa|places|plaza|plazas)\b", n)
    if not m:
        return None
    valor = m.group(1)
    return int(valor) if valor.isdigit() else palabras.get(valor)


def importar_municipales_bop(*, hasta: date, dias: int=30, aplicar: bool=False) -> dict[str, Any]:
    diagnostico=descubrir_municipales_bop(hasta=hasta,dias=dias); candidatas=[h for h in diagnostico["hallazgos"] if h["clase"]=="NUEVA_CONVOCATORIA"]
    resultado={"modo":"APLICAR" if aplicar else "SOLO_REVISION","desde":diagnostico["desde"],"hasta":diagnostico["hasta"],"candidatas":len(candidatas),"dias_con_error":diagnostico["dias_con_error"],"nuevos":0,"existentes":0,"organismos_creados":0,"detalle":[]}
    if not aplicar: resultado["detalle"]=candidatas; return resultado
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id FROM fuentes WHERE id=2")
        if not cursor.fetchone(): raise RuntimeError("No existe la fuente BOP Valencia esperada (id=2)")
        for h in candidatas:
            estable=f"BOPMUN:{h['registro']}"; cursor.execute("SELECT id FROM procesos WHERE identificador_estable=%s",(estable,)); ex=cursor.fetchone()
            if ex:
                resultado["existentes"]+=1; resultado["detalle"].append({"registro":h["registro"],"estado":"EXISTENTE","proceso_id":ex["id"]}); continue
            municipio=h["municipio_detectado"]; cursor.execute("SELECT id FROM organismos WHERE tipo='AYUNTAMIENTO' AND lower(COALESCE(municipio,''))=lower(%s) LIMIT 1",(municipio,)); org=cursor.fetchone()
            if org:
                organismo_id=org["id"]; cursor.execute("UPDATE organismos SET activo=TRUE,updated_at=NOW() WHERE id=%s",(organismo_id,))
            else:
                nombre=f"Ayuntamiento de {_nombre_municipio(municipio)}"; cursor.execute("INSERT INTO organismos (nombre,tipo,municipio,provincia,activo,created_at,updated_at) VALUES (%s,'AYUNTAMIENTO',%s,'Valencia',TRUE,NOW(),NOW()) RETURNING id",(nombre,municipio)); organismo_id=cursor.fetchone()["id"]; resultado["organismos_creados"]+=1
            cursor.execute("INSERT INTO procesos (organismo_id,codigo_externo,identificador_estable,denominacion,plazas,estado,fecha_convocatoria,fuente_principal_id,es_oportunidad,ambito_administrativo,datos_json,updated_at) VALUES (%s,%s,%s,%s,%s,'EN_CURSO',%s,2,TRUE,'SI',%s,NOW()) RETURNING id",(organismo_id,h["registro"],estable,h["titulo"],_extraer_plazas(h["titulo"]),h["fecha_publicacion"],Jsonb({"url_oficial":h["url"],"bop_registro":h["registro"],"origen":"BOP_VALENCIA_MUNICIPAL"}))); pid=cursor.fetchone()["id"]
            resultado["nuevos"]+=1; resultado["detalle"].append({"registro":h["registro"],"estado":"NUEVO","proceso_id":pid,"municipio":municipio})
        connection.commit()
    return resultado


def listar_municipios_detectados() -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id,nombre,municipio,activo FROM organismos WHERE tipo='AYUNTAMIENTO' ORDER BY municipio NULLS LAST,nombre"); rows=cursor.fetchall()
    return [{"id":r[0],"nombre":r[1],"municipio":r[2],"activo":r[3]} for r in rows]
