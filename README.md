# NetReto Web — Empleo

Módulo independiente de seguimiento de empleo público para NetReto.

## Principios

- Repositorio y base de datos independientes de `OpoCoach-Web` / NetReto actual.
- No modificar la base de datos actual de NetReto.
- Fuentes oficiales como referencia principal.
- Un proceso selectivo mantiene identidad estable e historial de publicaciones y cambios.
- Acceso inicial: usuario autenticado con suscripción activa.
- La capacidad de acceso a Empleo queda separada para permitir un futuro plan o complemento específico.

## Estado

Fundación inicial del proyecto. No contiene todavía integración con producción ni con el dominio público de NetReto.

## Estructura prevista

- `frontend/` — interfaz de usuario.
- `backend/` — API y lógica de negocio.
- `database/` — esquema y migraciones de la BD independiente.
- `recolectores/` — obtención desde fuentes oficiales.
- `normalizacion/` — identificación estable y detección de cambios.
- `notificaciones/` — preparación y envío de avisos.
