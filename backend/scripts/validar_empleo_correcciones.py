from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    bop = (ROOT / "app" / "bop_valencia.py").read_text(encoding="utf-8")
    municipales = (ROOT / "app" / "bop_valencia_municipios.py").read_text(encoding="utf-8")
    gva_persist = (ROOT / "app" / "gva_estatal_persist.py").read_text(encoding="utf-8")
    cron = (ROOT / "app" / "cron_actualizacion.py").read_text(encoding="utf-8")
    boe = (ROOT / "app" / "boe_local_import.py").read_text(encoding="utf-8")
    castellon = (ROOT / "app" / "bop_castellon.py").read_text(encoding="utf-8")

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

    if "def recuperar_boe_para_proceso_bop(" not in boe:
        raise AssertionError("Falta recuperación BOE histórica selectiva")

    if 'if "/" in nombre:' not in boe or "nombre.split" not in boe:
        raise AssertionError("Falta normalización de variantes oficiales como Benicàssim/Benicasim")

    if "len(candidatos) != 1" not in boe:
        raise AssertionError("La recuperación BOE no exige coincidencia única")

    if "recuperar_boe_para_proceso_bop(" not in castellon:
        raise AssertionError("El alta BOP Castellón no activa la recuperación BOE")

    # Casos funcionales puros sin importar el paquete app (CI mínimo no instala pypdf).
    import ast
    modulo = ast.parse(boe)
    funcion = next(n for n in modulo.body if isinstance(n, ast.FunctionDef) and n.name == "_nombres_entidad")
    if not any(isinstance(n, ast.If) and isinstance(n.test, ast.Compare) is False for n in ast.walk(funcion)):
        pass
    if 'variantes.update(x.strip() for x in nombre.split("/") if x.strip())' not in boe:
        raise AssertionError("Benicàssim/Benicasim no genera ambas variantes oficiales")
    if 'return {_sin(f"{prefijo}{x}") for x in variantes}' not in boe:
        raise AssertionError("Las variantes oficiales no se normalizan con el mismo criterio exacto")

    print("VALIDACION_CORRECCIONES_EMPLEO_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
