from app import notificaciones_generales


def test_enviar_envios_sin_pendientes_no_hace_nada(monkeypatch):
    class CursorSimulado:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            pass

        def fetchall(self):
            return []

    class ConexionSimulada:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self, **kwargs):
            return CursorSimulado()

    def email_no_debe_ejecutarse(**kwargs):
        raise AssertionError("No debía ejecutarse")

    monkeypatch.setattr(
        notificaciones_generales,
        "get_connection",
        lambda: ConexionSimulada(),
    )
    monkeypatch.setattr(
        notificaciones_generales,
        "enviar_email",
        email_no_debe_ejecutarse,
    )

    resultado = notificaciones_generales.enviar_envios_pendientes()

    assert resultado == {
        "procesadas": 0,
        "enviadas": 0,
        "errores": 0,
    }

def test_envio_correcto_marca_enviada_y_guarda_message_id(monkeypatch):
    from app.email_sender import ResultadoEnvio

    pendiente = {
        "id": 25,
        "email": "usuario@example.com",
        "proceso_id": 321,
        "denominacion": "Administrativo",
        "plazas": 5,
        "sistema_selectivo": "Oposición",
        "fecha_convocatoria": "2026-09-20",
        "fecha_apertura": "2026-09-21",
        "fecha_cierre": "2026-10-10",
        "organismo": "Ayuntamiento de Prueba",
        "provincia": "Alicante",
    }

    ejecuciones = []
    numero_conexion = 0

    class CursorSimulado:
        def __init__(self, pendientes=None):
            self.pendientes = pendientes

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            ejecuciones.append((sql, params))

        def fetchall(self):
            return self.pendientes or []

    class ConexionSimulada:
        def __init__(self, pendientes=None):
            self.pendientes = pendientes

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self, **kwargs):
            return CursorSimulado(self.pendientes)

        def commit(self):
            pass

    def conexion_simulada():
        nonlocal numero_conexion
        numero_conexion += 1
        if numero_conexion == 1:
            return ConexionSimulada([pendiente])
        return ConexionSimulada()

    monkeypatch.setattr(
        notificaciones_generales,
        "get_connection",
        conexion_simulada,
    )
    monkeypatch.setattr(
        notificaciones_generales,
        "enviar_email",
        lambda **kwargs: ResultadoEnvio(message_id="msg-prueba-123"),
    )

    resultado = notificaciones_generales.enviar_envios_pendientes()

    assert resultado == {
        "procesadas": 1,
        "enviadas": 1,
        "errores": 0,
    }

    assert len(ejecuciones) == 2
    sql, params = ejecuciones[1]
    assert "SET estado = 'ENVIADA'" in sql
    assert params == ("msg-prueba-123", 25)

def test_fallo_de_un_envio_no_impide_el_siguiente(monkeypatch):
    from app.email_sender import ResultadoEnvio

    pendientes = [
        {
            "id": 25,
            "email": "primero@example.com",
            "proceso_id": 321,
            "denominacion": "Primera convocatoria",
            "plazas": 1,
            "sistema_selectivo": "Oposición",
            "fecha_convocatoria": "2026-09-20",
            "fecha_apertura": None,
            "fecha_cierre": None,
            "organismo": "Organismo Primero",
            "provincia": "Alicante",
        },
        {
            "id": 26,
            "email": "segundo@example.com",
            "proceso_id": 322,
            "denominacion": "Segunda convocatoria",
            "plazas": 2,
            "sistema_selectivo": "Concurso-oposición",
            "fecha_convocatoria": "2026-09-21",
            "fecha_apertura": None,
            "fecha_cierre": None,
            "organismo": "Organismo Segundo",
            "provincia": "Castellón",
        },
    ]

    ejecuciones = []
    numero_conexion = 0

    class CursorSimulado:
        def __init__(self, filas=None):
            self.filas = filas

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            ejecuciones.append((sql, params))

        def fetchall(self):
            return self.filas or []

    class ConexionSimulada:
        def __init__(self, filas=None):
            self.filas = filas

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self, **kwargs):
            return CursorSimulado(self.filas)

        def commit(self):
            pass

    def conexion_simulada():
        nonlocal numero_conexion
        numero_conexion += 1
        if numero_conexion == 1:
            return ConexionSimulada(pendientes)
        return ConexionSimulada()

    monkeypatch.setattr(
        notificaciones_generales,
        "get_connection",
        conexion_simulada,
    )

    intentos = []

    def email_simulado(**kwargs):
        intentos.append(kwargs["destinatario"])
        if kwargs["destinatario"] == "primero@example.com":
            raise RuntimeError("fallo simulado")
        return ResultadoEnvio(message_id="msg-segundo")

    monkeypatch.setattr(
        notificaciones_generales,
        "enviar_email",
        email_simulado,
    )

    resultado = notificaciones_generales.enviar_envios_pendientes()

    assert resultado == {
        "procesadas": 2,
        "enviadas": 1,
        "errores": 1,
    }

    assert intentos == [
        "primero@example.com",
        "segundo@example.com",
    ]

    assert len(ejecuciones) == 3

    sql_error, params_error = ejecuciones[1]
    assert "SET estado = 'ERROR'" in sql_error
    assert params_error[1] == 25

    sql_enviada, params_enviada = ejecuciones[2]
    assert "SET estado = 'ENVIADA'" in sql_enviada
    assert params_enviada == ("msg-segundo", 26)
