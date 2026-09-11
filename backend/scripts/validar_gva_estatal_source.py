from __future__ import annotations

from app.gva_estatal_source import clasificar_oportunidad, nuevo_cliente, obtener_detalle

CASOS = {
    219621: ("C2-01", True),
    219862: ("C1-01", True),
    220197: ("C1-07", True),
    220291: ("A2-05", True),
    221774: ("A1-01", True),
    219292: ("C1-01", True),
    219983: ("C2-01", True),
    220783: (None, True),
}

TITULOS = {
    219621: "AUXILIAR - Cuerpo Auxiliar",
    219862: "ADMINISTRATIVO",
    220197: "AGENTES TRIBUTARIOS - cuerpo de agentes tributarios de la Generalitat,",
    220291: "TÉCNICOS TRIBUTARIOS - cuerpo técnico tributario de la Generalitat",
    221774: "SUPERIOR DE ADMINISTRACIÓN",
    219292: "ADMINISTRATIVO",
    219983: "AUXILIAR - Cuerpo auxiliar",
    220783: "ADMINISTRATIVO",
}

ORGANOS = {
    219621: "Conselleria de Economía, Hacienda y Administración Pública",
    219862: "Conselleria de Economía, Hacienda y Administración Pública",
    220197: "Conselleria de Economía, Hacienda y Administración Pública",
    220291: "Conselleria de Economía, Hacienda y Administración Pública",
    221774: "Conselleria de Economía, Hacienda y Administración Pública",
    219292: "Conselleria de Economía, Hacienda y Administración Pública",
    219983: "Conselleria de Economía, Hacienda y Administración Pública",
    220783: "Espacios Económicos Empresariales S.L. (EEE)",
}


def main() -> int:
    errores = []
    with nuevo_cliente() as client:
        for referencia, (codigo_esperado, incluida_esperada) in CASOS.items():
            detalle = obtener_detalle(client, referencia)
            tarjeta = {
                "referencia": referencia,
                "titulo": TITULOS[referencia],
                "ubicacion": "AUTONÓMICO - COMUNITAT VALENCIANA",
                "organo": ORGANOS[referencia],
                "url": f"https://administracion.gob.es/pagFront/ofertasempleopublico/detalleEmpleo.htm?idConvocatoria={referencia}",
            }
            item = clasificar_oportunidad(tarjeta, detalle)
            print(
                referencia,
                "| via=", item["via"],
                "| admin=", item["ambito_administrativo"],
                "| codigos=", item["codigos_administrativos"],
                "| incluida=", item["es_oportunidad"],
                "| organo=", item["organo"],
            )
            if item["es_oportunidad"] != incluida_esperada:
                errores.append(f"{referencia}: inclusión inesperada {item['es_oportunidad']}")
            if codigo_esperado and codigo_esperado not in item["codigos_administrativos"]:
                errores.append(f"{referencia}: no se detecta {codigo_esperado}")
    if errores:
        for error in errores:
            print("ERROR", error)
        return 2
    print("VALIDACION_OK=8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
