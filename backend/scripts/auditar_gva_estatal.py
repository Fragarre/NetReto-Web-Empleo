from __future__ import annotations

import argparse
import json
import math
import re
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

BASE = "https://administracion.gob.es"
RESULTADOS = f"{BASE}/pagFront/ofertasempleopublico/resultadosEmpleo.htm"
DETALLE = f"{BASE}/pagFront/ofertasempleopublico/detalleEmpleo.htm"
UA = "NetReto-Empleo-Auditoria/1.0 (https://netexamenes.com)"
TAM_PAGINA = 100

CODIGOS_ADMIN = ("A1-01", "A2-01", "A2-05", "C1-01", "C1-07", "C2-01")
PATRONES_ADMIN = (
    r"\bsuperior de administracion\b",
    r"\bsuperior de gestion\b",
    r"\bcuerpo administrativo\b",
    r"\bcuerpo auxiliar\b",
    r"\badministrativ[oa]\b",
    r"\bauxiliar administrativ[oa]\b",
    r"\btecnico tributario\b",
    r"\bagente[s]? tributario[s]?\b",
    r"\bgestion tributaria\b",
)
PATRONES_NO_ADMIN = (
    r"\bingenier", r"\barquitect", r"\bmedic", r"\benfermer", r"\bpsicolog",
    r"\bveterinar", r"\binvestig", r"\bbibliotec", r"\bsubaltern", r"\bconserje",
    r"\bprofesor", r"\bdocente", r"\btraductor", r"\bdelineant", r"\bforestal",
)
PATRONES_ORGANISMO_GVA = (
    "generalitat valenciana", "generalitat", "conselleria", "presidencia de la generalitat",
    "vicepresidencia", "labora", "agencia valenciana", "agencia valenciana",
    "agencia tributaria valenciana", "institut valencia", "instituto valenciano",
)
PATRONES_ORGANISMO_NO_GVA = (
    "universitat", "universidad", "ajuntament", "ayuntamiento", "diputacion", "diputacio",
)


