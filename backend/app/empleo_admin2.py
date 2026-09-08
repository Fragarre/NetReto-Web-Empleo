from __future__ import annotations

from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from .database import get_connection

REVISION_ESTADOS = ("PENDIENTE_REVISION", "PUBLICADA", "DESCARTADA")
TEMARIO_ESTADOS = ("PENDIENTE_REVISION", "VERIFICADO", "DESCARTADO")
TEMARIO_ORIGENES = ("AUTOMATICO", "MANUAL")


def crear_proceso_manual(datos: dict[str, Any]) -> dict[str, Any]:
    denominacion = str(datos.get("denominacion") or "").strip()
    organismo_id = datos.get("organismo_id")
    if not denominacion or organismo_id is None:
        raise ValueError("Organismo y denominación son obligatorios")
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM organismos WHERE id=%s AND activo=TRUE", (organismo_id,))
        if cursor.fetchone() is None:
            raise ValueError("Organismo no encontrado o inactivo")
        cursor.execute("""
            INSERT INTO procesos(
              organismo_id,codigo_externo,identificador_estable,denominacion,cuerpo_escala,
              grupo,subgrupo,tipo_proceso,sistema_selectivo,turno,plazas,estado,anio_oep,
              anio_convocatoria,fecha_convocatoria,fecha_apertura,fecha_cierre,fecha_examen,
              lugar_examen,fuente_principal_id,datos_json,es_oportunidad,origen_dato,
              revision_estado,observaciones_internas,updated_at
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'MANUAL','PENDIENTE_REVISION',%s,NOW())
            RETURNING *
        """,(
            organismo_id,str(datos.get("codigo_externo") or "").strip() or None,f"MAN:{organismo_id}:{uuid4()}",
            denominacion,datos.get("cuerpo_escala"),datos.get("grupo"),datos.get("subgrupo"),
            datos.get("tipo_proceso"),datos.get("sistema_selectivo"),datos.get("turno"),datos.get("plazas"),
            datos.get("estado") or "EN_CURSO",datos.get("anio_oep"),datos.get("anio_convocatoria"),
            datos.get("fecha_convocatoria"),datos.get("fecha_apertura"),datos.get("fecha_cierre"),
            datos.get("fecha_examen"),datos.get("lugar_examen"),datos.get("fuente_principal_id"),
            Jsonb({"url_oficial":datos.get("url_oficial"),"observaciones":datos.get("observaciones")}),
            bool(datos.get("es_oportunidad",True)),datos.get("observaciones")))
        return dict(cursor.fetchone())


def guardar_temas(proceso_id:int, temas:list[dict[str,Any]]) -> dict[str,Any]:
    if not temas: raise ValueError("Debe existir al menos un tema")
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM temarios_empleo WHERE proceso_id=%s",(proceso_id,))
        row=cursor.fetchone()
        if row is None: raise ValueError("Primero debe existir el temario")
        tid=row["id"]
        cursor.execute("DELETE FROM temas_empleo WHERE temario_id=%s",(tid,))
        for i,tema in enumerate(temas,1):
            contenido=str(tema.get("contenido_texto") or "").strip()
            if not contenido: raise ValueError(f"El tema {i} no puede estar vacío")
            cursor.execute("INSERT INTO temas_empleo(temario_id,numero,titulo,contenido_texto,orden) VALUES(%s,%s,%s,%s,%s)",(tid,tema.get("numero"),tema.get("titulo"),contenido,i))
        cursor.execute("SELECT * FROM temarios_empleo WHERE id=%s",(tid,))
        resultado=dict(cursor.fetchone())
        cursor.execute("SELECT id,numero,titulo,contenido_texto,orden FROM temas_empleo WHERE temario_id=%s ORDER BY orden",(tid,))
        resultado["temas"]=[dict(r) for r in cursor.fetchall()]
        return resultado
