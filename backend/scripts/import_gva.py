from __future__ import annotations

import argparse

from app.gva import importar_gva


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa procesos de empleo público de la GVA")
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--max-details", type=int, default=None)
    args = parser.parse_args()

    resultado = importar_gva(
        max_paginas=max(1, args.max_pages),
        max_detalles=args.max_details,
    )
    print(resultado)


if __name__ == "__main__":
    main()
