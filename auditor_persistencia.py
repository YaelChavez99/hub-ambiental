"""
Módulo: auditor_persistencia.py
Caché de auditorías por hash de documento (Fase 1).

Responsabilidad única: evitar reprocesar el mismo PDF dos veces. Si un
consultor vuelve a abrir un proyecto o quiere regenerar el Word/TXT del
reporte, no debe volver a pagar las llamadas a Claude de Pass 1 — el hash
del PDF es la clave de caché.

No depende de app.py — se importa como módulo.
Compatible con: Python 3.10+, psycopg2>=2.9, streamlit>=1.35
"""

from __future__ import annotations

import json

import psycopg2
import streamlit as st


# ---------------------------------------------------------------------------
# Conexión BD (mismo patrón que gestor_proyectos.py / conagua_ref.py)
# ---------------------------------------------------------------------------

def _conn() -> psycopg2.extensions.connection:
    try:
        return psycopg2.connect(st.secrets["DATABASE_URL"])
    except (KeyError, FileNotFoundError) as exc:
        raise RuntimeError("DATABASE_URL no encontrada en secrets.toml") from exc


# ---------------------------------------------------------------------------
# Migración — idempotente, segura en cada arranque
# ---------------------------------------------------------------------------

def migrar_bd_auditor() -> bool:
    """Crea las tablas de caché de extracción y reportes del auditor."""
    try:
        conn = _conn()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS auditoria_documentos (
                hash_documento   TEXT PRIMARY KEY,
                id_proyecto      TEXT NOT NULL REFERENCES proyectos(id_proyecto) ON DELETE CASCADE,
                nombre_archivo   TEXT,
                total_paginas    INTEGER,
                hechos_json      TEXT,
                fecha_procesado  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS auditoria_reportes (
                id_reporte       SERIAL PRIMARY KEY,
                hash_documento   TEXT NOT NULL REFERENCES auditoria_documentos(hash_documento) ON DELETE CASCADE,
                id_proyecto      TEXT NOT NULL,
                reporte_json     TEXT NOT NULL,
                fecha_generado   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception as exc:
        st.session_state["_auditor_persistencia_error"] = str(exc)
        return False


# ---------------------------------------------------------------------------
# Caché de hechos extraídos (Pass 1)
# ---------------------------------------------------------------------------

def obtener_hechos_cache(hash_documento: str) -> list[dict] | None:
    """Devuelve los hechos de Pass 1 ya extraídos para este PDF exacto,
    o None si nunca se procesó (o si hubo error de conexión)."""
    try:
        conn = _conn()
        c = conn.cursor()
        c.execute(
            "SELECT hechos_json FROM auditoria_documentos WHERE hash_documento = %s",
            (hash_documento,),
        )
        row = c.fetchone()
        c.close()
        conn.close()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def guardar_hechos_cache(
    hash_documento: str,
    id_proyecto: str,
    nombre_archivo: str,
    total_paginas: int,
    hechos: list[dict],
) -> None:
    """Guarda el resultado de Pass 1 para no repetir las llamadas a Claude
    si se vuelve a procesar exactamente el mismo PDF."""
    try:
        conn = _conn()
        c = conn.cursor()
        c.execute(
            """INSERT INTO auditoria_documentos
               (hash_documento, id_proyecto, nombre_archivo, total_paginas, hechos_json)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (hash_documento) DO UPDATE SET
                 hechos_json     = EXCLUDED.hechos_json,
                 total_paginas   = EXCLUDED.total_paginas,
                 fecha_procesado = CURRENT_TIMESTAMP""",
            (
                hash_documento, id_proyecto, nombre_archivo, total_paginas,
                json.dumps(hechos, ensure_ascii=False),
            ),
        )
        conn.commit()
        c.close()
        conn.close()
    except Exception as exc:
        st.warning(f"⚠️ No se pudo guardar la caché de extracción: {exc}")


# ---------------------------------------------------------------------------
# Reportes de auditoría completos (para reexportar sin reprocesar)
# ---------------------------------------------------------------------------

def guardar_reporte(hash_documento: str, id_proyecto: str, reporte: dict) -> None:
    """Guarda el reporte de auditoría completo (con ICTI y hallazgos) ligado
    al hash del documento que lo originó."""
    try:
        conn = _conn()
        c = conn.cursor()
        c.execute(
            """INSERT INTO auditoria_reportes (hash_documento, id_proyecto, reporte_json)
               VALUES (%s, %s, %s)""",
            (hash_documento, id_proyecto, json.dumps(reporte, ensure_ascii=False)),
        )
        conn.commit()
        c.close()
        conn.close()
    except Exception as exc:
        st.warning(f"⚠️ No se pudo guardar el reporte de auditoría: {exc}")


def obtener_ultimo_reporte(hash_documento: str) -> dict | None:
    """Recupera el reporte de auditoría más reciente para este documento."""
    try:
        conn = _conn()
        c = conn.cursor()
        c.execute(
            """SELECT reporte_json FROM auditoria_reportes
               WHERE hash_documento = %s
               ORDER BY fecha_generado DESC LIMIT 1""",
            (hash_documento,),
        )
        row = c.fetchone()
        c.close()
        conn.close()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return None
