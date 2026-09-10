from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from .ambito_administrativo import clasificar_ambito_administrativo

BOE_SUMARIO = "https://www.boe.es/datosabiertos/api/boe/sumario/{fecha}"
SECCION_OPOSICIONES = "II. B. Oposiciones y concursos"
DEPARTAMENTO_LOCAL = "ADMINISTRACIÓN LOCAL"
PROVINCIAS = ("alicante", "castellon", "castellón", "valencia", "valència")


def _lista(valor: Any) -> list[Any]:
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


def _texto_url(valor: Any) -> str | None:
    if isinstance(valor, str):
        return valor.strip() or None
    if isinstance(valor, dict):
        texto = valor.get("texto")
        return str(texto).strip() if texto else None
    return None


def _iter_items_locales(data: dict[str, Any]):
    sumario = ((data.get("data") or {}).get("sumario") or {})
    for diario in _lista(sumario.get("diario")):
        for seccion in _lista((diario or {}).get("seccion")):
            nombre_seccion = str((seccion or {}).get("nombre") or "").strip()
            if nombre_seccion != SECCION_OPOSICIONES:
                continue
            for departamento in _lista((seccion or {}).get("departamento")):
                nombre_departamento = str((departamento or {}).get("nombre") or "").strip()
                if nombre_departamento != DEPARTAMENTO_LOCAL:
                    continue
                for epigrafe in _lista((departamento or {}).get("epigrafe")):
                    nombre_epigrafe = str((epigrafe or {}).get("nombre") or "").strip()
                    for item in _lista((epigrafe or {}).get("item")):
                        if isinstance(item, dict):
                            yield nombre_seccion, nombre_departamento, nombre_epigrafe, item


def diagnosticar_boe_local(*, hasta: date | None = None, dias: int = 30) -> dict[str, Any]:
    """SOLO LECTURA. Descubre convocatorias administrativas locales CV desde la API oficial BOE."""
    hasta = hasta or date.today()
    desde = hasta - timedelta(days=max(0, dias - 1))
    hallazgos: list[dict[str, Any]] = []
    errores: list[dict[str, str]] = []
    vistos: set[str] = set()
    headers = {
        "Accept": "application/json",
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
    }

    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        fecha = desde
        while fecha <= hasta:
            try:
                r = client.get(BOE_SUMARIO.format(fecha=fecha.strftime("%Y%m%d")))
                if r.status_code == 404:
                    fecha += timedelta(days=1)
                    continue
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                errores.append({"fecha": fecha.isoformat(), "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
                fecha += timedelta(days=1)
                continue

            for seccion, departamento, epigrafe, item in _iter_items_locales(data):
                ident = str(item.get("identificador") or "").strip()
                titulo = str(item.get("titulo") or "").strip()
                if not ident or ident in vistos or not titulo:
                    continue

                contexto = f"{epigrafe} {titulo}".lower()
                if not any(p in contexto for p in PROVINCIAS):
                    continue

                ambito = clasificar_ambito_administrativo(
                    {"denominacion": f"{epigrafe}. {titulo}", "cuerpo_escala": None, "grupo": None}
                )
                if ambito != "SI":
                    continue

                vistos.add(ident)
                hallazgos.append(
                    {
                        "fecha": fecha.isoformat(),
                        "boe_id": ident,
                        "seccion": seccion,
                        "departamento": departamento,
                        "epigrafe": epigrafe,
                        "titulo": titulo,
                        "url_html": _texto_url(item.get("url_html")),
                        "url_xml": _texto_url(item.get("url_xml")),
                        "url_pdf": _texto_url(item.get("url_pdf")),
                        "ambito_administrativo": ambito,
                    }
                )
            fecha += timedelta(days=1)

    return {
        "modo": "SOLO_LECTURA",
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "hallazgos": len(hallazgos),
        "dias_con_error": len(errores),
        "errores": errores,
        "detalle": hallazgos,
    }
