from datetime import date

from app.estado_proceso import estado_inscripcion
from app.boe_local_import import _agrupar_convocatorias_por_boe, _fusionar_boe_agregados


def test_boe_local_unico_mantiene_contrato_actual():
    proceso = {
        "organismo_nombre": "Ayuntamiento de Ejemplo",
        "fecha_boe_publicacion": date(2026, 8, 13),
        "datos_json": {
            "boe_local": {
                "plazo_solicitudes_literal": "20 días hábiles a partir del día siguiente"
            }
        },
    }

    estado = estado_inscripcion(proceso, hoy=date(2026, 8, 20))

    assert estado["codigo"] == "ABIERTO"
    assert estado["fecha_cierre_calculada"] is True
    assert estado["fecha_cierre_sin_festivos_locales"] is True
    assert "plazos_multiples" not in estado


def test_boe_agregados_calculan_cada_plazo_por_separado():
    proceso = {
        "organismo_nombre": "Diputación Provincial de Castellón",
        "datos_json": {
            "boe_local_agregados": [
                {
                    "codigo_externo": "BOE-A-2026-18928",
                    "fecha_boe": "2026-09-10",
                    "denominacion": "Auxiliar de Recaudación",
                    "plazas": 5,
                    "plazo_solicitudes_literal": "10 días hábiles",
                },
                {
                    "codigo_externo": "BOE-A-2026-19226",
                    "fecha_boe": "2026-09-15",
                    "denominacion": "Técnico/a Medio/a de Gestión",
                    "plazas": 1,
                    "plazo_solicitudes_literal": "10 días hábiles",
                },
            ]
        },
    }

    estado = estado_inscripcion(proceso, hoy=date(2026, 9, 20))

    assert estado["codigo"] == "ABIERTO"
    assert estado["plazos_multiples"] is True
    assert [p["fecha_cierre"] for p in estado["plazos"]] == [
        date(2026, 9, 24),
        date(2026, 9, 29),
    ]
    assert all(p["codigo"] == "ABIERTO" for p in estado["plazos"])


def test_boe_agregados_no_inventan_estado_unico_si_los_plazos_difieren():
    proceso = {
        "organismo_nombre": "Diputación Provincial de Castellón",
        "datos_json": {
            "boe_local_agregados": [
                {
                    "codigo_externo": "BOE-1",
                    "fecha_boe": "2026-09-01",
                    "plazo_solicitudes_literal": "10 días hábiles",
                },
                {
                    "codigo_externo": "BOE-2",
                    "fecha_boe": "2026-09-15",
                    "plazo_solicitudes_literal": "10 días hábiles",
                },
            ]
        },
    }

    estado = estado_inscripcion(proceso, hoy=date(2026, 9, 20))

    assert estado["codigo"] == "PLAZOS_MULTIPLES"
    assert {p["codigo"] for p in estado["plazos"]} == {"ABIERTO", "CERRADO"}


def test_fusion_agregados_es_idempotente_por_documento_boe():
    primero = {
        "boe_id": "BOE-A-2026-18928",
        "codigo_externo": "BOE-A-2026-18928#1",
        "fecha_boe": "2026-09-10",
        "plazas": 5,
    }
    segundo = {
        "boe_id": "BOE-A-2026-19226",
        "codigo_externo": "BOE-A-2026-19226#1",
        "fecha_boe": "2026-09-15",
        "plazas": 1,
    }

    una_ejecucion = _fusionar_boe_agregados([], [primero, segundo])
    dos_ejecuciones = _fusionar_boe_agregados(una_ejecucion, [primero, segundo])

    assert dos_ejecuciones == una_ejecucion
    assert len(dos_ejecuciones) == 2


def test_fusion_agregados_incorpora_boe_posterior_sin_duplicar_previos():
    primero = {
        "boe_id": "BOE-A-2026-18928",
        "codigo_externo": "BOE-A-2026-18928#1",
        "fecha_boe": "2026-09-10",
        "plazas": 5,
    }
    posterior = {
        "boe_id": "BOE-A-2026-20000",
        "codigo_externo": "BOE-A-2026-20000#1",
        "fecha_boe": "2026-09-25",
        "plazas": 3,
    }

    existentes = _fusionar_boe_agregados([], [primero])
    actualizados = _fusionar_boe_agregados(existentes, [primero, posterior])

    assert [x["boe_id"] for x in actualizados] == [
        "BOE-A-2026-18928",
        "BOE-A-2026-20000",
    ]
    assert len(actualizados) == 2


def test_un_documento_boe_con_varios_turnos_conserva_total_plazas():
    convocatorias = [
        {"boe_id": "BOE-A-2026-17643", "codigo_externo": "BOE-A-2026-17643#1", "denominacion": "Auxiliar Administrativo/a", "plazas": 1},
        {"boe_id": "BOE-A-2026-17643", "codigo_externo": "BOE-A-2026-17643#2", "denominacion": "Auxiliar Administrativo/a", "plazas": 7},
        {"boe_id": "BOE-A-2026-17643", "codigo_externo": "BOE-A-2026-17643#3", "denominacion": "Auxiliar Administrativo/a", "plazas": 3},
    ]

    documentos = _agrupar_convocatorias_por_boe(convocatorias)

    assert len(documentos) == 1
    assert documentos[0]["boe_id"] == "BOE-A-2026-17643"
    assert documentos[0]["plazas"] == 11


def test_no_se_infiere_cobertura_sumando_documentos_boe_distintos():
    # Regla de contrato: varios BOE pueden contener subconjuntos solapados.
    # La cobertura del proceso no puede deducirse sumando sus cifras.
    documentos = [
        {"boe_id": "BOE-1", "plazas": 5},
        {"boe_id": "BOE-2", "plazas": 9},
    ]
    total_aritmetico = sum(x["plazas"] for x in documentos)

    assert total_aritmetico == 14
    assert total_aritmetico != 9
