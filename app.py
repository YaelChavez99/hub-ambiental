"""
Hub de Automatización Ambiental
================================
Aplicación Streamlit multi-herramienta para empresas de remediación de suelos.
Motor cognitivo: Claude Sonnet (Entorno Corporativo Protegido).

Versión: 2.0.0 (Parseo robusto con chunking, limpieza agresiva y max_tokens 8192)
"""

from __future__ import annotations

import base64
import io
import json
import re
import psycopg2
import os
from pathlib import Path
from typing import Any

import anthropic
import fitz  # PyMuPDF
import pandas as pd
import pdfplumber
import streamlit as st
from PIL import Image

# ---------------------------------------------------------------------------
# Configuración de página
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Hub de Automatización Ambiental",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Matriz Oficial NOM-138-SEMARNAT/SSA1-2012 (mg/kg base seca)
# ---------------------------------------------------------------------------
NOM_138_MATRIZ: dict[str, dict[str, float]] = {
    "Agrícola/Forestal": {"HFL": 200.0,  "Benceno": 6.0,  "Tolueno": 40.0,  "Etilbenceno": 10.0,  "Xilenos": 40.0},
    "Residencial":       {"HFL": 1200.0, "Benceno": 6.0,  "Tolueno": 40.0,  "Etilbenceno": 10.0,  "Xilenos": 40.0},
    "Industrial":        {"HFL": 3000.0, "Benceno": 15.0, "Tolueno": 100.0, "Etilbenceno": 50.0,  "Xilenos": 200.0},
}

MAX_CHARS_POR_CHUNK = 80_000  # límite seguro de contexto por llamada a Claude
MAX_PAGINAS_VISION  = 5    # 413 si se envían más de ~5 páginas PNG por llamada
VISION_DPI          = 100  # 100 dpi es suficiente para leer tablas; 150 excede el límite

# ---------------------------------------------------------------------------
# BASE DE DATOS EN LA NUBE (PostgreSQL)
# ---------------------------------------------------------------------------
def obtener_conexion():
    try:
        url = st.secrets["DATABASE_URL"]
        return psycopg2.connect(url)
    except Exception as e:
        st.error(f"❌ Error crítico de conexión a la Base de Datos: {e}")
        st.stop()

