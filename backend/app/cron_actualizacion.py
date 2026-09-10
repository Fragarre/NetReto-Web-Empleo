from __future__ import annotations

import os
import sys

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

    with httpx.Client(timeout=120.0) as client:
        _post(client, "/admin/import/gva?max_paginas=3")
        _post(client, "/admin/import/bop-valencia?historico=false&dias=30")
        _post(client, "/admin/import/boe-local?dias=30&aplicar=true")
        _post(client, "/admin/seguimiento/preparar-notificaciones")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
