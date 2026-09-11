from __future__ import annotations

from app.gva_estatal_import import construir_registro
from app.gva_estatal_source import clasificar_oportunidad, nuevo_cliente, obtener_detalle

CASOS = [
    (219621, "C2-01", "GVA:110135"),
    (219862, "C1-01", "GVA:110206"),
    (220197, "C1-07", "GVA:110235"),
    (220291, "A2-05", "GVA:110202"),
    (221774, "A1-01", "GVAESTATAL:221774"),
    (219292, "C1-01", "GVAESTATAL:219292"),
    (219983, "C2-01", "GVAESTATAL:219983"),
    (220783, None, "GVAESTATAL:220783"),
]

TITULOS = {
    219621: "AUXILIAR - Cuerpo Auxiliar",
    219862: "ADMINISTRATIVO",
    220197: "AGENTES TRIBUTARIOS - cuerpo de agentes tributarios de la Generalitat,",
    220291: "TÉCNICOS TRIBUTARIOS - cuerpo técnico tributario de la Generalitat",
    221774: "SUPERIOR DE ADMINISTRACIÓN",
    219292: "ADMINISTRATIVO",
    219983: "AUXILIAR - Cuerpo Auxiliar",
    220783: "ADMINISTRATIVO/A",
}

ORGANOS = {
    220783: "Espacios Económicos Empresariales S.L. (EEE)",
}


def main() -> int:
    with nuevo_cliente() as client:
        for referencia, codigo_esperado, identificador_esperado in CASOS:
            detalle = obtener_detalle(client, referencia)
            tarjeta = {
                "referencia": referencia,
                "titulo": TITULOS[referencia],
                "ubicacion": "AUTONÓMICO - COMUNITAT VALENCIANA",
                "organo": ORGANOS.get(referencia, "Conselleria de Economía, Hacienda y Administración Pública"),
                "url": f"https://administracion.gob.es/pagFront/ofertasempleopublico/detalleEmpleo.htm?idConvocatoria={referencia}",
            }
            clasificado = clasificar_oportunidad(tarjeta, detalle)
            registro = construir_registro(tarjeta, detalle)
            codigos = clasificado.get("codigos_administrativos") or []
            if codigo_esperado and codigo_esperado not in codigos:
                raise AssertionError(f"{referencia}: falta código {codigo_esperado}: {codigos}")
            if not clasificado.get("es_oportunidad"):
                raise AssertionError(f"{referencia}: no quedó incluida: {clasificado}")
            if registro["identificador_estable"] != identificador_esperado:
                raise AssertionError(
                    f"{referencia}: identificador {registro['identificador_estable']} != {identificador_esperado}"
                )
            print(
                referencia,
                "|", registro["identificador_estable"],
                "|", registro["tipo_proceso"],
                "|", registro["cuerpo_escala"],
                "|", registro["fecha_apertura"], registro["fecha_cierre"],
            )
    print("VALIDACION_INTEGRACION_OK=8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
