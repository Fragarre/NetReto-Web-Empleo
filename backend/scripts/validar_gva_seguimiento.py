from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.gva_estatal_seguimiento import (  # noqa: E402
    _obtener_html,
    extraer_seguimientos_validos,
    planificar_seguimientos,
)
from app.gva_estatal_source import nuevo_cliente  # noqa: E402


def main() -> int:
    with nuevo_cliente() as client:
        a1 = extraer_seguimientos_validos(_obtener_html(client, 221774))
        c1 = extraer_seguimientos_validos(_obtener_html(client, 219862))

    a1_validos = {x["signatura"] for x in a1["validos"]}
    c1_validos = {x["signatura"] for x in c1["validos"]}
    c1_rechazados = {x["signatura"] for x in c1["rechazados"]}

    assert "2026_24485" in a1_validos, a1
    assert "2026_27166" not in c1_validos, c1
    assert "2026_27166" in c1_rechazados, c1

    resultado = {1: a1}
    base = {
        "id": 1,
        "identificador_estable": "GVAESTATAL:221774",
        "referencia_estatal": 221774,
        "datos_json": {},
    }
    plan_baseline = planificar_seguimientos([base], resultado)
    assert plan_baseline["resumen"]["baseline"] == 1
    assert plan_baseline["resumen"]["publicaciones_nuevas"] == 0

    con_baseline = {
        **base,
        "datos_json": {
            "seguimiento_gva": {
                "inicializado": True,
                "vistos": ["2026_24485"],
            }
        },
    }
    plan_noop = planificar_seguimientos([con_baseline], resultado)
    assert plan_noop["resumen"]["sin_cambios"] == 1
    assert plan_noop["resumen"]["publicaciones_nuevas"] == 0

    antes_correccion = {
        **base,
        "datos_json": {
            "seguimiento_gva": {
                "inicializado": True,
                "vistos": [],
            }
        },
    }
    plan_novedad = planificar_seguimientos([antes_correccion], resultado)
    assert plan_novedad["resumen"]["procesos_con_novedades"] == 1
    assert plan_novedad["resumen"]["publicaciones_nuevas"] == 1

    print("VALIDACION GVA SEGUIMIENTO: OK")
    print("A1-01 validos:", sorted(a1_validos))
    print("C1-01 rechazados:", sorted(c1_rechazados))
    print("Baseline silencioso: OK")
    print("Segunda pasada SIN_CAMBIOS: OK")
    print("Nueva publicacion posterior: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
