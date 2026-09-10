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


def _muestra_diario(data: dict[str, Any]) -> dict[str, Any]:
    sumario = ((data.get("data") or {}).get("sumario") or {})
    diario = sumario.get("diario")
    muestra: dict[str, Any] = {
        "tipo_diario": type(diario).__name__,
        "cantidad_diarios": len(diario) if isinstance(diario, list) else (1 if diario is not None else 0),
    }
    primero = diario[0] if isinstance(diario, list) and diario else diario
    if isinstance(primero, dict):
        muestra["claves_primer_diario"] = list(primero.keys())
        seccion = primero.get("seccion")
        muestra["tipo_seccion"] = type(seccion).__name__
        muestra["cantidad_secciones"] = len(seccion) if isinstance(seccion, list) else (1 if seccion is not None else 0)
        primeras = _lista(seccion)[:5]
        muestra["primeras_secciones"] = [
            {
                "claves": list(s.keys()) if isinstance(s, dict) else [],
                "codigo": s.get("codigo") if isinstance(s, dict) else None,
                "nombre": s.get("nombre") if isinstance(s, dict) else None,
            }
            for s in primeras
        ]
    else:
        muestra["valor_primer_diario"] = str(primero)[:500]
    return muestra


def diagnosticar_boe_local(*, hasta: date | None = None, dias: int = 30) -> dict[str, Any]:
    """SOLO LECTURA. Diagnóstico por etapas de convocatorias locales CV desde BOE."""
    hasta = hasta or date.today()
    desde = hasta - timedelta(days=max(0, dias - 1))
    hallazgos: list[dict[str, Any]] = []
    errores: list[dict[str, str]] = []
    vistos: set[str] = set()
    items_locales = 0
    candidatos_cv = 0
    descartados_ambito: list[dict[str, str]] = []
    muestras_raiz: list[dict[str, Any]] = []
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
                if len(muestras_raiz) < 3:
                    muestras_raiz.append({
                        "fecha": fecha.isoformat(),
                        "claves_raiz": list(data.keys()) if isinstance(data, dict) else [],
                        "claves_data": list((data.get("data") or {}).keys()) if isinstance(data, dict) and isinstance(data.get("data"), dict) else [],
                        "claves_sumario": list((((data.get("data") or {}).get("sumario") or {}).keys())) if isinstance(data, dict) and isinstance((data.get("data") or {}).get("sumario"), dict) else [],
                        "diario": _muestra_diario(data) if isinstance(data, dict) else {},
                    })
            except Exception as exc:
                errores.append({"fecha": fecha.isoformat(), "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
                fecha += timedelta(days=1)
                continue

            for seccion, departamento, epigrafe, item in _iter_items_locales(data):
                items_locales += 1
                ident = str(item.get("identificador") or "").strip()
                titulo = str(item.get("titulo") or "").strip()
                if not ident or ident in vistos or not titulo:
                    continue

                contexto = f"{epigrafe} {titulo}".lower()
                if not any(p in contexto for p in PROVINCIAS):
                    continue
                candidatos_cv += 1

                ambito = clasificar_ambito_administrativo(
                    {"denominacion": f"{epigrafe}. {titulo}", "cuerpo_escala": None, "grupo": None}
                )
                if ambito != "SI":
                    if len(descartados_ambito) < 20:
                        descartados_ambito.append({"fecha": fecha.isoformat(), "boe_id": ident, "titulo": titulo, "ambito": ambito})
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
        "items_locales": items_locales,
        "candidatos_cv": candidatos_cv,
        "descartados_ambito": descartados_ambito,
        "muestras_estructura": muestras_raiz,
        "hallazgos": len(hallazgos),
        "dias_con_error": len(errores),
        "errores": errores,
        "detalle": hallazgos,
    }