def inicializar_db():
    conn = obtener_conexion()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS proyectos (
            id_proyecto TEXT PRIMARY KEY, nombre_siniestro TEXT, uso_de_suelo TEXT,
            estado TEXT, fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS fotos_sistema (
            id_foto SERIAL PRIMARY KEY, id_proyecto TEXT, categoria_ia TEXT,
            pie_de_foto TEXT, nombre_archivo TEXT, foto_b64 TEXT,
            FOREIGN KEY (id_proyecto) REFERENCES proyectos (id_proyecto) ON DELETE CASCADE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS datos_laboratorio (
            id_registro SERIAL PRIMARY KEY, id_proyecto TEXT, id_muestra TEXT,
            zona TEXT, profundidad TEXT, coordenada_x TEXT, coordenada_y TEXT,
            json_resultados TEXT, rebase_nom BOOLEAN,
            FOREIGN KEY (id_proyecto) REFERENCES proyectos (id_proyecto) ON DELETE CASCADE)''')
    conn.commit(); c.close(); conn.close()

inicializar_db()

def obtener_proyectos() -> list[str]:
    try:
        conn = obtener_conexion(); c = conn.cursor()
        c.execute("SELECT id_proyecto, nombre_siniestro FROM proyectos ORDER BY fecha_creacion DESC")
        data = c.fetchall(); c.close(); conn.close()
        return [f"{row[0]}: {row[1]}" for row in data]
    except Exception: return []

def obtener_detalles_proyecto(id_proyecto: str) -> dict | None:
    try:
        conn = obtener_conexion(); c = conn.cursor()
        c.execute("SELECT nombre_siniestro, uso_de_suelo FROM proyectos WHERE id_proyecto = %s", (id_proyecto,))
        row = c.fetchone(); c.close(); conn.close()
        if row: return {"nombre": row[0], "uso_de_suelo": row[1]}
    except Exception: pass
    return None

def crear_proyecto_db(id_proj: str, nombre: str, uso: str) -> bool:
    try:
        conn = obtener_conexion(); c = conn.cursor()
        c.execute("INSERT INTO proyectos (id_proyecto, nombre_siniestro, uso_de_suelo, estado) VALUES (%s,%s,%s,%s)",
                  (id_proj.strip(), nombre.strip(), uso, "Activo"))
        conn.commit(); c.close(); conn.close(); return True
    except Exception: return False

def guardar_foto_db(id_proyecto: str, category: str, pie: str, archivo: str, foto_bytes: bytes):
    try:
        b64_str = base64.b64encode(foto_bytes).decode("utf-8")
        conn = obtener_conexion(); c = conn.cursor()
        c.execute("INSERT INTO fotos_sistema (id_proyecto,categoria_ia,pie_de_foto,nombre_archivo,foto_b64) VALUES (%s,%s,%s,%s,%s)",
                  (id_proyecto, category, pie, archivo, b64_str))
        conn.commit(); c.close(); conn.close()
    except Exception as e: st.error(f"Error al guardar foto: {e}")

def cargar_fotos_proyecto(id_proyecto: str) -> list[dict]:
    try:
        conn = obtener_conexion(); c = conn.cursor()
        c.execute("SELECT id_foto,categoria_ia,pie_de_foto,nombre_archivo,foto_b64 FROM fotos_sistema WHERE id_proyecto=%s ORDER BY id_foto DESC", (id_proyecto,))
        rows = c.fetchall(); c.close(); conn.close()
        return [{"id_foto":r[0],"categoria":r[1],"pie":r[2],"nombre":r[3],"b64":r[4]} for r in rows]
    except Exception: return []

def eliminar_foto_db(id_foto: int) -> bool:
    try:
        conn = obtener_conexion(); c = conn.cursor()
        c.execute("DELETE FROM fotos_sistema WHERE id_foto=%s", (id_foto,))
        conn.commit(); c.close(); conn.close(); return True
    except Exception: return False

def guardar_muestra_db(id_proyecto, id_muestra, zona, prof, x, y, json_res, rebase):
    try:
        conn = obtener_conexion(); c = conn.cursor()
        c.execute("DELETE FROM datos_laboratorio WHERE id_proyecto=%s AND id_muestra=%s", (id_proyecto, id_muestra))
        c.execute("INSERT INTO datos_laboratorio (id_proyecto,id_muestra,zona,profundidad,coordenada_x,coordenada_y,json_resultados,rebase_nom) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                  (id_proyecto, id_muestra, zona, prof, x, y, json_res, rebase))
        conn.commit(); c.close(); conn.close()
    except Exception as e: st.error(f"Error al registrar analítico: {e}")

def cargar_laboratorio_proyecto(id_proyecto: str) -> list[dict]:
    try:
        conn = obtener_conexion(); c = conn.cursor()
        c.execute("SELECT id_muestra,zona,profundidad,coordenada_x,coordenada_y,json_resultados,rebase_nom FROM datos_laboratorio WHERE id_proyecto=%s ORDER BY id_registro ASC", (id_proyecto,))
        rows = c.fetchall(); c.close(); conn.close()
        return [{"id_muestra":r[0],"zona":r[1],"profundidad":r[2],"x":r[3],"y":r[4],"resultados":json.loads(r[5]),"rebase":r[6]} for r in rows]
    except Exception: return []

# ---------------------------------------------------------------------------
# Helpers generales
# ---------------------------------------------------------------------------
def safe_float(val: Any) -> float:
    try:
        if isinstance(val, str): val = val.replace(",","").strip()
        return float(val)
    except Exception: return 0.0

def limpiar_json_response(raw: str) -> str:
    raw = re.sub(r"
http://googleusercontent.com/immersive_entry_chip/0
http://googleusercontent.com/immersive_entry_chip/1
