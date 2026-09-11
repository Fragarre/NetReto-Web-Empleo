from __future__ import annotations

from datetime import date
from typing import Any

from .gva_estatal_import import construir_registro
from .gva_estatal_persist import persistir_registros
from .gva_estatal_seguimiento import actualizar_seguimientos_gva
from .gva_estatal_source import (
    clasificar_oportunidad,
    descubrir_referencias,
    nuevo_cliente,
    obtener_detalle,
)


def importar_gva_estatal(*, desde: date, hasta: date, aplicar: bool = False) -> dict[str, Any]:
    """Descubre convocatorias nuevas y revisa las fichas GVA ya conocidas.

    `aplicar=False` es el modo por defecto y no escribe en base de datos.
    Las fechas delimitan únicamente el descubrimiento de nuevas convocatorias;
    el seguimiento de procesos activos se hace por sus fichas ya persistidas.
    """
    if desde > hasta:
        raise ValueError("La fecha 'desde' no puede ser posterior a 'hasta'")

    registros: list[dict[str, Any]] = []
    revision: list[dict[str, Any]] = []
    excluidas = {
        "fuera_ambito_administrativo": 0,
        "promocion_interna": 0,
        "organismo_excluido": 0,
        "via_no_incluida": 0,
    }

    with nuevo_cliente() as client:
        tarjetas = descubrir_referencias(client, desde, hasta)
        for tarjeta in tarjetas:
            detalle = obtener_detalle(client, int(tarjeta["referencia"]))
            clasificada = clasificar_oportunidad(tarjeta, detalle)

            if clasificada.get("es_oportunidad"):
                registros.append(construir_registro(tarjeta, detalle))
                continue

            motivo = clasificada.get("motivo") or "revision"
            if motivo == "organismo_revision" or (
                clasificada.get("ambito_administrativo") == "SI"
                and motivo == "via_no_incluida"
            ):
                revision.append({
                    "referencia_estatal": clasificada.get("referencia"),
                    "titulo": clasificada.get("titulo"),
                    "organo": clasificada.get("organo") or clasificada.get("organo_detalle"),
                    "via": clasificada.get("via"),
                    "codigos_administrativos": clasificada.get("codigos_administrativos") or [],
                    "motivo": motivo,
                    "url": clasificada.get("url"),
                })
                continue

            excluidas[motivo] = excluidas.get(motivo, 0) + 1

    persistencia = persistir_registros(registros, aplicar=aplicar)
    seguimiento = actualizar_seguimientos_gva(aplicar=aplicar)
    return {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "tarjetas_autonomico_cv": len(tarjetas),
        "oportunidades_incluidas": len(registros),
        "revision_manual": len(revision),
        "excluidas": excluidas,
        "revision": revision,
        "persistencia": persistencia,
        "seguimiento": seguimiento,
    }
