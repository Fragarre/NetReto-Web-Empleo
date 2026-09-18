from app.periodic import _ejecutar_fuente, _estado_fuente


def test_fuente_ok():
    salida = _ejecutar_fuente(lambda: {"descubiertos": 2, "errores": []})
    assert salida[1]["estado"] == "OK"
    assert salida[0]["descubiertos"] == 2


def test_fuente_sin_novedades():
    salida = _ejecutar_fuente(lambda: {"descubiertos": 0, "errores": []})
    assert salida[1]["estado"] == "SIN_NOVEDADES"


def test_fuente_degradada():
    salida = _ejecutar_fuente(
        lambda: {"descubiertos": 1, "dias_con_error": 1, "errores": [{"fecha": "2026-09-18"}]},
    )
    assert salida[1]["estado"] == "DEGRADADA"


def test_fallo_queda_aislado_y_permite_continuar():
    llamadas = []

    def falla():
        llamadas.append("falla")
        raise RuntimeError("fallo controlado")

    def sigue():
        llamadas.append("sigue")
        return {"descubiertos": 1}

    primera = _ejecutar_fuente(falla)
    segunda = _ejecutar_fuente(sigue)

    assert primera[1]["estado"] == "ERROR"
    assert "RuntimeError: fallo controlado" in primera[1]["error"]
    assert segunda[1]["estado"] == "OK"
    assert llamadas == ["falla", "sigue"]


def test_estado_no_inventa_sin_novedades_sin_contadores_conocidos():
    assert _estado_fuente({"modo": "SOLO_DIAGNOSTICO", "resultado": {}}) == "OK"
