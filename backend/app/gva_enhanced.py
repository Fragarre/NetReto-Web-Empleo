from __future__ import annotations

import re
from typing import Any

from . import gva_clean as base
from .database import get_connection


def _tipo_convocatoria(texto: str) -> str | None:
    normal = base._sin_acentos(texto)
    # Hay fichas GVA que no tienen campo "Prueba" (especialmente bolsas
    # de Sanidad). En esas fichas el tipo termina antes de Grupo/Titulación.
    m = re.search(
        r"convocatoria\s+(.{1,120}?)(?:\s+prueba\s+|\s+grupo\s+|\s+titulacion\s+|\s+enlace a organismo\s+)",
        normal,
        re.I,
    )
    if not m:
        return None
    valor = base._normalizar(m.group(1))
    patrones = (
        ("bolsa de trabajo", "Bolsa de trabajo"),
        ("oposicion", "Oposición"),
        ("promocion interna", "Promoción interna"),
        ("contratacion laboral temporal", "Contratación laboral temporal"),
        ("contratacion laboral indefinida", "Contratación laboral indefinida"),
        ("proceso de estabilizacion", "Proceso de estabilización"),
        ("acto unico telematico", "Acto único telemático"),
        ("acte unic telematic", "Acto único telemático"),
        ("anuncio dificil cobertura", "Anuncio difícil cobertura"),
        ("concurso general de meritos", "Concurso general de méritos"),
        ("concurso-oposicion", "Concurso-oposición"),
        ("concurso", "Concurso"),
        ("cobertura interina", "Cobertura interina"),
        ("comision de servicio", "Comisión de servicio"),
        ("libre designacion", "Libre designación"),
        ("seleccion personal directivo", "Selección personal directivo"),
        ("procesos especiales", "Procesos especiales"),
        ("otros", "Otros"),
    )
    for patron, nombre in patrones:
        if patron in valor:
            return nombre
    return base._normalizar(m.group(1))


def _es_incluido(tipo: str | None) -> bool:
    normal = base._sin_acentos(tipo or "")
    return any(
        patron in normal
        for patron in (
            "oposicion",
            "bolsa de trabajo",
            "promocion interna",
            "contratacion laboral",
            "proceso de estabilizacion",
            "acto unico telematico",
            "acte unic telematic",
            "anuncio dificil cobertura",
            "concurso general de meritos",
            "concurso-oposicion",
            "concurso",
            "cobertura interina",
            "comision de servicio",
            "libre designacion",
            "seleccion personal directivo",
        )
    )


base._tipo_convocatoria = _tipo_convocatoria
base._es_incluido = _es_incluido

importar_gva_robusto = base.importar_gva_robusto


def limpiar_gva_navegacion() -> dict[str, int]:
    """Elimina únicamente registros creados por el parser antiguo que dejó
    'Navegación' como denominación y marcó explícitamente ese error.
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM procesos
                WHERE organismo_id = %s
                  AND denominacion = 'Navegación'
                  AND datos_json->>'organismo_detectado' = 'Navegación'
                """,
                (base.GVA_ORGANISMO_ID,),
            )
            ids = [row[0] for row in cursor.fetchall()]
            if not ids:
                connection.commit()
                return {"procesos_eliminados": 0, "publicaciones_eliminadas": 0, "cambios_eliminados": 0}

            cursor.execute("DELETE FROM cambios WHERE proceso_id = ANY(%s)", (ids,))
            cambios = cursor.rowcount
            cursor.execute("DELETE FROM publicaciones WHERE proceso_id = ANY(%s)", (ids,))
            publicaciones = cursor.rowcount
            cursor.execute("DELETE FROM procesos WHERE id = ANY(%s)", (ids,))
            procesos = cursor.rowcount
            connection.commit()

    return {
        "procesos_eliminados": procesos,
        "publicaciones_eliminadas": publicaciones,
        "cambios_eliminados": cambios,
    }
