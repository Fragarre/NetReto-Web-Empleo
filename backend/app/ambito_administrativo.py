from __future__ import annotations

import re
import unicodedata
from typing import Any

AMBITOS = ("SI", "NO", "REVISION")


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto or "")
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip().lower()


def clasificar_ambito_administrativo(proceso: dict[str, Any]) -> str:
    """Clasificación automática conservadora para el catálogo de Tu Coach.

    Solo devuelve SI/NO cuando la denominación es suficientemente explícita.
    Los casos fronterizos permanecen en REVISION para decisión humana.
    """
    texto = _normalizar(" ".join(str(proceso.get(k) or "") for k in ("denominacion", "cuerpo_escala", "grupo")))

    # Cuerpos y perfiles administrativos en sentido amplio.
    patrones_si = (
        r"\bc1-01\b",
        r"\bc2-01\b",
        r"\ba1-01\b",
        r"\ba2-01\b",
        r"\bc1-07\b",
        r"\ba2-05\b",
        r"\bcuerpo administrativo\b",
        r"\bcuerpo auxiliar\b",
        r"\bcuerpo superior de administracion\b",
        r"\bcuerpo superior de gestion\b",
        r"\badministracion general\b",
        r"\btecnico(?:/a)? de administracion general\b",
        r"\bauxiliar administrativo\b",
        r"\badministrativo(?:/a)?\b",
        r"\bagentes? tributarios?\b",
        r"\btecnico(?:/a)? tributario\b",
        r"\bgestion tributaria\b",
    )
    if any(re.search(p, texto) for p in patrones_si):
        return "SI"

    # Exclusiones claras. No se usan términos genéricos que puedan aparecer
    # incidentalmente en una denominación administrativa.
    patrones_no = (
        r"investigacion cientifica",
        r"medicina(?: del trabajo)?",
        r"enfermeria",
        r"psicologia",
        r"educacion especial",
        r"educacion infantil",
        r"veterinari",
        r"ingenier",
        r"arquitect",
        r"laboratorio",
        r"servicios auxiliares de la investigacion",
        r"\bsubalternos?\b",
        r"ayudante de residencia",
        r"agricol",
        r"forestal",
        r"orientacion escolar",
        r"taller ocupacional",
        r"obras publicas",
    )
    if any(re.search(p, texto) for p in patrones_no):
        return "NO"

    return "REVISION"
