from __future__ import annotations

import os
import sys
import time
from datetime import date

import httpx


BASE_URL = os.getenv("NETRETO_EMPLEO_API_URL", "https://netreto-empleo-api.onrender.com").rstrip("/")
SECRET = os.getenv("EMPLOYMENT_IMPORT_SECRET")

# Solape técnico para tolerar fallos temporales y reintentos. NO es un criterio
# de inclusión: una oportunidad permanece en catálogo mientras el proceso
# selectivo siga activo, cualquiera que sea su antigüedad.
VENTANA_SOLAPE_DIAS = 30

# Render puede estar reiniciando una instancia o una fuente oficial puede sufrir
# un fallo transitorio. Reintentamos solo errores de red y respuestas 5xx.
REINTENTOS = 3
ESPERAS_REINTENTO = (10, 30)


def _post(client: httpx.Client, path: str) -> None:
    ultima_excepcion: Exception | None = None

    for intento in range(1, REINTENTOS + 1):
        try:
            response = client.post(
                f"{BASE_URL}{path}",
                headers={"X-Import-Secret": SECRET or ""},
            )

            # Los 4xx son errores permanentes de configuración/solicitud: no
            # tiene sentido repetirlos. Los 5xx sí pueden ser transitorios.
            if 400 <= response.status_code < 500:
                response.raise_for_status()

            if response.status_code >= 500:
                response.raise_for_status()

            print(path, response.json())
            return

        except httpx.HTTPStatusError as exc:
            ultima_excepcion = exc
            if exc.response.status_code < 500 or intento == REINTENTOS:
                raise
        except httpx.RequestError as exc:
            ultima_excepcion = exc
            if intento == REINTENTOS:
                raise

        espera = ESPERAS_REINTENTO[intento - 1]
        print(
            f"AVISO: fallo transitorio en {path}; reintento {intento + 1}/{REINTENTOS} "
            f"en {espera}s: {ultima_excepcion}",
            file=sys.stderr,
        )
        time.sleep(espera)

    if ultima_excepcion:
        raise ultima_excepcion


def main() -> int:
    if not SECRET:
        print("Falta EMPLOYMENT_IMPORT_SECRET", file=sys.stderr)
        return 2

    hoy = date.today().isoformat()
    tareas = [
        # GVA: descubre nuevas fichas y vuelve a consultar todos los procesos
        # conocidos no terminales, aunque su plazo de inscripción haya cerrado.
        "/admin/import/gva?max_paginas=10",

        # Diputación de Valencia: publicaciones recientes del BOP con solape.
        f"/admin/import/bop-valencia?historico=true&dias={VENTANA_SOLAPE_DIAS}",

        # Administración local: el solape solo descubre publicaciones nuevas.
        # La antigüedad nunca determina si un proceso sigue siendo oportunidad.
        f"/admin/gestion/import/boe-local?hasta={hoy}&dias={VENTANA_SOLAPE_DIAS}&aplicar=true",
        f"/admin/gestion/import/bop-municipios?hasta={hoy}&dias={VENTANA_SOLAPE_DIAS}&aplicar=true",

        "/admin/seguimiento/preparar-notificaciones",
    ]

    errores: list[tuple[str, Exception]] = []
    with httpx.Client(timeout=300.0) as client:
        for path in tareas:
            try:
                _post(client, path)
            except (httpx.HTTPError, ValueError) as exc:
                # Una fuente no debe impedir que se actualicen las demás. Al
                # terminar, el workflow queda en fallo si alguna tarea falló.
                errores.append((path, exc))
                print(f"ERROR: {path}: {exc}", file=sys.stderr)

    if errores:
        print(f"Actualización incompleta: {len(errores)} tarea(s) con error", file=sys.stderr)
        return 1

    print("Actualización completa: todas las tareas finalizaron correctamente")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
