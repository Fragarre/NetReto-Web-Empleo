from app.boe_local_import import _buscar_organismo, _candidatos_bop, _insertar_publicacion_boe


def test_buscar_organismo_exige_provincia_correcta():
    organismos = [
        {"id": 1, "nombre": "Ayuntamiento de San Vicente", "tipo": "AYUNTAMIENTO", "provincia": "Valencia", "municipio": "San Vicente"},
        {"id": 2, "nombre": "Ayuntamiento de San Vicente", "tipo": "AYUNTAMIENTO", "provincia": "Alicante", "municipio": "San Vicente"},
    ]
    assert _buscar_organismo(organismos, "Ayuntamiento de San Vicente", "Alicante")["id"] == 2
    assert _buscar_organismo(organismos, "Ayuntamiento de San Vicente", "Castellón") is None


def test_buscar_organismo_preserva_valencia():
    organismos = [
        {"id": 7, "nombre": "Ayuntamiento de València", "tipo": "AYUNTAMIENTO", "provincia": "Valencia", "municipio": "València"},
    ]
    assert _buscar_organismo(organismos, "Ayuntamiento de València", "Valencia")["id"] == 7


class CursorCandidatos:
    def __init__(self, filas):
        self.filas = filas
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.filas


def test_candidatos_locales_no_dependen_de_prefijo_bopmun():
    cursor = CursorCandidatos([
        {
            "id": 10,
            "identificador_estable": "ALICANTE:OTRA_FUENTE:123",
            "codigo_externo": "123",
            "denominacion": "Administrativo",
            "plazas": 2,
            "fecha_convocatoria": "2026-09-01",
            "datos_json": {},
        }
    ])
    candidatos = _candidatos_bop(
        cursor,
        organismo_id=2,
        fecha_bases="2026-09-01",
        denominacion="Administrativo",
        plazas=2,
    )
    assert [c["id"] for c in candidatos] == [10]
    assert "BOPMUN" not in cursor.sql
    assert cursor.params == (2, "2026-09-01")


def test_candidatos_conservan_todos_los_solapamientos():
    cursor = CursorCandidatos([
        {"id": 20, "identificador_estable": "X:1", "codigo_externo": "1", "denominacion": "Auxiliar Administrativo", "plazas": 1, "fecha_convocatoria": "2026-09-02", "datos_json": {}},
        {"id": 21, "identificador_estable": "Y:2", "codigo_externo": "2", "denominacion": "Auxiliar Administrativo", "plazas": 1, "fecha_convocatoria": "2026-09-02", "datos_json": {}},
    ])
    candidatos = _candidatos_bop(
        cursor,
        organismo_id=3,
        fecha_bases="2026-09-02",
        denominacion="Auxiliar Administrativo",
        plazas=1,
    )
    assert [c["id"] for c in candidatos] == [20, 21]


class CursorPublicacionExistentePorDocumento:
    def __init__(self):
        self.sql = None
        self.params = None
        self.insert_ejecutado = False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        if "INSERT INTO publicaciones" in sql:
            self.insert_ejecutado = True

    def fetchone(self):
        return (1,)


def test_publicacion_boe_se_deduplica_por_boe_id_aunque_cambie_la_fila():
    cursor = CursorPublicacionExistentePorDocumento()
    convocatoria = {
        "boe_id": "BOE-A-2026-17643",
        "url_html": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-17643",
    }

    creada = _insertar_publicacion_boe(
        cursor,
        fuente_id=1,
        proceso_id=406,
        convocatoria=convocatoria,
        codigo="BOE-A-2026-17643#2",
    )

    assert creada is False
    assert "datos_json->>'boe_id'=%s" in cursor.sql
    assert cursor.params == (1, "BOE-A-2026-17643#2", "BOE-A-2026-17643")
    assert cursor.insert_ejecutado is False
