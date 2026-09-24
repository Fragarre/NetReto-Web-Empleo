"""Envío manual y aislado para validar la integración con Resend.

No se conecta al cron, a la base de datos ni a los flujos de notificaciones.
El destinatario de prueba está fijado deliberadamente.
"""

from __future__ import annotations

import argparse

from app.email_sender import enviar_email


DESTINATARIO_PRUEBA = "fragarre@outlook.es"


def main() -> int:
    parser = argparse.ArgumentParser(description="Envía un único correo de prueba de Tu Coach")
    parser.parse_args()

    resultado = enviar_email(
        destinatario=DESTINATARIO_PRUEBA,
        asunto="Tu Coach — prueba de notificaciones de empleo",
        html=(
            "<p>Este es un correo de prueba del sistema de notificaciones "
            "de empleo de Tu Coach.</p>"
            "<p>No corresponde a una oportunidad real.</p>"
        ),
        texto=(
            "Este es un correo de prueba del sistema de notificaciones de empleo "
            "de Tu Coach. No corresponde a una oportunidad real."
        ),
    )
    print(f"Envío aceptado por Resend. id={resultado.message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
