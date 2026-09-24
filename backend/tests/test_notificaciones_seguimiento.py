from app import seguimiento


def test_enviar_notificaciones_sin_pendientes_no_hace_nada(monkeypatch):
    monkeypatch.setattr(
        seguimiento,
        "listar_notificaciones_pendientes",
        lambda limite=100: [],
    )

    def no_debe_ejecutarse(*args, **kwargs):
        raise AssertionError("No debía ejecutarse")

    monkeypatch.setattr(seguimiento, "emails_usuarios", no_debe_ejecutarse)
    monkeypatch.setattr(seguimiento, "enviar_email", no_debe_ejecutarse)
    monkeypatch.setattr(seguimiento, "get_connection", no_debe_ejecutarse)

    resultado = seguimiento.enviar_notificaciones_pendientes()

    assert resultado == {
        "procesadas": 0,
        "enviadas": 0,
        "errores": 0,
    }


def test_enviar_notificacion_pendiente_envia_y_marca_enviada(monkeypatch):
    from uuid import UUID

    user_id = UUID("11111111-1111-1111-1111-111111111111")

    pendiente = {
        "id": 25,
        "user_id": user_id,
        "proceso_id": 321,
        "denominacion": "Administrativo",
        "resumen": "Publicada fecha de examen",
    }

    monkeypatch.setattr(
        seguimiento,
        "listar_notificaciones_pendientes",
        lambda limite=100: [pendiente],
    )
    monkeypatch.setattr(
        seguimiento,
        "emails_usuarios",
        lambda user_ids: {user_id: "seguidor@example.com"},
    )
    monkeypatch.setenv("PUBLIC_APP_URL", "https://tucoach-oposiciones.com")

    correos = []

    def email_simulado(**kwargs):
        correos.append(kwargs)

    monkeypatch.setattr(seguimiento, "enviar_email", email_simulado)

    ejecuciones = []

    class CursorSimulado:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            ejecuciones.append((sql, params))

    class ConexionSimulada:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return CursorSimulado()

        def commit(self):
            pass

    monkeypatch.setattr(
        seguimiento,
        "get_connection",
        lambda: ConexionSimulada(),
    )

    resultado = seguimiento.enviar_notificaciones_pendientes()

    assert resultado == {
        "procesadas": 1,
        "enviadas": 1,
        "errores": 0,
    }

    assert len(correos) == 1
    assert correos[0]["destinatario"] == "seguidor@example.com"
    assert "Administrativo" in correos[0]["asunto"]
    assert (
        "https://tucoach-oposiciones.com/empleo/proceso/321"
        in correos[0]["texto"]
    )

    assert len(ejecuciones) == 1
    sql, params = ejecuciones[0]
    assert "SET estado = 'ENVIADA'" in sql
    assert params == (25,)


def test_fallo_de_un_envio_no_impide_el_siguiente(monkeypatch):
    from uuid import UUID

    user_1 = UUID("11111111-1111-1111-1111-111111111111")
    user_2 = UUID("22222222-2222-2222-2222-222222222222")

    pendientes = [
        {
            "id": 25,
            "user_id": user_1,
            "proceso_id": 321,
            "denominacion": "Primera convocatoria",
            "resumen": "Primera novedad",
        },
        {
            "id": 26,
            "user_id": user_2,
            "proceso_id": 322,
            "denominacion": "Segunda convocatoria",
            "resumen": "Segunda novedad",
        },
    ]

    monkeypatch.setattr(
        seguimiento,
        "listar_notificaciones_pendientes",
        lambda limite=100: pendientes,
    )
    monkeypatch.setattr(
        seguimiento,
        "emails_usuarios",
        lambda user_ids: {
            user_1: "primero@example.com",
            user_2: "segundo@example.com",
        },
    )

    intentos = []

    def email_simulado(**kwargs):
        intentos.append(kwargs["destinatario"])
        if kwargs["destinatario"] == "primero@example.com":
            raise RuntimeError("fallo simulado")

    monkeypatch.setattr(seguimiento, "enviar_email", email_simulado)

    ejecuciones = []

    class CursorSimulado:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            ejecuciones.append((sql, params))

    class ConexionSimulada:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return CursorSimulado()

        def commit(self):
            pass

    monkeypatch.setattr(
        seguimiento,
        "get_connection",
        lambda: ConexionSimulada(),
    )

    resultado = seguimiento.enviar_notificaciones_pendientes()

    assert resultado == {
        "procesadas": 2,
        "enviadas": 1,
        "errores": 1,
    }

    assert intentos == [
        "primero@example.com",
        "segundo@example.com",
    ]

    assert len(ejecuciones) == 2

    sql_error, params_error = ejecuciones[0]
    assert "SET estado = 'ERROR'" in sql_error
    assert params_error[1] == 25

    sql_enviada, params_enviada = ejecuciones[1]
    assert "SET estado = 'ENVIADA'" in sql_enviada
    assert params_enviada == (26,)
