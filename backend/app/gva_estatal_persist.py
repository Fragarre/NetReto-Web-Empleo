from __future__ import annotations

from copy import deepcopy
from typing import Any

from .gva_estatal_import import LEGACY_ALIASES


def planificar_persistencia(
    registros: list[dict[str, Any]],
    existentes_por_identificador: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Genera un plan de persistencia sin escribir en BD.

    Reglas de seguridad:
    - Los cuatro procesos GVA legacy jamás se sobrescriben con campos funcionales.
      Solo se propone añadir metadatos de la referencia estatal a datos_json.
    - Los nuevos registros inequívocos se proponen como INSERT.
    - Los registros no publicables se dejan en REVISION y nunca se insertan aquí.
    - Si ya existe un identificador nuevo, solo se propone actualización de metadatos;
      no se sustituyen denominación, fechas, estado ni clasificación.
    """
    legacy_ids = set(LEGACY_ALIASES.values())
    acciones: list[dict[str, Any]] = []

    for original in registros:
        registro = deepcopy(original)
        identificador = registro["identificador_estable"]
        existente = existentes_por_identificador.get(identificador)

        if not registro.get("es_oportunidad"):
            acciones.append({
                "accion": "REVISION",
                "identificador_estable": identificador,
                "referencia_estatal": registro.get("referencia_estatal"),
                "motivo": registro.get("motivo") or "no_publicable_automaticamente",
            })
            continue

        metadatos = {
            "fuente_descubrimiento": "administracion.gob.es",
            "referencia_estatal": registro.get("referencia_estatal"),
            "url_estatal": (registro.get("datos_json") or {}).get("url_estatal"),
            "via_estatal": (registro.get("datos_json") or {}).get("via_estatal"),
            "codigos_administrativos": (registro.get("datos_json") or {}).get("codigos_administrativos"),
        }

        if existente is not None:
            acciones.append({
                "accion": "ENLAZAR_METADATOS",
                "identificador_estable": identificador,
                "proceso_id": existente.get("id"),
                "preservar_campos_funcionales": True,
                "metadatos_estatales": metadatos,
            })
            continue

        if identificador in legacy_ids:
            # Un alias legacy que no exista en BD es una anomalía: no crear un proceso
            # nuevo con ese identificador porque podría ocultar una pérdida de datos.
            acciones.append({
                "accion": "BLOQUEAR",
                "identificador_estable": identificador,
                "referencia_estatal": registro.get("referencia_estatal"),
                "motivo": "alias_legacy_ausente_en_bd",
            })
            continue

        acciones.append({
            "accion": "INSERTAR",
            "identificador_estable": identificador,
            "registro": registro,
        })

    resumen = {
        "insertar": sum(a["accion"] == "INSERTAR" for a in acciones),
        "enlazar_metadatos": sum(a["accion"] == "ENLAZAR_METADATOS" for a in acciones),
        "revision": sum(a["accion"] == "REVISION" for a in acciones),
        "bloquear": sum(a["accion"] == "BLOQUEAR" for a in acciones),
    }
    return {"modo": "SOLO_REVISION", "resumen": resumen, "acciones": acciones}
