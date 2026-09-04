from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

GVA_SEARCH_URL = "https://sede.gva.es/es/cercador-ocupacio-publica"
GVA_BASE_URL = "https://sede.gva.es"
GVA_ORGANISMO_ID = 1
GVA_FUENTE_ID = 1

# En esta primera versión nos centramos en procesos que pueden interesar a una
# persona que busca acceso o bolsas de empleo. Los concursos de traslados,
# RPT y libre designación se dejan fuera deliberadamente.
TIPOS_INCLUIDOS = {
    "oposicion",
    "bolsa de trabajo",
    "promocion interna",
    "contratacion laboral temporal",
    "contratacion laboral indefinida",
    "proceso de estabilizacion",
    "acte unic telematic",
    "acto unico telematico",
    "anuncio dificil cobertura",
}


def _normalizar(texto: str) -> str:
    return " ".join(texto.replace("\xa0", " ").split())


def _sin_acentos(texto: str) -> str:
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )


def _fecha(valor: str | None) -> date | None:
    if not valor:
        return None
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", valor)
    if not m:
        return None
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))


def _anio(texto: str) -> int | None:
    # Prioriza formatos de convocatoria habituales: 1/26, 11/25, 2026.
    for patron in (r"convocatoria\s+\d+[/-](\d{2})", r"convocatoria\s+\d{1,3}/(\d{4})"):
        m = re.search(patron, _sin_acentos(texto))
        if m:
            valor = int(m.group(1))
            return 2000 + valor if valor < 100 else valor
    m = re.search(r"\b(2026|2027)\b", texto)
    return int(m.group(1)) if m else None


def _extraer_campo(texto: str, etiqueta: str, siguiente: str) -> str | None:
    patron = rf"{re.escape(etiqueta)}\s*(.*?)(?=\s+{re.escape(siguiente)}\s*|$)"
    m = re.search(patron, texto, re.IGNORECASE)
    return _normalizar(m.group(1)) if m else None


def _tipo_proceso(texto: str) -> str | None:
    m = re.search(r"Proceso selectivo\s*:\s*([^\n]+?)(?=\s+Tipo de prueba\s*:|\s+Plazas\s+\d|$)", texto, re.I)
    return _normalizar(m.group(1)) if m else None


def _plazas(texto: str) -> int | None:
    m = re.search(r"(?:N[uú]m\. de plazas totales|Plazas)\s*([\d.]+)", texto, re.I)
    if not m:
        return None
    try:
        return int(m.group(1).replace(".", ""))
    except ValueError:
        return None


def _grupo(texto: str) -> str | None:
    m = re.search(r"\bGrupo\s+([A-Z0-9/]+)", texto, re.I)
    return m.group(1).upper() if m else None


def _turno(texto: str) -> str | None:
    normal = _sin_acentos(texto)
    if "promocion interna" in normal:
        return "PROMOCION_INTERNA"
    if "turno libre" in normal:
        return "TURNO_LIBRE"
    if "discapacidad intelectual" in normal:
        return "DISCAPACIDAD_INTELECTUAL"
    if "discapacidad" in normal:
        return "DISCAPACIDAD"
    return None


def _estado(texto: str) -> str:
    normal = _sin_acentos(texto)
    if "plazo abierto" in normal or "abierto" in normal:
        return "ABIERTO"
    if "pendiente" in normal:
        return "PENDIENTE"
    if "cerrado" in normal or "tancat" in normal:
        return "CERRADO"
    return "EN_SEGUIMIENTO"


def _fecha_publicacion(texto: str) -> date | None:
    # En la página de detalle, la primera publicación relevante suele aparecer
    # junto a la fase actual. Si no se localiza, se deja NULL.
    m = re.search(r"Publicaci[oó]n\s+(?:DOGV[^\n]*?|Web)?\s*(?:n[uú]m\.[^\n]*?\s+)?(?:de\s+)?(\d{2}-\d{2}-\d{4})", texto, re.I)
    return _fecha(m.group(1)) if m else None


