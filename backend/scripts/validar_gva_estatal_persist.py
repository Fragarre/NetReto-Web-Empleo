from __future__ import annotations

from app.gva_estatal_import import construir_registro
from app.gva_estatal_persist import planificar_persistencia
from app.gva_estatal_source import nuevo_cliente, obtener_detalle

CASOS = [
    (219621, "C2-01", "GVA:110135"),
    (219862, "C1-01", "GVA:110206"),
    (220197, "C1-07", "GVA:110235"),
    (220291, "A2-05", "GVA:110202"),
    (221774, "A1-01", "GVAESTATAL:221774"),
    (219292, "C1-01", "GVAESTATAL:219292"),
    (219983, "C2-01", "GVAESTATAL:219983"),
]

TITULOS = {
    219621: "AUXILIAR - Cuerpo Auxiliar",
    219862: "ADMINISTRATIVO",
    220197: "AGENTES TRIBUTARIOS - cuerpo de agentes tributarios de la Generalitat,",
    220291: "TÉCNICOS TRIBUTARIOS - cuerpo técnico tributario de la Generalitat",
    221774: "SUPERIOR DE ADMINISTRACIÓN",
    219292: "ADMINISTRATIVO",
    219983: "AUXILIAR - Cuerpo Auxiliar",
}


def _meta(registro: dict) -> dict:
    datos = registro.get("datos_json") or {}
    return {
        "fuente_descubrimiento": "administracion.gob.es",
        "referencia_estatal": registro.get("referencia_estatal"),
        "url_estatal": datos.get("url_estatal"),
        "via_estatal": datos.get("via_estatal"),
        "codigos_administrativos": datos.get("codigos_administrativos"),
    }


def main() -> int:
    registros = []
    with nuevo_cliente() as client:
        for referencia, _, _ in CASOS:
            detalle = obtener_detalle(client, referencia)
            tarjeta = {
                "referencia": referencia,
                "titulo": TITULOS[referencia],
                "ubicacion": "AUTONÓMICO - COMUNITAT VALENCIANA",
                "organo": "Conselleria de Economía, Hacienda y Administración Pública",
                "url": f"https://administracion.gob.es/pagFront/ofertasempleopublico/detalleEmpleo.htm?idConvocatoria={referencia}",
            }
            registros.append(construir_registro(tarjeta, detalle))

    existentes = {
        "GVA:110135": {"id": 26, "datos_json": {}},
        "GVA:110202": {"id": 28, "datos_json": {}},
        "GVA:110206": {"id": 29, "datos_json": {}},
        "GVA:110235": {"id": 30, "datos_json": {}},
    }
    plan = planificar_persistencia(registros, existentes, set())
    esperado = {
        "insertar": 3,
        "enlazar_metadatos": 4,
        "publicaciones": 7,
        "sin_cambios": 0,
        "revision": 0,
        "bloquear": 0,
    }
    if plan["resumen"] != esperado:
        raise AssertionError(f"Plan inesperado: {plan['resumen']} != {esperado}")

    # Segunda pasada simulada: procesos, metadatos y publicaciones ya existen.
    por_id = {r["identificador_estable"]: r for r in registros}
    existentes_2 = {
        identificador: {
            "id": 1000 + i,
            "datos_json": {"fuente_estatal": _meta(registro)},
        }
        for i, (identificador, registro) in enumerate(por_id.items(), start=1)
    }
    referencias = {referencia for referencia, _, _ in CASOS}
    plan2 = planificar_persistencia(registros, existentes_2, referencias)
    esperado2 = {
        "insertar": 0,
        "enlazar_metadatos": 0,
        "publicaciones": 0,
        "sin_cambios": 7,
        "revision": 0,
        "bloquear": 0,
    }
    if plan2["resumen"] != esperado2:
        raise AssertionError(f"Plan idempotente inesperado: {plan2['resumen']} != {esperado2}")

    # Si falta un legacy real, nunca se recrea automáticamente.
    existentes_sin_legacy = dict(existentes)
    existentes_sin_legacy.pop("GVA:110135")
    plan3 = planificar_persistencia(registros, existentes_sin_legacy, set())
    if plan3["resumen"]["bloquear"] != 1:
        raise AssertionError(f"No se bloqueó legacy ausente: {plan3['resumen']}")

    # Una oportunidad sin PDF DOGV oficial tampoco puede publicarse.
    registro_sin_dogv = dict(registros[-1])
    registro_sin_dogv["identificador_estable"] = "GVAESTATAL:999999"
    registro_sin_dogv["referencia_estatal"] = 999999
    registro_sin_dogv["datos_json"] = dict(registro_sin_dogv["datos_json"])
    registro_sin_dogv["datos_json"]["url_publicacion_oficial"] = None
    registro_sin_dogv["datos_json"]["fecha_publicacion_oficial"] = None
    plan4 = planificar_persistencia([registro_sin_dogv], {}, set())
    if plan4["resumen"]["bloquear"] != 1:
        raise AssertionError(f"No se bloqueó publicación sin DOGV: {plan4['resumen']}")

    print("PLAN_1", plan["resumen"])
    print("PLAN_2", plan2["resumen"])
    print("PLAN_3", plan3["resumen"])
    print("PLAN_4", plan4["resumen"])
    print("VALIDACION_PERSISTENCIA_SOLO_REVISION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
