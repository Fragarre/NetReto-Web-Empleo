from __future__ import annotations

from app.gva_estatal_import import construir_registro
from app.gva_estatal_source import clasificar_oportunidad, nuevo_cliente, obtener_detalle

CASOS_INCLUIDOS = [
    (219621, "C2-01", "GVA:110135", True),
    (219862, "C1-01", "GVA:110206", True),
    (220197, "C1-07", "GVA:110235", True),
    (220291, "A2-05", "GVA:110202", True),
    (221774, "A1-01", "GVAESTATAL:221774", False),
    (219292, "C1-01", "GVAESTATAL:219292", False),
    (219983, "C2-01", "GVAESTATAL:219983", False),
]

CASO_REVISION = (220783, None, "GVAESTATAL:220783")

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


def tarjeta(referencia: int, organo: str) -> dict:
    return {
        "referencia": referencia,
        "titulo": TITULOS[referencia],
        "ubicacion": "AUTONÓMICO - COMUNITAT VALENCIANA",
        "organo": organo,
        "url": f"https://administracion.gob.es/pagFront/ofertasempleopublico/detalleEmpleo.htm?idConvocatoria={referencia}",
    }


def main() -> int:
    with nuevo_cliente() as client:
        for referencia, codigo_esperado, identificador_esperado, legacy in CASOS_INCLUIDOS:
            detalle = obtener_detalle(client, referencia)
            t = tarjeta(referencia, "Conselleria de Economía, Hacienda y Administración Pública")
            clasificado = clasificar_oportunidad(t, detalle)
            registro = construir_registro(t, detalle)
            codigos = clasificado.get("codigos_administrativos") or []
            if codigo_esperado not in codigos:
                raise AssertionError(f"{referencia}: falta código {codigo_esperado}: {codigos}")
            if not clasificado.get("es_oportunidad"):
                raise AssertionError(f"{referencia}: no quedó incluida: {clasificado}")
            if registro["identificador_estable"] != identificador_esperado:
                raise AssertionError(
                    f"{referencia}: identificador {registro['identificador_estable']} != {identificador_esperado}"
                )
            if registro["preservar_campos_existentes"] is not legacy:
                raise AssertionError(f"{referencia}: política legacy incorrecta")
            datos = registro.get("datos_json") or {}
            if not str(datos.get("url_publicacion_oficial") or "").startswith("https://dogv.gva.es/"):
                raise AssertionError(f"{referencia}: falta URL oficial DOGV: {datos}")
            if not datos.get("fecha_publicacion_oficial"):
                raise AssertionError(f"{referencia}: falta fecha oficial DOGV")
            print(
                referencia,
                "|", registro["identificador_estable"],
                "|", registro["tipo_proceso"],
                "|", registro["cuerpo_escala"],
                "| DOGV=", datos["fecha_publicacion_oficial"],
                "| preservar=", registro["preservar_campos_existentes"],
            )

        referencia, _, identificador_esperado = CASO_REVISION
        detalle = obtener_detalle(client, referencia)
        t = tarjeta(referencia, "Espacios Económicos Empresariales S.L. (EEE)")
        clasificado = clasificar_oportunidad(t, detalle)
        registro = construir_registro(t, detalle)
        if clasificado.get("es_oportunidad"):
            raise AssertionError("EEE no debe auto-publicarse sin revisión explícita")
        if clasificado.get("organismo_gva") != "REVISION":
            raise AssertionError(f"EEE debería quedar en REVISION: {clasificado}")
        if registro["identificador_estable"] != identificador_esperado:
            raise AssertionError("Identificador de caso en revisión incorrecto")
        print(referencia, "| REVISION organismo | no auto-publicada")

    print("VALIDACION_INTEGRACION_OK=7+1_REVISION")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
