from __future__ import annotations

import re
from typing import Any

from psycopg.types.json import Jsonb

from .database import get_connection
from .gva import (
    GVA_FUENTE_ID,
    GVA_ORGANISMO_ID,
    _sin_acentos,
    descubrir_detalles,
    parsear_detalle,
)


def _tipo_convocatoria(texto: str) -> str | None:
    normal = _sin_acentos(texto)
    patrones = (
        ("convocatoria bolsa de trabajo", "Bolsa de trabajo"),
        ("convocatoria oposicion", "Oposición"),
        ("convocatoria promocion interna", "Promoción interna"),
        ("convocatoria contratacion laboral temporal", "Contratación laboral temporal"),
        ("convocatoria contratacion laboral indefinida", "Contratación laboral indefinida"),
        ("convocatoria proceso de estabilizacion", "Proceso de estabilización"),
        ("acto unico telematico", "Acto único telemático"),
        ("acte unic telematic", "Acto único telemático"),
        ("anuncio dificil cobertura", "Anuncio difícil cobertura"),
    )
    for patron, valor in patrones:
        if patron in normal:
            return valor
    return None


def _es_incluido(tipo: str | None) -> bool:
    normal = _sin_acentos(tipo or "")
    return any(valor in normal for valor in (
        "oposicion", "bolsa de trabajo", "promocion interna",
        "contratacion laboral temporal", "contratacion laboral indefinida",
        "proceso de estabilizacion", "acto unico telematico",
        "acte unic telematic", "anuncio dificil cobertura",
    ))


def importar_gva_robusto(*, max_paginas: int = 1, max_detalles: int | None = 5) -> dict[str, Any]:
    estadisticas: dict[str, Any] = {
        "descubiertos": 0,
        "procesos": 0,
        "publicaciones": 0,
        "cambios": 0,
        "diagnostico": [],
    }

    import httpx

    headers = {
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        detalles = descubrir_detalles(client, max_paginas=max_paginas)
        if max_detalles is not None:
            detalles = detalles[:max_detalles]
        estadisticas["descubiertos"] = len(detalles)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                for id_emp, url in detalles:
                    respuesta = client.get(url)
                    respuesta.raise_for_status()
                    proceso = parsear_detalle(url, respuesta.text, id_emp)
                    texto = proceso["publicacion"]["contenido_texto"]
                    tipo_parser = proceso.get("tipo_proceso")
                    tipo_convocatoria = _tipo_convocatoria(texto)
                    tipo_final = tipo_parser or tipo_convocatoria
                    proceso["tipo_proceso"] = tipo_final

                    if not _es_incluido(tipo_final):
                        estadisticas["diagnostico"].append({
                            "id_emp": id_emp,
                            "url": url,
                            "tipo_parser": tipo_parser,
                            "tipo_convocatoria": tipo_convocatoria,
                            "tipo_final": tipo_final,
                            "fragmentos": [
                                texto[max(0, m.start()-80):m.end()+180]
                                for m in re.finditer(r"Convocatoria|Proceso selectivo|Tipo de prueba|Grupo|Plazas", texto, re.I)
                            ][:8],
                        })
                        continue

                    campos = [
                        "denominacion", "grupo", "tipo_proceso", "turno", "plazas",
                        "estado", "anio_convocatoria", "fecha_apertura", "fecha_cierre",
                    ]
                    cursor.execute(
                        "SELECT id, denominacion, grupo, tipo_proceso, turno, plazas, estado, anio_convocatoria, fecha_apertura, fecha_cierre FROM procesos WHERE identificador_estable = %s",
                        (proceso["identificador_estable"],),
                    )
                    existente = cursor.fetchone()
                    valores = tuple(proceso.get(c) for c in campos)

                    if existente:
                        proceso_id = existente[0]
                        for indice, campo in enumerate(campos, start=1):
                            anterior = existente[indice]
                            nuevo = valores[indice - 1]
                            if anterior != nuevo:
                                cursor.execute(
                                    "INSERT INTO cambios (proceso_id, tipo, campo, valor_anterior, valor_nuevo, resumen) VALUES (%s,%s,%s,%s,%s,%s)",
                                    (proceso_id, "ACTUALIZACION", campo, str(anterior) if anterior is not None else None, str(nuevo) if nuevo is not None else None, f"Cambio en {campo}: {anterior!r} -> {nuevo!r}"),
                                )
                                estadisticas["cambios"] += 1
                        cursor.execute(
                            """UPDATE procesos SET codigo_externo=%s, denominacion=%s, grupo=%s,
                            tipo_proceso=%s, turno=%s, plazas=%s, estado=%s,
                            anio_convocatoria=%s, fecha_apertura=%s, fecha_cierre=%s,
                            ultima_publicacion_at=COALESCE(%s, ultima_publicacion_at),
                            fuente_principal_id=%s, datos_json=%s, updated_at=NOW()
                            WHERE id=%s""",
                            (proceso["codigo_externo"], proceso["denominacion"], proceso["grupo"], proceso["tipo_proceso"], proceso["turno"], proceso["plazas"], proceso["estado"], proceso["anio_convocatoria"], proceso["fecha_apertura"], proceso["fecha_cierre"], proceso["ultima_publicacion_at"], GVA_FUENTE_ID, Jsonb(proceso["datos_json"]), proceso_id),
                        )
                    else:
                        cursor.execute(
                            """INSERT INTO procesos
                            (organismo_id, codigo_externo, identificador_estable, denominacion,
                             grupo, tipo_proceso, turno, plazas, estado, anio_convocatoria,
                             fecha_apertura, fecha_cierre, ultima_publicacion_at,
                             fuente_principal_id, datos_json)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            RETURNING id""",
                            (GVA_ORGANISMO_ID, proceso["codigo_externo"], proceso["identificador_estable"], proceso["denominacion"], proceso["grupo"], proceso["tipo_proceso"], proceso["turno"], proceso["plazas"], proceso["estado"], proceso["anio_convocatoria"], proceso["fecha_apertura"], proceso["fecha_cierre"], proceso["ultima_publicacion_at"], GVA_FUENTE_ID, Jsonb(proceso["datos_json"])),
                        )
                        proceso_id = cursor.fetchone()[0]

                    estadisticas["procesos"] += 1
                    publicacion = proceso["publicacion"]
                    cursor.execute("SELECT 1 FROM publicaciones WHERE proceso_id=%s AND contenido_hash=%s LIMIT 1", (proceso_id, publicacion["contenido_hash"]))
                    if cursor.fetchone() is None:
                        cursor.execute(
                            """INSERT INTO publicaciones
                            (proceso_id, fuente_id, referencia, tipo, titulo, fecha_publicacion,
                             url, contenido_hash, contenido_texto, datos_json)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (proceso_id, GVA_FUENTE_ID, publicacion["referencia"], publicacion["tipo"], publicacion["titulo"], publicacion["fecha_publicacion"], publicacion["url"], publicacion["contenido_hash"], publicacion["contenido_texto"], Jsonb(publicacion["datos_json"])),
                        )
                        estadisticas["publicaciones"] += 1

            connection.commit()

    return estadisticas
