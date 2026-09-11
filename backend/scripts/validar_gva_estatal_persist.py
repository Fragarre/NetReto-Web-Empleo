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
        "GVA:110135": {"id": 26},
        "GVA:110202": {"id": 28},
        "GVA:110206": {"id": 29},
        "GVA:110235": {"id": 30},
    }
    plan = planificar_persistencia(registros, existentes)
    esperado = {"insertar": 3, "enlazar_metadatos": 4, "revision": 0, "bloquear": 0}
    if plan["resumen"] != esperado:
        raise AssertionError(f"Plan inesperado: {plan['resumen']} != {esperado}")

    # Segunda pasada simulada: los tres nuevos ya existirían. Debe ser idempotente,
    # sin proponer nuevos INSERT.
    existentes_2 = dict(existentes)
    existentes_2.update({
        "GVAESTATAL:221774": {"id": 1001},
        "GVAESTATAL:219292": {"id": 1002},
        "GVAESTATAL:219983": {"id": 1003},
    })
    plan2 = planificar_persistencia(registros, existentes_2)
    esperado2 = {"insertar": 0, "enlazar_metadatos": 7, "revision": 0, "bloquear": 0}
    if plan2["resumen"] != esperado2:
        raise AssertionError(f"Plan idempotente inesperado: {plan2['resumen']} != {esperado2}")

    # Salvaguarda: si falta un legacy real, no debe recrearse automáticamente.
    existentes_sin_legacy = dict(existentes)
    existentes_sin_legacy.pop("GVA:110135")
    plan3 = planificar_persistencia(registros, existentes_sin_legacy)
    if plan3["resumen"]["bloquear"] != 1:
        raise AssertionError(f"No se bloqueó legacy ausente: {plan3['resumen']}")

    print("PLAN_1", plan["resumen"])
    print("PLAN_2", plan2["resumen"])
    print("PLAN_3", plan3["resumen"])
    print("VALIDACION_PERSISTENCIA_SOLO_REVISION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
