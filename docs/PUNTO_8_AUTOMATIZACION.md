# Punto 8 — Automatización periódica

La actualización de NetReto Empleo se ejecuta diariamente mediante GitHub Actions.

- Horario: 07:00 UTC (aprox. 09:00 CEST / 08:00 CET en España).
- Workflow: `.github/workflows/empleo-actualizacion.yml`.
- Ejecutor: `backend/app/cron_actualizacion.py`.
- Fuentes oficiales actualizadas: GVA, BOP de la Diputación Provincial de Valencia, BOE Administración Local y BOP municipal de Valencia.
- Tras la importación se preparan las notificaciones de seguimiento.
- La ejecución requiere el secreto de repositorio `EMPLOYMENT_IMPORT_SECRET`.
- También puede lanzarse manualmente mediante `workflow_dispatch`.

## Regla funcional de cobertura

La antigüedad de una convocatoria y el cierre del plazo de solicitudes **no son criterios de exclusión**. Una oportunidad administrativa permanece en el catálogo mientras el proceso selectivo siga activo y cumpla las reglas de inclusión del producto.

Los `30 días` utilizados por los importadores BOE/BOP durante la actualización diaria son únicamente un **solape técnico de consulta** para tolerar fallos temporales y reintentos. No determinan qué oportunidades son válidas ni cuándo dejan de serlo.

En GVA se separan expresamente dos conceptos:

1. `estado_plazo_solicitud`: abierto, pendiente o cerrado;
2. `estado` del proceso selectivo: en curso o finalizado según la etapa oficial.

Además de descubrir nuevas fichas GVA, cada actualización vuelve a consultar las oportunidades GVA ya conocidas que todavía no tienen un estado terminal, aunque su plazo de solicitud haya finalizado.

## Control de cobertura

Auténtica Oposiciones se utiliza exclusivamente como contraste externo para detectar posibles huecos de cobertura. Nunca sustituye a las fuentes oficiales ni aporta reglas de inclusión. Cuando existe una discrepancia, la decisión se valida contra BOE, BOP o GVA según corresponda.
