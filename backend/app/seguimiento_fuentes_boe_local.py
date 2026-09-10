from __future__ import annotations

import re
import unicodedata
from typing import Any


# Configuración validada a partir de las bases oficiales revisadas en el Punto 6.
# No sustituye a publicaciones/fuentes de BD: describe qué canales deben vigilarse
# para cada proceso BOE local y será consumida por la automatización del Punto 8.
FUENTES_SEGUIMIENTO_VALIDADAS: dict[str, list[dict[str, Any]]] = {
    "BOELOCAL:BOE-A-2026-17977#1": [
        {"tipo": "BOP", "prioridad": 20, "uso": "hitos_formales"},
        {"tipo": "SEDE_TABLON", "prioridad": 10, "url": "https://sede.xirivella.es/", "uso": "seguimiento_ordinario"},
    ],
    "BOELOCAL:BOE-A-2026-17977#2": [
        {"tipo": "BOP", "prioridad": 20, "uso": "hitos_formales"},
        {"tipo": "SEDE_TABLON", "prioridad": 10, "url": "https://sede.xirivella.es/", "uso": "seguimiento_ordinario"},
    ],
    "BOELOCAL:BOE-A-2026-18240#1": [
        {"tipo": "BOP", "prioridad": 20, "uso": "hitos_formales"},
        {"tipo": "SEDE_TABLON", "prioridad": 10, "url": "https://petres.sedelectronica.es/", "uso": "seguimiento_ordinario"},
    ],
    "BOELOCAL:BOE-A-2026-18402#1": [
        {"tipo": "BOP", "prioridad": 20, "uso": "lista_provisional"},
        {"tipo": "SEDE", "prioridad": 10, "url": "https://manlacomuna.sede.dival.es/default.aspx", "uso": "listas"},
        {"tipo": "SEDE_TABLON", "prioridad": 5, "url": "https://mancomunidad-para-servicios-de-bienestar-social-de-lenova.sedelectronica.es/info.0", "uso": "ejercicios_resultados_decisiones"},
        {"tipo": "WEB", "prioridad": 15, "url": "https://www.mancomunitatlacomuna.es/", "uso": "seguimiento_ordinario"},
    ],
    "BOELOCAL:BOE-A-2026-18598#1": [
        {"tipo": "BOP", "prioridad": 20, "uso": "lista_definitiva_primer_ejercicio"},
        {"tipo": "SEDE_TABLON", "prioridad": 10, "url": "https://silla.sedipualba.es/", "uso": "ejercicios_sucesivos_resultados_aprobados"},
    ],
    "BOELOCAL:BOE-A-2026-18846#1": [
        {"tipo": "BOP", "prioridad": 20, "uso": "listas_admitidos"},
        {"tipo": "SEDE_TABLON", "prioridad": 10, "url": "https://massanassa.sedelectronica.es/", "uso": "seguimiento_ordinario"},
        {"tipo": "WEB", "prioridad": 50, "url": "https://www.massanassa.es/", "uso": "complementario_no_preceptivo"},
    ],
}


def _sin(texto: str | None) -> str:
    valor = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in valor if unicodedata.category(c) != "Mn")


def fuentes_seguimiento(identificador_estable: str) -> list[dict[str, Any]]:
    return [dict(f) for f in FUENTES_SEGUIMIENTO_VALIDADAS.get(identificador_estable, [])]


def clasificar_hito(titulo: str | None) -> str:
    """Clasifica un anuncio de continuidad sin alterar la decisión de vinculación."""
    n = _sin(titulo)
    if any(x in n for x in ("lista provisional", "relacion provisional", "relacio provisional", "admitidos provisional", "admesos provisional")):
        return "ADMITIDOS_PROVISIONAL"
    if any(x in n for x in ("lista definitiva", "relacion definitiva", "relacio definitiva", "admitidos definitiva", "admesos definitiva")):
        return "ADMITIDOS_DEFINITIVA"
    if any(x in n for x in ("tribunal", "organ tecnico de seleccion", "organ tecnic de seleccio")):
        return "TRIBUNAL"
    if any(x in n for x in ("primer ejercicio", "primer exercici", "fecha de examen", "data d'examen", "convocatoria ejercicio", "convocatoria exercici")):
        return "EXAMEN"
    if any(x in n for x in ("resultado", "resultat", "calificacion", "qualificacio", "puntuacion", "puntuacio")):
        return "RESULTADOS"
    if any(x in n for x in ("personas aprobadas", "persones aprovades", "propuesta de aprobados", "proposta d'aprovats")):
        return "APROBADOS"
    if any(x in n for x in ("nombramiento", "nomenament", "toma de posesion", "presa de possessio")):
        return "NOMBRAMIENTO"
    if re.search(r"\b(correccion|correccio)\b", n):
        return "CORRECCION"
    return "OTRO_SEGUIMIENTO"
