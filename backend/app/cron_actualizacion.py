from __future__ import annotations

import os
import sys
from datetime import date

import httpx


BASE_URL = os.getenv("NETRETO_EMPLEO_API_URL", "https://netreto-empleo-api.onrender.com").rstrip("/")
SECRET = os.getenv("EMPLOYMENT_IMPORT_SECRET")

# Solape técnico para tolerar fallos temporales y reintentos. NO es un criterio
# de inclusión: una oportunidad permanece en catálogo mientras el proceso
# selectivo siga activo, cualquiera que sea su antigüedad.
VENTANA_SOLAPE_DIAS = 30


def _post(client: httpx.Client, path: str) -> None:
    response = client.post(
        f"{BASE_URL}{path}",
        headers={"X-Import-Secret": SECRET or ""},
    )
    response.raise_for_status()
    print(path, response.json())


def main() -> int:
    if not SECRET:
        print("Falta EMPLOYMENT_IMPORT_SECRET", file=sys.stderr)
        return 2

    hoy = date.today().isoformat()
    with httpx.Client(timeout=300.0) as client:
        # GVA: descubre nuevas fichas y vuelve a consultar todos los procesos
        # conocidos no terminales, aunque su plazo de inscripción haya cerrado.
        _post(client, "/admin/import/gva?max_paginas=10")

        # Diputación de Valencia: publicaciones recientes del BOP con solape.
        _post(client, f"/admin/import/bop-valencia?historico=true&dias={VENTANA_SOLAPE_DIAS}")

        # Administración local: el solape solo descubre publicaciones nuevas.
        # La antigüedad nunca determina si un proceso sigue siendo oportunidad.
        _post(client, f"/admin/gestion/import/boe-local?hasta={hoy}&dias={VENTANA_SOLAPE_DIAS}&aplicar=true")
        _post(client, f"/admin/gestion/import/bop-municipios?hasta={hoy}&dias={VENTANA_SOLAPE_DIAS}&aplicar=true")

        _post(client, "/admin/seguimiento/preparar-notificaciones")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
