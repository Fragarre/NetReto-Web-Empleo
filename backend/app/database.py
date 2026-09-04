import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import Connection


@contextmanager
def get_connection() -> Iterator[Connection]:
    """Abre una conexión PostgreSQL para una operación del backend.

    DATABASE_URL debe apuntar a la base de datos PostgreSQL independiente
    de NetReto Empleo. La conexión se cierra siempre al finalizar el bloque.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL no está configurada")

    with psycopg.connect(database_url, connect_timeout=10) as connection:
        yield connection