def parsear_detalle(url: str, html: str, id_emp: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    texto = _normalizar(soup.get_text(" ", strip=True))

    titulo = None
    h1 = soup.find("h1")
    if h1:
        titulo = _normalizar(h1.get_text(" ", strip=True))
    if not titulo:
        titulo = _normalizar(texto.split("Informació bàsica", 1)[0])

    tipo = _tipo_proceso(texto)
    grupo = _grupo(texto)
    plazas = _plazas(texto)
    anio_convocatoria = _anio(titulo or texto)

    # El plazo de la etapa actual se captura de forma conservadora.
    m_plazo = re.search(
        r"(?:Plazo|Termini)\s+(?:de la etapa actual\s*)?(?:Desde|Des de)\s+(\d{2}-\d{2}-\d{4})\s+(?:hasta|a)\s+(\d{2}-\d{2}-\d{4})",
        texto,
        re.I,
    )
    fecha_apertura = _fecha(m_plazo.group(1)) if m_plazo else None
    fecha_cierre = _fecha(m_plazo.group(2)) if m_plazo else None

    m_codigo = re.search(r"C[oó]digo GVA\s*:?\s*(\d+)", texto, re.I)
    codigo_gva = m_codigo.group(1) if m_codigo else str(id_emp)

    m_sia = re.search(r"C[oó]digo SIA\s*:?\s*([0-9]+)", texto, re.I)
    codigo_sia = m_sia.group(1) if m_sia else None

    m_org = re.search(
        r"(?:Informaci[oó]n b[aá]sica|Información básica).*?(?:Convocatoria|Convocat[oò]ria).*?"
        r"(?:Oposici[oó]n|Borsa de treball|Bolsa de trabajo|Promoci[oó]n interna|"
        r"Contrataci[oó]n laboral|Proc[eé]s de estabilitzaci[oó]n|Proceso de estabilizaci[oó]n)",
        texto,
        re.I,
    )
    # La denominación del organismo está inmediatamente después del título en
    # las páginas actuales; se recupera de los elementos h1 y texto posterior.
    organismo = None
    if h1:
        siguiente = h1.find_next(string=True)
        # No dependemos de este dato para el FK; se conserva en datos_json.
        if siguiente:
            organismo = _normalizar(str(siguiente))
    if not organismo:
        m = re.search(
            r"(Conselleria[^|]+?|Labora[^|]+?|Turisme Comunitat Valenciana|"
            r"Conselleria de Sanitat|Conselleria d'Educaci[oó]n[^|]+?|"
            r"Ag[eè]ncia[^|]+?|Institut [^|]+?)\s+Etapa actual",
            texto,
            re.I,
        )
        organismo = _normalizar(m.group(1)) if m else None

    fecha_pub = _fecha_publicacion(texto)
    hash_contenido = hashlib.sha256(html.encode("utf-8")).hexdigest()

    return {
        "codigo_externo": codigo_gva,
        "identificador_estable": f"GVA:{id_emp}",
        "denominacion": titulo or f"Proceso GVA {id_emp}",
        "grupo": grupo,
        "tipo_proceso": tipo,
        "turno": _turno(titulo or texto),
        "plazas": plazas,
        "estado": _estado(texto),
        "anio_convocatoria": anio_convocatoria,
        "fecha_apertura": fecha_apertura,
        "fecha_cierre": fecha_cierre,
        "ultima_publicacion_at": datetime.combine(
            fecha_pub, datetime.min.time(), tzinfo=timezone.utc
        ) if fecha_pub else None,
        "datos_json": {
            "id_emp": id_emp,
            "codigo_gva": codigo_gva,
            "codigo_sia": codigo_sia,
            "organismo_detectado": organismo,
            "url_detalle": url,
        },
        "publicacion": {
            "referencia": f"GVA:{id_emp}:{hash_contenido}",
            "tipo": "DETALLE",
            "titulo": titulo,
            "fecha_publicacion": fecha_pub,
            "url": url,
            "contenido_hash": hash_contenido,
            "contenido_texto": texto,
            "datos_json": {"id_emp": id_emp, "codigo_gva": codigo_gva},
        },
    }


def descubrir_detalles(client: httpx.Client, max_paginas: int = 3) -> list[tuple[int, str]]:
    encontrados: dict[int, str] = {}
    for pagina in range(1, max_paginas + 1):
        params = {
            "pagina": pagina,
            "tipoOrganismo": "1",
            "plazos": "A",
            "tamanyoPagina": "30",
        }
        respuesta = client.get(GVA_SEARCH_URL, params=params)
        respuesta.raise_for_status()
        soup = BeautifulSoup(respuesta.text, "html.parser")
        for enlace in soup.select('a[href*="detall-ocupacio-publica"]'):
            href = enlace.get("href")
            if not href:
                continue
            match = re.search(r"id_emp=(\d+)", href)
            if not match:
                continue
            id_emp = int(match.group(1))
            url = urljoin(GVA_BASE_URL, href)
            encontrados[id_emp] = url
    return sorted(encontrados.items())


def importar_gva(*, max_paginas: int = 3, max_detalles: int | None = None) -> dict[str, int]:
    from .database import get_connection

    estadisticas = {"descubiertos": 0, "procesos": 0, "publicaciones": 0, "cambios": 0}

    headers = {
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        detalles = descubrir_detalles(client, max_paginas=max_paginas)
        if max_detalles is not None:
            detalles = detalles[:max_detalles]
        estadisticas["descubiertos"] = len(detalles)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                for id_emp, url in detalles:
                    respuesta = client.get(url)
                    respuesta.raise_for_status()
                    proceso = parsear_detalle(url, respuesta.text, id_emp)

                    tipo_normalizado = _sin_acentos(proceso.get("tipo_proceso") or "")
                    if not any(t in tipo_normalizado for t in TIPOS_INCLUIDOS):
                        continue

                    cursor.execute(
                        "SELECT id, denominacion, grupo, tipo_proceso, turno, plazas, estado, anio_convocatoria, fecha_apertura, fecha_cierre FROM procesos WHERE identificador_estable = %s",
                        (proceso["identificador_estable"],),
                    )
                    existente = cursor.fetchone()

                    campos = [
                        "denominacion", "grupo", "tipo_proceso", "turno", "plazas",
                        "estado", "anio_convocatoria", "fecha_apertura", "fecha_cierre",
                    ]
                    valores = tuple(proceso.get(c) for c in campos)
                    proceso_id = None

                    if existente:
                        proceso_id = existente[0]
                        for indice, campo in enumerate(campos, start=1):
                            anterior = existente[indice]
                            nuevo = valores[indice - 1]
                            if anterior != nuevo:
                                cursor.execute(
                                    "INSERT INTO cambios (proceso_id, tipo, campo, valor_anterior, valor_nuevo, resumen) VALUES (%s, %s, %s, %s, %s, %s)",
                                    (
                                        proceso_id,
                                        "ACTUALIZACION",
                                        campo,
                                        str(anterior) if anterior is not None else None,
                                        str(nuevo) if nuevo is not None else None,
                                        f"Cambio en {campo}: {anterior!r} -> {nuevo!r}",
                                    ),
                                )
                                estadisticas["cambios"] += 1

                        cursor.execute(
                            """UPDATE procesos SET codigo_externo=%s, denominacion=%s, grupo=%s,
                               tipo_proceso=%s, turno=%s, plazas=%s, estado=%s,
                               anio_convocatoria=%s, fecha_apertura=%s, fecha_cierre=%s,
                               ultima_publicacion_at=COALESCE(%s, ultima_publicacion_at),
                               fuente_principal_id=%s, datos_json=%s, updated_at=NOW()
                               WHERE id=%s""",
                            (
                                proceso["codigo_externo"], proceso["denominacion"], proceso["grupo"],
                                proceso["tipo_proceso"], proceso["turno"], proceso["plazas"],
                                proceso["estado"], proceso["anio_convocatoria"], proceso["fecha_apertura"],
                                proceso["fecha_cierre"], proceso["ultima_publicacion_at"],
                                GVA_FUENTE_ID, proceso["datos_json"], proceso_id,
                            ),
                        )
                    else:
                        cursor.execute(
                            """INSERT INTO procesos
                               (organismo_id, codigo_externo, identificador_estable, denominacion,
                                grupo, tipo_proceso, turno, plazas, estado, anio_convocatoria,
                                fecha_apertura, fecha_cierre, ultima_publicacion_at,
                                fuente_principal_id, datos_json)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               RETURNING id""",
                            (
                                GVA_ORGANISMO_ID, proceso["codigo_externo"], proceso["identificador_estable"],
                                proceso["denominacion"], proceso["grupo"], proceso["tipo_proceso"],
                                proceso["turno"], proceso["plazas"], proceso["estado"],
                                proceso["anio_convocatoria"], proceso["fecha_apertura"], proceso["fecha_cierre"],
                                proceso["ultima_publicacion_at"], GVA_FUENTE_ID, proceso["datos_json"],
                            ),
                        )
                        proceso_id = cursor.fetchone()[0]
                    estadisticas["procesos"] += 1

                    publicacion = proceso["publicacion"]
                    cursor.execute(
                        "SELECT 1 FROM publicaciones WHERE proceso_id=%s AND contenido_hash=%s LIMIT 1",
                        (proceso_id, publicacion["contenido_hash"]),
                    )
                    if cursor.fetchone() is None:
                        cursor.execute(
                            """INSERT INTO publicaciones
                               (proceso_id, fuente_id, referencia, tipo, titulo,
                                fecha_publicacion, url, contenido_hash, contenido_texto, datos_json)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (
                                proceso_id, GVA_FUENTE_ID, publicacion["referencia"], publicacion["tipo"],
                                publicacion["titulo"], publicacion["fecha_publicacion"], publicacion["url"],
                                publicacion["contenido_hash"], publicacion["contenido_texto"],
                                publicacion["datos_json"],
                            ),
                        )
                        estadisticas["publicaciones"] += 1

            connection.commit()

    return estadisticas
