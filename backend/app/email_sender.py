"""Cliente mínimo de correo transaccional para TuCoach Empleo.

Este módulo no decide destinatarios ni genera eventos de notificación.
Solo encapsula el envío de un mensaje ya preparado mediante Resend.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "Tu Coach <avisos@tucoach-oposiciones.com>"


@dataclass(frozen=True)
class ResultadoEnvio:
    message_id: str


def enviar_email(
    *,
    destinatario: str,
    asunto: str,
    html: str,
    texto: str | None = None,
) -> ResultadoEnvio:
    """Envía un único correo ya preparado y devuelve el id de Resend."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Falta RESEND_API_KEY")

    remitente = os.getenv("EMPLOYMENT_EMAIL_FROM", DEFAULT_FROM).strip()
    if not remitente:
        raise RuntimeError("EMPLOYMENT_EMAIL_FROM no puede estar vacío")

    payload: dict[str, object] = {
        "from": remitente,
        "to": [destinatario],
        "subject": asunto,
        "html": html,
    }
    if texto is not None:
        payload["text"] = texto

    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if response.is_error:
        raise RuntimeError(
            f"Resend devolvió HTTP {response.status_code}: {response.text[:500]}"
        )

    data = response.json()
    message_id = str(data.get("id") or "").strip()
    if not message_id:
        raise RuntimeError("Resend respondió sin id de mensaje")

    return ResultadoEnvio(message_id=message_id)
