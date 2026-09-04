import os

import psycopg
from fastapi import FastAPI

app = FastAPI(title="NetReto Empleo API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "netreto-empleo"}


@app.get("/db-health")
def db_health() -> dict[str, str]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return {"status": "error", "detail": "DATABASE_URL no configurada"}

    try:
        with psycopg.connect(database_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                result = cur.fetchone()
        if result != (1,):
            return {"status": "error", "detail": "respuesta inesperada de PostgreSQL"}
        return {"status": "ok", "service": "netreto-empleo", "database": "postgresql"}
    except Exception:
        return {"status": "error", "detail": "no se pudo conectar a PostgreSQL"}
