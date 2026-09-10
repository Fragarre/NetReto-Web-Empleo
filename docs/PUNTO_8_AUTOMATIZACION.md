# Punto 8 — Automatización periódica

La actualización de NetReto Empleo se ejecuta diariamente mediante GitHub Actions.

- Horario: 07:00 UTC (aprox. 09:00 CEST / 08:00 CET en España).
- Workflow: `.github/workflows/empleo-actualizacion.yml`.
- Ejecutor: `backend/app/cron_actualizacion.py`.
- Fuentes incluidas: GVA, BOE local y BOP municipal de Valencia.
- Tras la importación se preparan las notificaciones de seguimiento.
- La ejecución requiere el secreto de repositorio `EMPLOYMENT_IMPORT_SECRET`.
- También puede lanzarse manualmente mediante `workflow_dispatch`.
