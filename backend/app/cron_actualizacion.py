from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta

import httpx


BASE_URL = os.getenv("NETRETO_EMPLEO_API_URL", "https://netreto-empleo-api.onrender.com").rstrip("/")
SECRET = os.getenv("EMPLOYMENT_IMPORT_SECRET")

# Ventanas técnicas de solape. NO son criterios de inclusión: una oportunidad
# permanece en catálogo mientras su proceso selectivo siga activo, cualquiera
# que sea su antigüedad.
VENTANA_GVA_DIAS = 3
VENTANA_BOE_DIAS = 30
VENTANA_BOP_DIAS = 7

# Render puede estar reiniciando una instancia o una fuente oficial puede sufrir
# un fallo transitorio. Reintentamos solo errores de red y respuestas 5xx.
REINTENTOS = 3
ESPERAS_REINTENTO = (10, 30)


def _respuesta_incompleta(path: str, data: object) -> str | None:
    """Detecta respuestas HTTP 200 que no representan una actualización completa."""
    # Compatibilidad con el importador GVA antiguo mientras siga disponible como
    # respaldo técnico. El cron recurrente ya no lo utiliza.
    if not path.startswith("/admin/import/gva?") or not isinstance(data, dict):
        return None

    if data.get("estado_importacion") == "INCOMPLETA":
        return f"GVA incompleta: {data.get('errores_fuente', 0)} error(es) de fuente"

    diagnostico = data.get("diagnostico")
    if isinstance(diagnostico, list):
        errores = [
            item for item in diagnostico
            if isinstance(item, dict)
            and item.get("motivo") in {"error_descubrimiento", "error_detalle"}
        ]
        if errores:
            return f"GVA incompleta: {len(errores)} error(es) de fuente"
    return None


def _post(client: httpx.Client, path: str) -> None:
    ultima_excepcion: Exception | None = None

    for intento in range(1, REINTENTOS + 1):
        try:
            response = client.post(
                f"{BASE_URL}{path}",
                headers={"X-Import-Secret": SECRET or ""},
            )

            if 400 <= response.status_code < 500:
                response.raise_for_status()

            if response.status_code >= 500:
                detalle = response.text[:1000].replace("\n", " ")
                print(
                    f"RESPUESTA {response.status_code} en {path}: {detalle}",
                    file=sys.stderr,
                )
                response.raise_for_status()

            data = response.json()
            print(path, data)
            incompleta = _respuesta_incompleta(path, data)
            if incompleta:
                raise ValueError(incompleta)
            return

        except ValueError:
            raise
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

    hoy = date.today()
    desde_gva = hoy - timedelta(days=VENTANA_GVA_DIAS - 1)
    hoy_iso = hoy.isoformat()
    desde_gva_iso = desde_gva.isoformat()
    tareas = [
        # GVA: descubrimiento incremental con tres días de solape. La ventana
        # solo protege frente a retrasos/fallos; no define qué procesos siguen
        # perteneciendo al catálogo.
        f"/admin/import/gva-estatal?desde={desde_gva_iso}&hasta={hoy_iso}&aplicar=true",

        # Diputación: una semana de solape permite recuperar varios días de
        # incidencias sin repetir cada día un histórico costoso de 30 días.
        f"/admin/import/bop-valencia?historico=true&dias={VENTANA_BOP_DIAS}",

        # BOE local es rápido y conserva un solape mayor para descubrimiento.
        f"/admin/gestion/import/boe-local?hasta={hoy_iso}&dias={VENTANA_BOE_DIAS}&aplicar=true",

        # BOP municipal es la fuente más costosa; siete días de solape son
        # suficientes para una ejecución diaria y no afectan al ciclo de vida
        # de los procesos ya persistidos.
        f"/admin/gestion/import/bop-municipios?hasta={hoy_iso}&dias={VENTANA_BOP_DIAS}&aplicar=true",

        "/admin/seguimiento/preparar-notificaciones",
    ]

    errores: list[tuple[str, Exception]] = []
    with httpx.Client(timeout=300.0) as client:
        for path in tareas:
            try:
                _post(client, path)
            except (httpx.HTTPError, ValueError) as exc:
                errores.append((path, exc))
                print(f"ERROR: {path}: {exc}", file=sys.stderr)

    if errores:
        print(f"Actualización incompleta: {len(errores)} tarea(s) con error", file=sys.stderr)
        return 1

    print("Actualización completa: todas las tareas finalizaron correctamente")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
