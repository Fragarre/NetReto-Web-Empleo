from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from .ambito_administrativo import clasificar_ambito_administrativo

BOE_SUMARIO = "https://www.boe.es/datosabiertos/api/boe/sumario/{fecha}"
PROVINCIAS = ("alicante", "castellon", "castellón", "valencia", "valència")


def _iter_items(nodo: Any):
    if isinstance(nodo, dict):
        if "identificador" in nodo and ("titulo" in nodo or "texto" in nodo):
            yield nodo
        for valor in nodo.values():
            yield from _iter_items(valor)
    elif isinstance(nodo, list):
        for valor in nodo:
            yield from _iter_items(valor)


def diagnosticar_boe_local(*, hasta: date | None = None, dias: int = 30) -> dict[str, Any]:
    """SOLO LECTURA. Descubre posibles convocatorias administrativas locales CV desde la API oficial BOE."""
    hasta = hasta or date.today()
    desde = hasta - timedelta(days=max(0, dias - 1))
    hallazgos: list[dict[str, Any]] = []
    errores: list[dict[str, str]] = []
    vistos: set[str] = set()
    headers = {"Accept": "application/json", "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)"}

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

            for item in _iter_items(data):
                ident = str(item.get("identificador") or "").strip()
                titulo = str(item.get("titulo") or item.get("texto") or "").strip()
                if not ident or ident in vistos or not titulo:
                    continue
                bajo = titulo.lower()
                if not any(p in bajo for p in PROVINCIAS):
                    continue
                ambito = clasificar_ambito_administrativo({"denominacion": titulo, "cuerpo_escala": None, "grupo": None})
                if ambito != "SI":
                    continue
                vistos.add(ident)
                hallazgos.append({"fecha": fecha.isoformat(), "boe_id": ident, "titulo": titulo, "ambito_administrativo": ambito})
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
