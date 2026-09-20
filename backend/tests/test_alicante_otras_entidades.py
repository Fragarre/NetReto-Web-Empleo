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

    # Diagnóstico temporal de la estructura HTML real: cabeceras y una fila administrativa.
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(respuesta.text, "html.parser")
    for tr in soup.find_all("tr"):
        textos = [" ".join(celda.get_text(" ", strip=True).split()) for celda in tr.find_all(["th", "td"])]
        if textos and ("Plaza" in textos or "Entidad" in textos):
            print("ALICANTE_HTML_CABECERAS", repr(textos))
            break
    for tr in soup.find_all("tr"):
        textos = [" ".join(celda.get_text(" ", strip=True).split()) for celda in tr.find_all("td")]
        if textos and any("Administr" in texto for texto in textos):
            enlaces = [(a.get_text(" ", strip=True), a.get("href")) for a in tr.find_all("a", href=True)]
            print("ALICANTE_HTML_FILA", repr(textos))
            print("ALICANTE_HTML_ENLACES", repr(enlaces))
            break

    assert filas

    # Cruce histórico BOP Alicante, estrictamente SOLO_REVISION y sin BD.
    from datetime import date
    from app.bop_alicante import consultar_bop_alicante
    administrativas_base = [x for x in filas if x["ambito_administrativo"] == "SI"]
    fechas_base = []
    for x in administrativas_base:
        for enlace in x.get("enlaces") or []:
            # La fecha visible de Bases está en la cuarta celda del listado; se obtiene de nuevo
            # de la fila normalizada mediante el documento asociado cuando haya plazo visible.
            pass
    historico = consultar_bop_alicante(dias_solape=365, hasta=date.today(), max_items=5000)
    print(
        "ALICANTE_BOP_HISTORICO",
        "desde=", historico.get("desde"),
        "hasta=", historico.get("hasta"),
        "descubiertos=", historico.get("descubiertos"),
        "administrativos=", historico.get("administrativos"),
        "revision=", historico.get("revision"),
        "seguimientos=", historico.get("seguimientos"),
        "errores=", historico.get("errores"),
    )
    for h in historico.get("detalle", []):
        texto = " ".join(str(h.get(k) or "") for k in ("extracto", "organismo", "denominacion")).lower()
        if any((x.get("entidad") or "").lower().replace("ayuntamiento de ", "") in texto for x in administrativas_base):
            print("ALICANTE_BOP_COINCIDENCIA", repr(h))

    assert any(x["ambito_administrativo"] == "SI" for x in filas)
    assert all("estado_revision" in x for x in filas)

    administrativas = [x for x in filas if x["ambito_administrativo"] == "SI"]
    candidatas = [x for x in administrativas if x["estado_revision"] == "CANDIDATO_ACTIVO"]
    terminales = [x for x in administrativas if x["estado_revision"] == "TERMINAL"]
    excluidas = [x for x in filas if x["ambito_administrativo"] != "SI"]

    print(
        f"ALICANTE_DIAGNOSTICO total={len(filas)} "
        f"administrativas={len(administrativas)} "
        f"candidatas={len(candidatas)} terminales={len(terminales)} "
        f"excluidas={len(excluidas)}"
    )
    for x in candidatas:
        print(
            "ALICANTE_CASO",
            x.get("estado_revision"),
            x.get("estado_terminal"),
            x.get("denominacion"),
            "|",
            x.get("entidad"),
            "| inicio=", x.get("fecha_inicio_presentacion"),
            "| fin=", x.get("fecha_fin_presentacion"),
            "| obs=", x.get("observaciones"),
            "| enlaces=", x.get("enlaces"),
        )
