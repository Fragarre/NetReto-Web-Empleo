from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    bop = (ROOT / "app" / "bop_valencia.py").read_text(encoding="utf-8")
    municipales = (ROOT / "app" / "bop_valencia_municipios.py").read_text(encoding="utf-8")
    gva_persist = (ROOT / "app" / "gva_estatal_persist.py").read_text(encoding="utf-8")
    cron = (ROOT / "app" / "cron_actualizacion.py").read_text(encoding="utf-8")

    if "fecha.year not in {2026, 2027}" in bop:
        raise AssertionError("Sigue existiendo el límite artificial 2026/2027 en BOP Valencia")

    if 'updates.extend(["ultima_publicacion_at=%s", "updated_at=NOW()"])' in municipales:
        raise AssertionError("El seguimiento municipal sigue actualizando siempre aunque la publicación ya exista")

    if '"seguimientos_sin_cambios": 0' not in municipales:
        raise AssertionError("Falta contabilizar explícitamente los seguimientos sin cambios")

    if 'item["estado_importacion"] = "SIN_CAMBIOS"' not in municipales:
        raise AssertionError("Falta estado SIN_CAMBIOS en seguimiento municipal idempotente")

    if "FUENTE_GVA_DESCUBRIMIENTO_ID" in gva_persist:
        raise AssertionError("La persistencia GVA sigue dependiendo de la antigua fuente sede.gva.es")

    if "fuente_dogv_id" not in gva_persist:
        raise AssertionError("Las nuevas GVA no usan explícitamente la fuente DOGV")

    if "/admin/import/gva-estatal" not in cron:
        raise AssertionError("El cron no utiliza la nueva fuente estatal GVA")

    if "VENTANA_GVA_DIAS = 3" not in cron:
        raise AssertionError("La actualización GVA no conserva el solape técnico corto acordado")

    print("VALIDACION_CORRECCIONES_EMPLEO_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
