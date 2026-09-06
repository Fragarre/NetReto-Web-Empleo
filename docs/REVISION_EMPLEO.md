# Revisión y estado de NetReto Empleo

**Fecha de cierre de esta revisión:** 6 de septiembre de 2026

## 1. Propósito

Empleo es un módulo de NetReto orientado al opositor. Su función no es presentar un histórico de anuncios administrativos, sino permitir localizar oportunidades de acceso al empleo público y, opcionalmente, seguir una convocatoria concreta para consultar sus novedades oficiales.

## 2. Arquitectura actual

El módulo mantiene repositorio y base de datos independientes de OpoCoach-Web.

- Backend: `Fragarre/NetReto-Web-Empleo`
- Base de datos de Empleo: proyecto Supabase `netreto-empleo`
- Frontend: `Fragarre/OpoCoach-Web`, servido en NetExamenes (`netexamenes.com`)
- Backend desplegado en Render como `netreto-empleo-api`
- Frontend desplegado en Vercel dentro del proyecto de NetExamenes

La base de datos contiene principalmente:

- `organismos`
- `fuentes`
- `procesos`
- `publicaciones`
- `cambios`
- `suscripciones`
- `notificaciones`

La relación de seguimiento es `usuario -> proceso` mediante `suscripciones`.

## 3. Catálogo de oportunidades

Se decidió separar una **oportunidad para el opositor** de una **publicación o anuncio asociado**.

El campo `procesos.es_oportunidad` determina si un proceso puede aparecer en el catálogo global.

La pantalla global de Empleo muestra únicamente oportunidades activas y presenta información resumida para permitir decidir rápidamente si interesa:

- organismo;
- identificación de convocatoria, cuando existe;
- descripción/cuerpo o puesto;
- turno y grupo cuando están disponibles;
- número de plazas cuando está disponible;
- fecha de apertura y cierre cuando están disponibles.

No se muestran como oportunidades independientes los anuncios administrativos de seguimiento, modificaciones internas, designaciones, etc.

Durante la revisión se corrigió expresamente la inclusión accidental de procesos de promoción interna y se normalizaron varios casos de la GVA.

**Situación comprobada al cierre:** 11 oportunidades activas en el catálogo de GVA que cumplen el criterio actual de inclusión.

## 4. Fuentes y clasificación

La fase inicial está centrada en la Generalitat Valenciana. También existe tratamiento para BOP Valencia, pero los anuncios que no representan una oportunidad de empleo para el usuario no deben entrar en el catálogo global.

Se mantienen reglas de exclusión para categorías como promoción interna, concurso de traslados, libre designación, concurso general de méritos, comisiones de servicio y otros procedimientos de provisión que no representan una oportunidad de acceso compatible con el producto.

En GVA se intenta obtener además la etapa actual de la ficha oficial y datos básicos de la convocatoria.

## 5. Seguimiento de convocatorias

El usuario autenticado puede seguir una oportunidad concreta.

Funciones disponibles:

- seguir una convocatoria;
- dejar de seguirla;
- consultar las convocatorias seguidas;
- abrir la convocatoria/fuente oficial;
- consultar las últimas novedades relacionadas con las convocatorias seguidas.

El backend dispone de los endpoints de seguimiento correspondientes.

## 6. Últimas novedades

La sección `Mi seguimiento` combina dos tipos de información:

### Publicaciones oficiales

Se muestran únicamente publicaciones que pueden resultar útiles para el opositor, evitando elementos técnicos como `Navegación`.

### Cambios relevantes

Se muestran cambios sobre datos de interés para el opositor, por ejemplo:

- fecha de apertura o cierre;
- fecha de examen;
- estado;
- número de plazas;
- turno;
- etapa actual;
- tipo de proceso.

Las altas iniciales desde `NULL` no se consideran por sí mismas una novedad para el usuario.

## 7. Aviso al iniciar sesión

Se ha elegido deliberadamente una solución sencilla en lugar de correo electrónico.

Cuando un usuario autenticado entra en la aplicación principal, NetReto comprueba si hay novedades relevantes en las convocatorias que sigue y, cuando las hay, muestra un aviso con acceso directo a `Mi seguimiento`.

Mensaje previsto:

> Tienes novedades en las convocatorias que sigues. Revisa Mi seguimiento para consultar la información oficial.

Actualmente el estado de "última novedad vista" del aviso se guarda en `localStorage`. Esto funciona para una primera versión, pero queda pendiente trasladarlo a la base de datos para que el estado de lectura acompañe al usuario entre dispositivos y navegadores.

## 8. Correo electrónico y vigilancia automática

**No se ha activado el envío de correo.**

Tampoco se ha creado todavía un proceso programado de vigilancia continua.

La decisión adoptada al cierre es mantener, por ahora, este modelo:

1. se actualizan las fuentes de Empleo mediante los mecanismos de importación existentes;
2. se detectan publicaciones y cambios;
3. el usuario consulta `Mi seguimiento`;
4. al iniciar sesión, se puede avisar de que existen novedades.

El correo automático queda como posible evolución posterior y no forma parte del estado actual.

## 9. Acceso a Empleo

A fecha de esta revisión, Empleo queda accesible a **cualquier usuario autenticado**.

La suscripción comercial de OpoCoach se sigue consultando para conservar el estado comercial disponible, pero ya no condiciona el acceso al módulo de Empleo.

Esta decisión puede revisarse en el futuro sin afectar al diseño del seguimiento de convocatorias.

## 10. Despliegues al cierre

### Backend Empleo

Repositorio `Fragarre/NetReto-Web-Empleo`.

Último commit registrado en `main` al cierre:

`fec384ea74a6c239b9f12384a8526bf682b83bcc` — `Abrir Empleo a usuarios autenticados`

Render: `netreto-empleo-api`.

### Frontend NetExamenes

Repositorio `Fragarre/OpoCoach-Web`.

Último commit registrado en `main` al cierre:

`7e9a53d402e8cd1ef5241be844a68e2cc2880bef` — `Mostrar aviso inmediatamente al iniciar sesión`

## 11. Estado funcional comprobado

Al cierre de esta revisión se comprobó en producción:

- autenticación para acceder a Empleo;
- catálogo global de oportunidades;
- filtrado de procedimientos que no deben aparecer como oportunidades;
- seguimiento de una convocatoria;
- baja del seguimiento;
- listado de convocatorias seguidas;
- enlace a la fuente oficial;
- sección de últimas novedades;
- exclusión de novedades técnicas irrelevantes;
- navegación entre Empleo y Mi seguimiento;
- apertura del módulo de Empleo para usuarios autenticados.

## 12. Pendiente para una siguiente sesión

La siguiente mejora recomendada, antes de incorporar correo o tareas programadas, es trasladar el estado `última_novedad_vista` desde `localStorage` a la base de datos de Empleo.

Después conviene revisar con datos reales durante un periodo de uso si el criterio de "novedad relevante" es suficientemente preciso. Solo después tendría sentido decidir si el correo automático aporta suficiente valor para justificar su complejidad.

## 13. Principio de continuidad

Este documento constituye el punto de partida consolidado para la próxima sesión. No debe asumirse que el correo, un Cron Job o una vigilancia automática estén implementados mientras no se documenten expresamente como completados.
