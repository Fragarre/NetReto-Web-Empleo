from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    bop = (ROOT / "app" / "bop_valencia.py").read_text(encoding="utf-8")
    municipales = (ROOT / "app" / "bop_valencia_municipios.py").read_text(encoding="utf-8")

    if "fecha.year not in {2026, 2027}" in bop:
        raise AssertionError("Sigue existiendo el límite artificial 2026/2027 en BOP Valencia")

    if 'updates.extend(["ultima_publicacion_at=%s", "updated_at=NOW()"])' in municipales:
        raise AssertionError("El seguimiento municipal sigue actualizando siempre aunque la publicación ya exista")

    if '"seguimientos_sin_cambios": 0' not in municipales:
        raise AssertionError("Falta contabilizar explícitamente los seguimientos sin cambios")

    if 'item["estado_importacion"] = "SIN_CAMBIOS"' not in municipales:
        raise AssertionError("Falta estado SIN_CAMBIOS en seguimiento municipal idempotente")

    print("VALIDACION_CORRECCIONES_EMPLEO_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
