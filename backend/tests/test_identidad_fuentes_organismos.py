from __future__ import annotations

import pytest
from psycopg.rows import dict_row

from app.organismos import resolver_fuente, resolver_organismo


class CursorFalso:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


def test_resolver_fuente_por_identidad_sin_id_historico():
    cursor = CursorFalso([
        {
            "id": 27,
            "organismo_id": None,
            "nombre": "Boletín Oficial de la Provincia de Valencia",
            "tipo": "BOP",
            "url": "https://ejemplo.invalid",
            "prioridad": 10,
            "activa": True,
        }
    ])
    fuente = resolver_fuente(
        cursor,
        nombre="Boletín Oficial de la Provincia de Valencia",
        tipo="BOP",
    )
    assert fuente["id"] == 27
    assert "id =" not in cursor.sql.lower()
    assert cursor.params[:2] == (
        "Boletín Oficial de la Provincia de Valencia",
        "BOP",
    )


def test_resolver_fuente_rechaza_ambiguedad():
    cursor = CursorFalso([
        {"id": 2, "organismo_id": None, "nombre": "BOP", "tipo": "BOP", "url": "a", "prioridad": 1, "activa": True},
        {"id": 8, "organismo_id": None, "nombre": "BOP", "tipo": "BOP", "url": "b", "prioridad": 1, "activa": True},
    ])
    with pytest.raises(RuntimeError, match="ambigua"):
        resolver_fuente(cursor, nombre="BOP", tipo="BOP")


def test_resolver_organismo_exige_provincia_y_municipio():
    cursor = CursorFalso([
        {"id": 4, "nombre": "Ayuntamiento de Xàtiva", "tipo": "AYUNTAMIENTO", "provincia": "Valencia", "municipio": "Xàtiva", "activo": True},
        {"id": 40, "nombre": "Ayuntamiento de Xàtiva", "tipo": "AYUNTAMIENTO", "provincia": "Alicante", "municipio": "Xàtiva", "activo": True},
    ])
    organismo = resolver_organismo(
        cursor,
        tipo="AYUNTAMIENTO",
        provincia="Valencia",
        municipio="Xativa",
    )
    assert organismo is not None
    assert organismo["id"] == 4


def test_resolver_organismo_no_enlaza_ambiguedad():
    cursor = CursorFalso([
        {"id": 4, "nombre": "Ayuntamiento de X", "tipo": "AYUNTAMIENTO", "provincia": "Valencia", "municipio": "X", "activo": True},
        {"id": 5, "nombre": "Ajuntament de X", "tipo": "AYUNTAMIENTO", "provincia": "Valencia", "municipio": "X", "activo": True},
    ])
    with pytest.raises(RuntimeError, match="ambiguo"):
        resolver_organismo(
            cursor,
            tipo="AYUNTAMIENTO",
            provincia="Valencia",
            municipio="X",
        )


def test_resolver_organismo_sin_coincidencia_no_inventa():
    cursor = CursorFalso([])
    assert resolver_organismo(
        cursor,
        tipo="AYUNTAMIENTO",
        provincia="Castellón",
        municipio="Segorbe",
    ) is None
