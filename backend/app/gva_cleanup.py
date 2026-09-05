from __future__ import annotations

from datetime import date
from typing import Any

from .database import get_connection

GVA_ORGANISMO_ID = 1
FECHA_CORTE = date(2026, 1, 1)


def limpiar_gva_stale() -> dict[str, Any]:
    """Elimina del catálogo GVA procesos antiguos que ya no pertenecen al ámbito.

    Los procesos con suscripciones activas se conservan y se devuelven como
    bloqueados para evitar pérdida de referencias de usuarios.
    """
    resultado: dict[str, Any] = {
        "candidatos": 0,
        "eliminados": 0,
        "bloqueados_por_suscripciones": 0,
        "procesos_bloqueados": [],
        "motivos": {},
    }

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, identificador_estable, denominacion, anio_convocatoria,
                       ultima_publicacion_at, datos_json
                FROM procesos
                WHERE organismo_id = %s
                  AND identificador_estable LIKE 'GVA:%%'
                  AND (
                      COALESCE(datos_json->>'organismo_motivo', '') = 'organismo_externo'
                      OR LOWER(COALESCE(datos_json->>'organismo_detectado', '')) LIKE '%%justicia%%'
                      OR LOWER(COALESCE(datos_json->>'organismo_detectado', '')) LIKE '%%istecdigital%%'
                      OR LOWER(COALESCE(datos_json->>'organismo_detectado', '')) LIKE '%%iislafe%%'
                      OR (
                          COALESCE(anio_convocatoria, 0) NOT IN (2026, 2027)
                          AND (ultima_publicacion_at IS NULL OR ultima_publicacion_at < %s)
                      )
                  )
                ORDER BY id
                """,
                (GVA_ORGANISMO_ID, FECHA_CORTE),
            )
            candidatos = cursor.fetchall()
            resultado["candidatos"] = len(candidatos)

            for proceso_id, identificador, denominacion, anio, ultima_pub, datos in candidatos:
                cursor.execute(
                    "SELECT COUNT(*) FROM suscripciones WHERE proceso_id = %s AND activa = TRUE",
                    (proceso_id,),
                )
                suscripciones = cursor.fetchone()[0]
                if suscripciones:
                    resultado["bloqueados_por_suscripciones"] += 1
                    resultado["procesos_bloqueados"].append(
                        {"id": proceso_id, "identificador_estable": identificador, "suscripciones": suscripciones}
                    )
                    continue

                motivo = "organismo_externo"
                if not (
                    (datos or {}).get("organismo_motivo") == "organismo_externo"
                    or "justicia" in str((datos or {}).get("organismo_detectado", "")).lower()
                    or "istecdigital" in str((datos or {}).get("organismo_detectado", "")).lower()
                    or "iislafe" in str((datos or {}).get("organismo_detectado", "")).lower()
                ):
                    motivo = "fuera_ambito"

                cursor.execute(
                    """
                    DELETE FROM notificaciones
                    WHERE cambio_id IN (SELECT id FROM cambios WHERE proceso_id = %s)
                    """,
                    (proceso_id,),
                )
                cursor.execute("DELETE FROM cambios WHERE proceso_id = %s", (proceso_id,))
                cursor.execute("DELETE FROM publicaciones WHERE proceso_id = %s", (proceso_id,))
                cursor.execute("DELETE FROM procesos WHERE id = %s", (proceso_id,))
                if cursor.rowcount:
                    resultado["eliminados"] += 1
                    resultado["motivos"][motivo] = resultado["motivos"].get(motivo, 0) + 1

            connection.commit()

    return resultado


def corregir_turnos_gva() -> dict[str, int]:
    """Corrige el turno cuando la propia denominación contiene un turno explícito."""
    resultado = {"turno_libre": 0, "promocion_interna": 0, "discapacidad_intelectual": 0, "discapacidad": 0}
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE procesos
                SET turno = CASE
                    WHEN LOWER(denominacion) LIKE '%%turno libre%%' THEN 'TURNO_LIBRE'
                    WHEN LOWER(denominacion) LIKE '%%promoción interna%%'
                      OR LOWER(denominacion) LIKE '%%promocion interna%%' THEN 'PROMOCION_INTERNA'
                    WHEN LOWER(denominacion) LIKE '%%discapacidad intelectual%%' THEN 'DISCAPACIDAD_INTELECTUAL'
                    WHEN LOWER(denominacion) LIKE '%%discapacidad%%' THEN 'DISCAPACIDAD'
                    ELSE turno
                END,
                    updated_at = NOW()
                WHERE organismo_id = %s
                  AND (
                    LOWER(denominacion) LIKE '%%turno libre%%'
                    OR LOWER(denominacion) LIKE '%%promoción interna%%'
                    OR LOWER(denominacion) LIKE '%%promocion interna%%'
                    OR LOWER(denominacion) LIKE '%%discapacidad intelectual%%'
                    OR LOWER(denominacion) LIKE '%%discapacidad%%'
                  )
                RETURNING turno
                """,
                (GVA_ORGANISMO_ID,),
            )
            for (turno,) in cursor.fetchall():
                clave = {
                    "TURNO_LIBRE": "turno_libre",
                    "PROMOCION_INTERNA": "promocion_interna",
                    "DISCAPACIDAD_INTELECTUAL": "discapacidad_intelectual",
                    "DISCAPACIDAD": "discapacidad",
                }.get(turno)
                if clave:
                    resultado[clave] += 1
            connection.commit()
    return resultado
