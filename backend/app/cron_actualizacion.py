from __future__ import annotations

import os
import sys
from datetime import date

import httpx


BASE_URL = os.getenv("NETRETO_EMPLEO_API_URL", "https://netreto-empleo-api.onrender.com").rstrip("/")
SECRET = os.getenv("EMPLOYMENT_IMPORT_SECRET")


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
    with httpx.Client(timeout=180.0) as client:
        _post(client, "/admin/import/gva?max_paginas=3")
        _post(client, f"/admin/gestion/import/boe-local?hasta={hoy}&dias=45&aplicar=true")
        _post(client, f"/admin/gestion/import/bop-municipios?hasta={hoy}&dias=30&aplicar=true")
        _post(client, "/admin/seguimiento/preparar-notificaciones")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
