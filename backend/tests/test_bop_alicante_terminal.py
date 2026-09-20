from app.estado_proceso import clasificar_evento_terminal


def test_seguimiento_terminal_finaliza_proceso():
    estado = clasificar_evento_terminal(
        "OPOSICION",
        "Anuncio de finalización del proceso selectivo de Administrativo/a",
    )
    assert estado == "FINALIZADO"


def test_seguimiento_intermedio_no_finaliza_proceso():
    estado = clasificar_evento_terminal(
        "OPOSICION",
        "Lista provisional de personas admitidas y excluidas de Administrativo/a",
    )
    assert estado is None


def test_cierre_inscripcion_no_finaliza_proceso():
    estado = clasificar_evento_terminal(
        "OPOSICION",
        "Finalización del plazo de presentación de solicitudes de Administrativo/a",
    )
    assert estado is None
