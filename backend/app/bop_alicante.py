from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from xml.sax.saxutils import escape

import httpx

from .ambito_administrativo import clasificar_ambito_administrativo

ENDPOINT = (
    "https://sede.diputacionalicante.es/wp-content/themes/"
    "Desarrollo-Diputacion/webservices/wseConsultaAjax.php"
)


def _valor(registro: dict[str, Any], campo: str) -> str:
    valor = registro.get(campo)
    if isinstance(valor, list):
        return str(valor[0]).strip() if valor else ""
    return str(valor or "").strip()


def _param(desde: date, hasta: date) -> str:
    # Contrato real observado en DevTools: param es XML.
    return (
        "<Raiz><entrada><Registro>"
        f"<desde>{escape(desde.strftime('%d/%m/%Y'))}</desde>"
        f"<hasta>{escape(hasta.strftime('%d/%m/%Y'))}</hasta>"
        "<texto></texto>"
        "<tipoorganismo></tipoorganismo>"
        "<publicante></publicante>"
        "</Registro></entrada></Raiz>"
    )


def _normalizar(registro: dict[str, Any]) -> dict[str, Any]:
    extracto = _valor(registro, "extracto")
    definicion = _valor(registro, "definicion")
    denominacion = _valor(registro, "denominacion")
    publicante = _valor(registro, "ampliacion") or definicion
    edicto = _valor(registro, "edicto")
    anyo = _valor(registro, "anyo")
    numero_bop = _valor(registro, "nBop")
    ubicacion = _valor(registro, "ubicacion")

    texto_clasificacion = " ".join(x for x in (extracto, definicion, denominacion, publicante) if x)
    return {
        "referencia": f"BOPAL:{anyo}:{edicto}" if anyo and edicto else "",
        "anyo": anyo,
        "numero_bop": numero_bop,
        "fecha_publicacion": _valor(registro, "fechaPublica"),
        "edicto": edicto,
        "extracto": extracto,
        "organismo": publicante,
        "denominacion": denominacion,
        "seccion": _valor(registro, "desecun"),
        "url_documento": ubicacion,
        "ambito_administrativo": clasificar_ambito_administrativo(
            {"denominacion": texto_clasificacion, "cuerpo_escala": None, "grupo": None}
        ),
    }


def consultar_bop_alicante(
    *,
    dias_solape: int = 7,
    hasta: date | None = None,
    max_items: int = 500,
) -> dict[str, Any]:
    """SOLO_REVISION del BOP Alicante: consulta, normaliza y clasifica; no usa BD."""
    hasta = hasta or date.today()
    desde = hasta - timedelta(days=max(dias_solape, 0))

    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION",
        "fuente": "Boletín Oficial de la Provincia de Alicante",
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "descubiertos": 0,
        "administrativos": 0,
        "revision": 0,
        "errores": [],
        "detalle": [],
    }

    try:
        with httpx.Client(
            timeout=45,
            follow_redirects=True,
            headers={"User-Agent": "TuCoach-Empleo/1.0", "Accept": "application/json"},
        ) as client:
            respuesta = client.get(ENDPOINT, params={"nemo": "BOP_EDI", "param": _param(desde, hasta), "usuario": "-"})
            respuesta.raise_for_status()
            payload = respuesta.json()
    except Exception as exc:
        resultado["errores"].append(f"{type(exc).__name__}: {exc}")
        return resultado

    registros = payload.get("bop", {}).get("registro", [])
    if not isinstance(registros, list):
        resultado["errores"].append("Respuesta BOP Alicante sin bop.registro[]")
        return resultado

    detalle = [_normalizar(r) for r in registros[:max_items] if isinstance(r, dict)]
    resultado["descubiertos"] = len(detalle)
    resultado["administrativos"] = sum(1 for r in detalle if r["ambito_administrativo"] == "SI")
    resultado["revision"] = sum(1 for r in detalle if r["ambito_administrativo"] == "REVISION")
    resultado["detalle"] = detalle
    return resultado
