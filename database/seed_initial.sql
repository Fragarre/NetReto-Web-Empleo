-- Datos iniciales del catálogo de fuentes de empleo público.
-- Idempotente: puede ejecutarse más de una vez sin duplicar estos registros.
-- No crea procesos; solo organismos y fuentes oficiales.

INSERT INTO organismos (nombre, tipo, provincia, municipio)
SELECT 'Generalitat Valenciana', 'ADMINISTRACION_AUTONOMICA', NULL, NULL
WHERE NOT EXISTS (
    SELECT 1 FROM organismos WHERE nombre = 'Generalitat Valenciana'
);

INSERT INTO organismos (nombre, tipo, provincia, municipio)
SELECT 'Diputación Provincial de Valencia', 'DIPUTACION', 'Valencia', 'València'
WHERE NOT EXISTS (
    SELECT 1 FROM organismos WHERE nombre = 'Diputación Provincial de Valencia'
);

INSERT INTO organismos (nombre, tipo, provincia, municipio)
SELECT 'Ayuntamiento de València', 'AYUNTAMIENTO', 'Valencia', 'València'
WHERE NOT EXISTS (
    SELECT 1 FROM organismos WHERE nombre = 'Ayuntamiento de València'
);

INSERT INTO organismos (nombre, tipo, provincia, municipio)
SELECT 'Diputación Provincial de Alicante', 'DIPUTACION', 'Alicante', 'Alicante'
WHERE NOT EXISTS (
    SELECT 1 FROM organismos WHERE nombre = 'Diputación Provincial de Alicante'
);

INSERT INTO organismos (nombre, tipo, provincia, municipio)
SELECT 'Ayuntamiento de Alicante', 'AYUNTAMIENTO', 'Alicante', 'Alicante'
WHERE NOT EXISTS (
    SELECT 1 FROM organismos WHERE nombre = 'Ayuntamiento de Alicante'
);

INSERT INTO organismos (nombre, tipo, provincia, municipio)
SELECT 'Diputación Provincial de Castellón', 'DIPUTACION', 'Castellón', 'Castelló de la Plana'
WHERE NOT EXISTS (
    SELECT 1 FROM organismos WHERE nombre = 'Diputación Provincial de Castellón'
);

-- Fuentes primarias o de seguimiento directo de cada organismo.
INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT o.id, 'Buscador de empleo público - Generalitat Valenciana', 'SEDE', 'https://sede.gva.es/es/cercador-ocupacio-publica', 10
FROM organismos o
WHERE o.nombre = 'Generalitat Valenciana'
  AND NOT EXISTS (
      SELECT 1 FROM fuentes f
      WHERE f.organismo_id = o.id
        AND f.url = 'https://sede.gva.es/es/cercador-ocupacio-publica'
  );

INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT o.id, 'Boletín Oficial de la Provincia de Valencia', 'BOP', 'https://bop.dival.es/bop', 20
FROM organismos o
WHERE o.nombre = 'Diputación Provincial de Valencia'
  AND NOT EXISTS (
      SELECT 1 FROM fuentes f
      WHERE f.organismo_id = o.id
        AND f.url = 'https://bop.dival.es/bop'
  );

INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT o.id, 'Sede electrónica - Ayuntamiento de València', 'SEDE', 'https://sede.valencia.es/', 10
FROM organismos o
WHERE o.nombre = 'Ayuntamiento de València'
  AND NOT EXISTS (
      SELECT 1 FROM fuentes f
      WHERE f.organismo_id = o.id
        AND f.url = 'https://sede.valencia.es/'
  );

INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT o.id, 'Oferta de empleo público y procesos selectivos - Diputación de Alicante', 'TRANSPARENCIA', 'https://abierta.diputacionalicante.es/informacion-institucional-y-organizativa/oferta-de-empleo-publico-procesos-selectivos/', 10
FROM organismos o
WHERE o.nombre = 'Diputación Provincial de Alicante'
  AND NOT EXISTS (
      SELECT 1 FROM fuentes f
      WHERE f.organismo_id = o.id
        AND f.url = 'https://abierta.diputacionalicante.es/informacion-institucional-y-organizativa/oferta-de-empleo-publico-procesos-selectivos/'
  );

INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT o.id, 'Convocatorias de oposiciones - Ayuntamiento de Alicante', 'PORTAL', 'https://w3.alicante.es/rrhh/oposiciones/', 10
FROM organismos o
WHERE o.nombre = 'Ayuntamiento de Alicante'
  AND NOT EXISTS (
      SELECT 1 FROM fuentes f
      WHERE f.organismo_id = o.id
        AND f.url = 'https://w3.alicante.es/rrhh/oposiciones/'
  );

INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT o.id, 'Recursos humanos - Diputación de Castellón', 'PORTAL', 'https://www.dipcas.es/es/recursos-humanos.html', 10
FROM organismos o
WHERE o.nombre = 'Diputación Provincial de Castellón'
  AND NOT EXISTS (
      SELECT 1 FROM fuentes f
      WHERE f.organismo_id = o.id
        AND f.url = 'https://www.dipcas.es/es/recursos-humanos.html'
  );

-- Fuentes oficiales transversales para verificación y descubrimiento.
INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT NULL, 'Boletín Oficial de la Provincia de Alicante', 'BOP', 'https://diputacionalicante.es/boletin-oficial-de-la-provincia/', 30
WHERE NOT EXISTS (
    SELECT 1 FROM fuentes
    WHERE organismo_id IS NULL
      AND url = 'https://diputacionalicante.es/boletin-oficial-de-la-provincia/'
);

INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT NULL, 'Boletín Oficial de la Provincia de Castellón', 'BOP', 'https://www.dipcas.es/es/bop.html', 30
WHERE NOT EXISTS (
    SELECT 1 FROM fuentes
    WHERE organismo_id IS NULL
      AND url = 'https://www.dipcas.es/es/bop.html'
);

INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT NULL, 'Boletín Oficial del Estado', 'BOE', 'https://www.boe.es/', 40
WHERE NOT EXISTS (
    SELECT 1 FROM fuentes
    WHERE organismo_id IS NULL
      AND url = 'https://www.boe.es/'
);
