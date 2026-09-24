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

    def castellon(**kwargs):
        llamadas.append("castellon")
        return {"marca": "castellon"}

    def otras_alicante(**kwargs):
        llamadas.append("otras_alicante")
        return {"marca": "otras_alicante"}

    def alicante(**kwargs):
        llamadas.append("alicante")
        return {"marca": "alicante"}

    def pendientes(**kwargs):
        llamadas.append("pendientes")
        return {"marca": "pendientes"}

    def boe(**kwargs):
        llamadas.append("boe")
        return {"marca": "boe"}

    def gva(**kwargs):
        llamadas.append("gva")
        return {"marca": "gva"}

    monkeypatch.setattr(periodic, "importar_bop_valencia", bop_falla)
    monkeypatch.setattr(periodic, "importar_municipales_bop", municipal)
    monkeypatch.setattr(periodic, "importar_bop_castellon", castellon)
    monkeypatch.setattr(periodic, "bootstrap_otras_entidades_alicante", otras_alicante)
    monkeypatch.setattr(periodic, "importar_bop_alicante", alicante)
    monkeypatch.setattr(periodic, "_recuperar_boe_pendientes_activos", pendientes)
    monkeypatch.setattr(periodic, "previsualizar_importacion_boe_local", boe)
    monkeypatch.setattr(periodic, "importar_gva_estatal", gva)

    lecturas_visibles = iter(({10, 20}, {10, 20, 30}))
    registrados = []
    monkeypatch.setattr(periodic, "ids_oportunidades_visibles", lambda: next(lecturas_visibles))
    monkeypatch.setattr(
        periodic,
        "registrar_nuevas_oportunidades",
        lambda proceso_ids: registrados.extend(sorted(proceso_ids)) or [900],
    )
    monkeypatch.setattr(periodic, "preparar_envios_eventos", lambda evento_ids: 2)

    salida = periodic.ejecutar_periodico(aplicar=True, hoy=periodic.date(2026, 9, 18), dias_solape=7)

    assert llamadas == [
        "bop",
        "municipal",
        "castellon",
        "otras_alicante",
        "alicante",
        "pendientes",
        "boe",
        "gva",
    ]
    assert salida["fuentes"]["bop_valencia_diputacion"]["error"].startswith("RuntimeError:")
    assert salida["fuentes"]["bop_valencia_municipios"] == {"marca": "municipal"}
    assert salida["fuentes"]["boe_local"] == {"marca": "boe"}
    assert salida["fuentes"]["gva"] == {"marca": "gva"}
    assert salida["estado_fuentes"]["bop_valencia_diputacion"]["estado"] == "ERROR"
    assert salida["resumen_fuentes"]["errores"] == 1
    assert registrados == [30]
    assert salida["notificaciones_generales"] == {
        "nuevas_oportunidades_visibles": 1,
        "eventos_creados": 1,
        "envios_preparados": 2,
    }


def test_periodico_revision_pasa_boe_absorbidos_al_importador(monkeypatch):
    import app.periodic as periodic

    monkeypatch.setattr(periodic, "diagnosticar_bop", lambda *args, **kwargs: {})
    monkeypatch.setattr(periodic, "importar_municipales_bop", lambda **kwargs: {})
    monkeypatch.setattr(periodic, "importar_bop_castellon", lambda **kwargs: {})
    monkeypatch.setattr(periodic, "bootstrap_otras_entidades_alicante", lambda **kwargs: {})
    monkeypatch.setattr(periodic, "importar_bop_alicante", lambda **kwargs: {})
    monkeypatch.setattr(
        periodic,
        "_recuperar_boe_pendientes_activos",
        lambda **kwargs: {
            "detalle": [
                {"proceso_id": 406, "boe_id": "BOE-UNICO"},
                {
                    "proceso_id": 408,
                    "boe_local_agregados_propuestos": [
                        {"boe_id": "BOE-AGREGADO-1"},
                        {"boe_id": "BOE-AGREGADO-2"},
                    ],
                },
            ]
        },
    )
    recibidos = {}

    def boe(**kwargs):
        recibidos.update(kwargs)
        return {}

    monkeypatch.setattr(periodic, "previsualizar_importacion_boe_local", boe)
    monkeypatch.setattr(periodic, "importar_gva_estatal", lambda **kwargs: {})

    periodic.ejecutar_periodico(
        aplicar=False,
        hoy=periodic.date(2026, 9, 20),
        dias_solape=7,
    )

    assert recibidos["boe_ids_absorbidos"] == {
        "BOE-UNICO",
        "BOE-AGREGADO-1",
        "BOE-AGREGADO-2",
    }


def test_selector_boe_pendientes_acota_oportunidades_y_administraciones_cv():
    import inspect
    import app.periodic as periodic

    codigo = inspect.getsource(periodic._recuperar_boe_pendientes_activos)

    assert "p.es_oportunidad=TRUE" in codigo
    assert "p.ambito_administrativo='SI'" in codigo
    assert "p.estado='EN_CURSO'" in codigo
    assert "JOIN organismos o ON o.id=p.organismo_id" in codigo
    assert "o.activo=TRUE" in codigo
    assert "o.tipo='AYUNTAMIENTO'" in codigo
    assert "o.tipo='DIPUTACION'" in codigo
    assert "'valencia','valència','alicante','castellón','castellon'" in codigo
    assert "p.datos_json->>'origen' IN ('BOP_VALENCIA','BOP_CASTELLON','BOP_ALICANTE')" in codigo
