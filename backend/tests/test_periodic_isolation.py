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
    assert "RuntimeError: fallo controlado" in primera[1]["traceback"]
    assert "test_periodic_isolation.py" in primera[1]["traceback"]
    assert primera[0]["traceback"] == primera[1]["traceback"]
    assert segunda[1]["estado"] == "OK"
    assert llamadas == ["falla", "sigue"]


def test_estado_no_inventa_sin_novedades_sin_contadores_conocidos():
    assert _estado_fuente({"modo": "SOLO_DIAGNOSTICO", "resultado": {}}) == "OK"


def test_periodico_conserva_payloads_y_continua_tras_fallo(monkeypatch):
    import app.periodic as periodic

    llamadas = []

    def bop_falla(**kwargs):
        llamadas.append("bop")
        raise RuntimeError("bop no disponible")

    def municipal(**kwargs):
        llamadas.append("municipal")
        return {"marca": "municipal"}

    def boe(**kwargs):
        llamadas.append("boe")
        return {"marca": "boe"}

    def gva(**kwargs):
        llamadas.append("gva")
        return {"marca": "gva"}

    monkeypatch.setattr(periodic, "importar_bop_valencia", bop_falla)
    monkeypatch.setattr(periodic, "importar_municipales_bop", municipal)
    monkeypatch.setattr(periodic, "previsualizar_importacion_boe_local", boe)
    monkeypatch.setattr(periodic, "importar_gva_estatal", gva)

    salida = periodic.ejecutar_periodico(aplicar=True, hoy=periodic.date(2026, 9, 18), dias_solape=7)

    assert llamadas == ["bop", "municipal", "boe", "gva"]
    assert salida["fuentes"]["bop_valencia_diputacion"]["error"].startswith("RuntimeError:")
    assert salida["fuentes"]["bop_valencia_municipios"] == {"marca": "municipal"}
    assert salida["fuentes"]["boe_local"] == {"marca": "boe"}
    assert salida["fuentes"]["gva"] == {"marca": "gva"}
    assert salida["estado_fuentes"]["bop_valencia_diputacion"]["estado"] == "ERROR"
    assert salida["resumen_fuentes"]["errores"] == 1