def sin_acentos(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").lower()


def limpio(texto: str) -> str:
    return " ".join((texto or "").replace("\xa0", " ").split())


def get_con_reintentos(client: httpx.Client, url: str, *, params: dict | None = None) -> httpx.Response:
    ultimo: Exception | None = None
    for intento in range(1, 5):
        try:
            r = client.get(url, params=params)
            r.raise_for_status()
            return r
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            ultimo = exc
            if intento < 4:
                time.sleep(2 * intento)
    assert ultimo is not None
    raise ultimo


def fecha_iter(inicio: date, fin: date):
    actual = inicio
    while actual <= fin:
        yield actual
        actual += timedelta(days=1)


def parse_total(soup: BeautifulSoup) -> int | None:
    texto = limpio(soup.get_text(" ", strip=True))
    m = re.search(r"Total resultados:\s*([\d.]+)", texto, re.I)
    return int(m.group(1).replace(".", "")) if m else None


def parse_pagina_actual(soup: BeautifulSoup) -> int | None:
    inp = soup.find("input", {"id": "numPaginaActual"})
    if not inp:
        return None
    try:
        return int(inp.get("value"))
    except (TypeError, ValueError):
        return None


def parse_paginas_totales(soup: BeautifulSoup) -> int | None:
    inp = soup.find("input", {"id": "numPaginasTotales"})
    if not inp:
        return None
    try:
        return int(inp.get("value"))
    except (TypeError, ValueError):
        return None


def parse_tarjetas(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    salida: list[dict] = []
    for div in soup.select("div.resultado_empleo"):
        texto = limpio(div.get_text(" ", strip=True))
        enlace = div.find("a", href=re.compile(r"idConvocatoria=\d+"))
        if not enlace:
            continue
        href = enlace.get("href") or ""
        m = re.search(r"idConvocatoria=(\d+)", href)
        if not m:
            continue
        ref = int(m.group(1))
        titulo = limpio(enlace.get_text(" ", strip=True))
        m_plazas = re.search(r"Plazas:\s*([^F]+?)(?=\s+Fin de plazo:)", texto, re.I)
        m_fin = re.search(r"Fin de plazo:\s*(\d{2}/\d{2}/\d{4})", texto, re.I)
        m_ubic = re.search(r"Ubicaci[oó]n:\s*(.+?)(?=\s+[ÓO]rgano convocante:)", texto, re.I)
        m_org = re.search(r"[ÓO]rgano convocante:\s*(.+)$", texto, re.I)
        salida.append({
            "referencia": ref,
            "titulo": titulo,
            "plazas_literal": limpio(m_plazas.group(1)) if m_plazas else None,
            "fin_plazo": m_fin.group(1) if m_fin else None,
            "ubicacion": limpio(m_ubic.group(1)) if m_ubic else None,
            "organo": limpio(m_org.group(1)) if m_org else None,
            "url": urljoin(BASE, href),
            "texto_tarjeta": texto,
        })
    return salida


def clasificar_organismo(organo: str | None) -> str:
    n = sin_acentos(organo or "")
    if any(p in n for p in PATRONES_ORGANISMO_NO_GVA):
        return "NO"
    if any(p in n for p in PATRONES_ORGANISMO_GVA):
        return "SI"
    return "REVISION"


def clasificar_admin(titulo: str, detalle: str) -> tuple[str, list[str]]:
    n_titulo = sin_acentos(titulo)
    n_detalle = sin_acentos(detalle)
    codigos = sorted({c for c in CODIGOS_ADMIN if c.lower() in n_detalle})
    if any(re.search(p, n_titulo) for p in PATRONES_NO_ADMIN):
        return "NO", codigos
    if codigos or any(re.search(p, n_titulo) for p in PATRONES_ADMIN):
        return "SI", codigos
    return "REVISION", codigos


def parse_detalle(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    texto = limpio(soup.get_text(" ", strip=True))
    n = sin_acentos(texto)
    if "promocion interna" in n:
        acceso = "PROMOCION_INTERNA"
    elif "ingreso libre" in n or "acceso libre" in n:
        acceso = "LIBRE"
    else:
        acceso = "REVISION"
    seguimientos = []
    for etiqueta, patrones in (
        ("CORRECCION_ERRORES", ("correccion de errores", "correcció d'errors")),
        ("ADMITIDOS_PROVISIONAL", ("admitidos y/o excluidos provisional",)),
        ("ADMITIDOS_DEFINITIVO", ("admitidos y/o excluidos definitivo",)),
        ("TRIBUNAL", ("tribunal",)),
        ("FECHA_EXAMEN", ("fecha de examen", "fecha examen")),
        ("RESULTADOS", ("resultado", "aprobados", "personas aprobadas")),
        ("NOMBRAMIENTO", ("nombramiento", "nomenament")),
        ("DESTINOS", ("destino", "destinacion")),
    ):
        if any(p in n for p in patrones):
            seguimientos.append(etiqueta)
    return {"texto": texto, "acceso": acceso, "seguimientos": sorted(set(seguimientos))}


def params_dia(f: str, pagina: int = 1) -> dict:
    p = {
        "tipoBusqueda": "CONVOCATORIAS",
        "buscar": "true",
        "tipoFechas": "intervaloFechas",
        "fechaPublicacionDesde": f,
        "fechaPublicacionHasta": f,
        "orders": "id",
        "sort": "desc",
        "tam": str(TAM_PAGINA),
    }
    if pagina > 1:
        p["desde"] = str(pagina)
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="Auditoría SOLO LECTURA de convocatorias GVA en administracion.gob.es")
    ap.add_argument("--desde", default="2026-01-01")
    ap.add_argument("--hasta", default=date.today().isoformat())
    ap.add_argument("--salida", default="auditoria_gva_estatal.json")
    args = ap.parse_args()
    inicio = date.fromisoformat(args.desde)
    fin = date.fromisoformat(args.hasta)

    headers = {"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"}
    errores: list[dict] = []
    tarjetas_cv: dict[int, dict] = {}
    dias_consultados = 0
    dias_con_resultados = 0
    paginas_consultadas = 0

    with httpx.Client(timeout=httpx.Timeout(45.0, connect=15.0), headers=headers, follow_redirects=True) as client:
        for dia in fecha_iter(inicio, fin):
            dias_consultados += 1
            f = dia.strftime("%d/%m/%Y")
            try:
                r = get_con_reintentos(client, RESULTADOS, params=params_dia(f, 1))
                paginas_consultadas += 1
            except Exception as exc:
                errores.append({"tipo": "ERROR_DIA", "fecha": dia.isoformat(), "pagina": 1, "error": f"{type(exc).__name__}: {exc}"})
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            total = parse_total(soup)
            pagina_actual = parse_pagina_actual(soup)
            paginas_html = parse_paginas_totales(soup)
            tarjetas_dia: dict[int, dict] = {t["referencia"]: t for t in parse_tarjetas(r.text)}

            if total:
                dias_con_resultados += 1
            if total is None:
                errores.append({"tipo": "TOTAL_NO_LOCALIZADO", "fecha": dia.isoformat()})
                total = len(tarjetas_dia)

            paginas_calculadas = max(1, math.ceil(total / TAM_PAGINA)) if total else 1
            if pagina_actual not in (None, 1):
                errores.append({"tipo": "PAGINA_INICIAL_INESPERADA", "fecha": dia.isoformat(), "pagina_actual": pagina_actual})
            if paginas_html is not None and paginas_html != paginas_calculadas:
                errores.append({
                    "tipo": "TOTAL_PAGINAS_INCONSISTENTE",
                    "fecha": dia.isoformat(),
                    "total": total,
                    "paginas_html": paginas_html,
                    "paginas_calculadas": paginas_calculadas,
                })

            for pagina in range(2, paginas_calculadas + 1):
                try:
                    rp = get_con_reintentos(client, RESULTADOS, params=params_dia(f, pagina))
                    paginas_consultadas += 1
                except Exception as exc:
                    errores.append({"tipo": "ERROR_DIA", "fecha": dia.isoformat(), "pagina": pagina, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                sp = BeautifulSoup(rp.text, "html.parser")
                actual = parse_pagina_actual(sp)
                if actual != pagina:
                    errores.append({
                        "tipo": "PAGINA_NO_RESPETADA",
                        "fecha": dia.isoformat(),
                        "pagina_solicitada": pagina,
                        "pagina_recibida": actual,
                    })
                for t in parse_tarjetas(rp.text):
                    tarjetas_dia[t["referencia"]] = t

            if len(tarjetas_dia) != total:
                errores.append({
                    "tipo": "PAGINACION_INCOMPLETA",
                    "fecha": dia.isoformat(),
                    "total": total,
                    "tarjetas_unicas": len(tarjetas_dia),
                    "paginas_esperadas": paginas_calculadas,
                })

            for t in tarjetas_dia.values():
                if "AUTONÓMICO - COMUNITAT VALENCIANA" in (t.get("ubicacion") or "").upper():
                    t["fecha_publicacion_busqueda"] = dia.isoformat()
                    tarjetas_cv[t["referencia"]] = t

        candidatos: list[dict] = []
        for num, ref in enumerate(sorted(tarjetas_cv), start=1):
            tarjeta = tarjetas_cv[ref]
            try:
                r = get_con_reintentos(client, DETALLE, params={"idConvocatoria": ref, "idioma": "es"})
                det = parse_detalle(r.text)
            except Exception as exc:
                errores.append({"tipo": "ERROR_DETALLE", "referencia": ref, "error": f"{type(exc).__name__}: {exc}"})
                continue
            ambito_admin, codigos = clasificar_admin(tarjeta["titulo"], det["texto"])
            organismo_gva = clasificar_organismo(tarjeta.get("organo"))
            item = {
                **tarjeta,
                "organismo_gva": organismo_gva,
                "ambito_administrativo": ambito_admin,
                "codigos_administrativos": codigos,
                "acceso": det["acceso"],
                "seguimientos_detectados": det["seguimientos"],
            }
            if organismo_gva != "NO" and ambito_admin != "NO":
                candidatos.append(item)
            if num % 25 == 0:
                print(f"Detalles revisados: {num}/{len(tarjetas_cv)}")

    incluidos = [x for x in candidatos if x["organismo_gva"] == "SI" and x["ambito_administrativo"] == "SI" and x["acceso"] == "LIBRE"]
    revision = [x for x in candidatos if x not in incluidos and x["acceso"] != "PROMOCION_INTERNA"]
    promocion = [x for x in candidatos if x["acceso"] == "PROMOCION_INTERNA"]
    informe = {
        "modo": "SOLO_LECTURA",
        "fuente": "administracion.gob.es",
        "desde": inicio.isoformat(),
        "hasta": fin.isoformat(),
        "dias_consultados": dias_consultados,
        "dias_con_resultados": dias_con_resultados,
        "paginas_consultadas": paginas_consultadas,
        "tarjetas_autonomico_cv": len(tarjetas_cv),
        "incluidos_automaticos": incluidos,
        "revision_manual": revision,
        "promocion_interna_excluida": promocion,
        "errores": errores,
        "auditoria_completa": len(errores) == 0,
    }
    Path(args.salida).write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "modo": informe["modo"],
        "desde": informe["desde"],
        "hasta": informe["hasta"],
        "dias_consultados": dias_consultados,
        "paginas_consultadas": paginas_consultadas,
        "tarjetas_autonomico_cv": len(tarjetas_cv),
        "incluidos_automaticos": len(incluidos),
        "revision_manual": len(revision),
        "promocion_interna_excluida": len(promocion),
        "errores": len(errores),
        "auditoria_completa": informe["auditoria_completa"],
        "referencias_incluidas": [x["referencia"] for x in incluidos],
    }, ensure_ascii=False, indent=2))
    return 0 if not errores else 2


if __name__ == "__main__":
    raise SystemExit(main())
