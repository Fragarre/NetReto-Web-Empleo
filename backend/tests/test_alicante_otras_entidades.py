from app.alicante_otras_entidades import _parse_listado


def _tabla(plaza, entidad, observaciones=""):
    return f"""
    <table><tr>
      <th>Plaza</th><th>Entidad</th><th>Vacantes</th><th>Bases</th>
      <th>Presentación F.Inicio</th><th>Presentación F.Final</th><th>Obs</th>
    </tr><tr>
      <td>{plaza}</td><td>{entidad}</td><td>1</td>
      <td><a href="/bases.pdf">01/09/2026</a></td>
      <td>10/09/2026</td><td>30/09/2026</td><td>{observaciones}</td>
    </tr></table>
    """


def test_desistimiento_es_terminal():
    fila = _parse_listado(_tabla(
        "Auxiliar Administrativo/a",
        "Ayuntamiento de Aigües",
        "BOP de fecha 18/03/2025 publica desistimiento procedimiento.",
    ))[0]
    assert fila["ambito_administrativo"] == "SI"
    assert fila["estado_revision"] == "TERMINAL"
    assert fila["estado_terminal"] == "DESISTIDO"


def test_administrativo_sin_evidencia_terminal_es_candidato_activo():
    fila = _parse_listado(_tabla(
        "Administrativo/a",
        "Ayuntamiento de Santa Pola",
        "BOE publica extracto bases y plazo presentación de instancias.",
    ))[0]
    assert fila["ambito_administrativo"] == "SI"
    assert fila["estado_revision"] == "CANDIDATO_ACTIVO"
    assert fila["estado_terminal"] is None


def test_no_administrativo_no_se_convierte_en_oportunidad_administrativa():
    fila = _parse_listado(_tabla(
        "Agente de la Policía Local",
        "Ayuntamiento de Calp",
    ))[0]
    assert fila["ambito_administrativo"] != "SI"


def test_fuente_real_solo_lectura():
    import httpx
    from app.alicante_otras_entidades import LISTADO_URL

    respuesta = httpx.get(
        LISTADO_URL,
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "TuCoach-Empleo/1.0"},
    )
    respuesta.raise_for_status()
    filas = _parse_listado(respuesta.text)

    assert filas
    assert any(x["ambito_administrativo"] == "SI" for x in filas)
    assert all("estado_revision" in x for x in filas)
