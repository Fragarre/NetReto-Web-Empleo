-- NetReto Empleo
-- Base de datos independiente de la BD actual de NetReto.

CREATE TABLE organismos (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    tipo TEXT NOT NULL,
    provincia TEXT,
    municipio TEXT,
    activo INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE fuentes (
    id INTEGER PRIMARY KEY,
    organismo_id INTEGER,
    nombre TEXT NOT NULL,
    tipo TEXT NOT NULL,
    url TEXT NOT NULL,
    prioridad INTEGER NOT NULL DEFAULT 100,
    activa INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organismo_id) REFERENCES organismos(id)
);

CREATE TABLE procesos (
    id INTEGER PRIMARY KEY,
    organismo_id INTEGER NOT NULL,
    codigo_externo TEXT,
    identificador_estable TEXT NOT NULL UNIQUE,
    denominacion TEXT NOT NULL,
    cuerpo_escala TEXT,
    grupo TEXT,
    subgrupo TEXT,
    tipo_proceso TEXT,
    sistema_selectivo TEXT,
    turno TEXT,
    plazas INTEGER,
    estado TEXT NOT NULL DEFAULT 'EN_SEGUIMIENTO',
    anio_oep INTEGER,
    anio_convocatoria INTEGER,
    fecha_convocatoria TEXT,
    fecha_apertura TEXT,
    fecha_cierre TEXT,
    fecha_examen TEXT,
    lugar_examen TEXT,
    ultima_publicacion_at TEXT,
    fuente_principal_id INTEGER,
    datos_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organismo_id) REFERENCES organismos(id),
    FOREIGN KEY (fuente_principal_id) REFERENCES fuentes(id)
);

CREATE INDEX idx_procesos_organismo ON procesos(organismo_id);
CREATE INDEX idx_procesos_estado ON procesos(estado);
CREATE INDEX idx_procesos_anio_convocatoria ON procesos(anio_convocatoria);
CREATE INDEX idx_procesos_fecha_cierre ON procesos(fecha_cierre);

CREATE TABLE publicaciones (
    id INTEGER PRIMARY KEY,
    proceso_id INTEGER NOT NULL,
    fuente_id INTEGER NOT NULL,
    referencia TEXT,
    tipo TEXT NOT NULL,
    titulo TEXT,
    fecha_publicacion TEXT,
    url TEXT NOT NULL,
    contenido_hash TEXT,
    contenido_texto TEXT,
    datos_json TEXT,
    detectada_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proceso_id) REFERENCES procesos(id),
    FOREIGN KEY (fuente_id) REFERENCES fuentes(id)
);

CREATE INDEX idx_publicaciones_proceso ON publicaciones(proceso_id);
CREATE INDEX idx_publicaciones_fecha ON publicaciones(fecha_publicacion);
CREATE UNIQUE INDEX uq_publicaciones_fuente_ref_url
    ON publicaciones(fuente_id, referencia, url);

CREATE TABLE cambios (
    id INTEGER PRIMARY KEY,
    proceso_id INTEGER NOT NULL,
    publicacion_id INTEGER,
    tipo TEXT NOT NULL,
    campo TEXT,
    valor_anterior TEXT,
    valor_nuevo TEXT,
    resumen TEXT NOT NULL,
    significativo INTEGER NOT NULL DEFAULT 1,
    detectado_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proceso_id) REFERENCES procesos(id),
    FOREIGN KEY (publicacion_id) REFERENCES publicaciones(id)
);

CREATE INDEX idx_cambios_proceso ON cambios(proceso_id);
CREATE INDEX idx_cambios_significativo ON cambios(significativo);

-- user_id es el UUID de Supabase Auth; no se crea una tabla de usuarios local.
CREATE TABLE suscripciones (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    proceso_id INTEGER NOT NULL,
    activa INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, proceso_id),
    FOREIGN KEY (proceso_id) REFERENCES procesos(id)
);

CREATE INDEX idx_suscripciones_user ON suscripciones(user_id, activa);
CREATE INDEX idx_suscripciones_proceso ON suscripciones(proceso_id, activa);

CREATE TABLE notificaciones (
    id INTEGER PRIMARY KEY,
    suscripcion_id INTEGER NOT NULL,
    cambio_id INTEGER NOT NULL,
    estado TEXT NOT NULL DEFAULT 'PENDIENTE',
    enviado_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(suscripcion_id, cambio_id),
    FOREIGN KEY (suscripcion_id) REFERENCES suscripciones(id),
    FOREIGN KEY (cambio_id) REFERENCES cambios(id)
);

CREATE INDEX idx_notificaciones_pendientes
    ON notificaciones(estado, created_at);
