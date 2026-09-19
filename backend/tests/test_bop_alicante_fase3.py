from app.bop_alicante import seleccionar_proceso_seguimiento


def _hallazgo():
    return {
        "extracto": "ACUMULACIÓN PLAZAS DE ADMINISTRATIVO A CONVOCATORIA BOP 27/2026",
        "denominacion": "Alcoy/Alcoi",
    }


def _candidato(id_, municipio="Alcoy/Alcoi", denominacion="Administrativo convocatoria BOP 27/2026"):
    return {"id": id_, "municipio": municipio, "denominacion": denominacion}


def test_sin_candidatos_no_vincula():
    proceso, motivo = seleccionar_proceso_seguimiento(_hallazgo(), [])
    assert proceso is None
    assert motivo == "SIN_COINCIDENCIA"


def test_un_candidato_inequivoco_vincula():
    proceso, motivo = seleccionar_proceso_seguimiento(_hallazgo(), [_candidato(1)])
    assert proceso["id"] == 1
    assert motivo in {"CODIGO_EXACTO", "UNICO_HITO_SELECTIVO"}


def test_varios_candidatos_no_vinculan():
    candidatos = [_candidato(1), _candidato(2)]
    proceso, motivo = seleccionar_proceso_seguimiento(_hallazgo(), candidatos)
    assert proceso is None
    assert motivo in {"CODIGO_AMBIGUO", "AMBIGUO_SIN_CODIGO"}


def test_municipio_distinto_no_vincula():
    proceso, motivo = seleccionar_proceso_seguimiento(
        _hallazgo(), [_candidato(1, municipio="Alicante/Alacant")]
    )
    assert proceso is None
    assert motivo == "SIN_COINCIDENCIA"
