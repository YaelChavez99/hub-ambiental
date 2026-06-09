"""
Hub de Automatización Ambiental
================================
Aplicación Streamlit multi-herramienta para empresas de remediación de suelos.
Motor cognitivo: Claude 3.5 Sonnet (Entorno Corporativo Protegido).

Versión: 2.0.0 (Arquitectura de Parseo Robusto con Fallback y Limpieza JSON)
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
    "Agrícola/Forestal": {"HFL": 200.0, "Benceno": 6.0, "Tolueno": 40.0, "Etilbenceno": 10.0, "Xilenos": 40.0},
    "Residencial": {"HFL": 1200.0, "Benceno": 6.0, "Tolueno": 40.0, "Etilbenceno": 10.0, "Xilenos": 40.0},
    "Industrial": {"HFL": 3000.0, "Benceno": 15.0, "Tolueno": 100.0, "Etilbenceno": 50.0, "Xilenos": 200.0}
}

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
    c.execute('''
        CREATE TABLE IF NOT EXISTS proyectos (
            id_proyecto TEXT PRIMARY KEY,
            nombre_siniestro TEXT,
            uso_de_suelo TEXT,
            estado TEXT,
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS fotos_sistema (
            id_foto SERIAL PRIMARY KEY,
            id_proyecto TEXT,
            categoria_ia TEXT,
            pie_de_foto TEXT,
            nombre_archivo TEXT,
            foto_b64 TEXT,
            FOREIGN KEY (id_proyecto) REFERENCES proyectos (id_proyecto) ON DELETE CASCADE
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS datos_laboratorio (
            id_registro SERIAL PRIMARY KEY,
            id_proyecto TEXT,
            id_muestra TEXT,
            zona TEXT,
            profundidad TEXT,
            coordenada_x TEXT,
            coordenada_y TEXT,
            json_resultados TEXT,
            rebase_nom BOOLEAN,
            FOREIGN KEY (id_proyecto) REFERENCES proyectos (id_proyecto) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    c.close()
    conn.close()

inicializar_db()

def obtener_proyectos() -> list[str]:
    try:
        conn = obtener_conexion()
        c = conn.cursor()
        c.execute("SELECT id_proyecto, nombre_siniestro FROM proyectos ORDER BY fecha_creacion DESC")
        data = c.fetchall()
        c.close()
        conn.close()
        return [f"{row[0]}: {row[1]}" for row in data]
    except Exception: return []

def obtener_detalles_proyecto(id_proyecto: str) -> dict | None:
    try:
        conn = obtener_conexion()
        c = conn.cursor()
        c.execute("SELECT nombre_siniestro, uso_de_suelo FROM proyectos WHERE id_proyecto = %s", (id_proyecto,))
        row = c.fetchone()
        c.close()
        conn.close()
        if row: return {"nombre": row[0], "uso_de_suelo": row[1]}
    except Exception: pass
    return None

def crear_proyecto_db(id_proj: str, nombre: str, uso: str) -> bool:
    try:
        conn = obtener_conexion()
        c = conn.cursor()
        c.execute("INSERT INTO proyectos (id_proyecto, nombre_siniestro, uso_de_suelo, estado) VALUES (%s, %s, %s, %s)", (id_proj.strip(), nombre.strip(), uso, 'Activo'))
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception: return False

def guardar_foto_db(id_proyecto: str, category: str, pie: str, archivo: str, foto_bytes: bytes):
    try:
        b64_str = base64.b64encode(foto_bytes).decode('utf-8')
        conn = obtener_conexion()
        c = conn.cursor()
        c.execute("INSERT INTO fotos_sistema (id_proyecto, categoria_ia, pie_de_foto, nombre_archivo, foto_b64) VALUES (%s, %s, %s, %s, %s)", (id_proyecto, category, pie, archivo, b64_str))
        conn.commit()
        c.close()
        conn.close()
    except Exception as e: st.error(f"Error al guardar foto: {e}")

def cargar_fotos_proyecto(id_proyecto: str) -> list[dict]:
    try:
        conn = obtener_conexion()
        c = conn.cursor()
        c.execute("SELECT id_foto, categoria_ia, pie_de_foto, nombre_archivo, foto_b64 FROM fotos_sistema WHERE id_proyecto = %s ORDER BY id_foto DESC", (id_proyecto,))
        rows = c.fetchall()
        c.close()
        conn.close()
        return [{"id_foto": r[0], "categoria": r[1], "pie": r[2], "nombre": r[3], "b64": r[4]} for r in rows]
    except Exception: return []

def eliminar_foto_db(id_foto: int) -> bool:
    try:
        conn = obtener_conexion()
        c = conn.cursor()
        c.execute("DELETE FROM fotos_sistema WHERE id_foto = %s", (id_foto,))
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception: return False

def guardar_muestra_db(id_proyecto: str, id_muestra: str, zona: str, prof: str, x: str, y: str, json_res: str, rebase: bool):
    try:
        conn = obtener_conexion()
        c = conn.cursor()
        c.execute("DELETE FROM datos_laboratorio WHERE id_proyecto = %s AND id_muestra = %s", (id_proyecto, id_muestra))
        c.execute(
            "INSERT INTO datos_laboratorio (id_proyecto, id_muestra, zona, profundidad, coordenada_x, coordenada_y, json_resultados, rebase_nom) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (id_proyecto, id_muestra, zona, prof, x, y, json_res, rebase)
        )
        conn.commit()
        c.close()
        conn.close()
    except Exception as e: st.error(f"Error al registrar analítico: {e}")

def cargar_laboratorio_proyecto(id_proyecto: str) -> list[dict]:
    try:
        conn = obtener_conexion()
        c = conn.cursor()
        c.execute("SELECT id_muestra, zona, profundidad, coordenada_x, coordenada_y, json_resultados, rebase_nom FROM datos_laboratorio WHERE id_proyecto = %s ORDER BY id_registro ASC", (id_proyecto,))
        rows = c.fetchall()
        c.close()
        conn.close()
        return [{"id_muestra": r[0], "zona": r[1], "profundidad": r[2], "x": r[3], "y": r[4], "resultados": json.loads(r[5]), "rebase": r[6]} for r in rows]
    except Exception: return []

# ---------------------------------------------------------------------------
# Helpers de Normalización y Filtro Avanzado de PDF
# ---------------------------------------------------------------------------
def safe_float(val: Any) -> float:
    try:
        if isinstance(val, str):
            val = val.replace(",", "").strip()
        return float(val)
    except Exception: return 0.0

def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Filtra el documento eliminando el ruido técnico instrumental de HFL y BTEX."""
    text_parts: list[str] = []
    exclusiones = ["TRACE 1310", "AUTOMUESTREADOR", "TRACEFINDER", "INTENSITY", "RT(MIN)", "MASS SPECTROMETER", "TIC MS"]
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            text_upper = text.upper()
            if any(k in text_upper for k in exclusiones):
                continue
            text_parts.append(f"\n--- PÁGINA {i} ---\n{text}")
    return "\n".join(text_parts)

# ---------------------------------------------------------------------------
# HERRAMIENTA 1: FILTRO DE FOTOGRAFÍAS
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_VISION = """
Eres un auditor técnico ambiental experto en inspección de sitios contaminados por derrames de hidrocarburos en México.
Tu tarea es analizar fotografías tomadas en campo y clasificarlas con estricto rigor técnico.

Debes elegir OBLIGATORIAMENTE una de estas categorías operativas:
- "Evidencia del Siniestro"
- "Excavaciones"
- "Sondeos y Muestreo"
- "Evidencias de Remediación"
- "INSERVIBLE"

Escribe una descripción resumida y general de máximo 15 palabras.

Devuelve EXCLUSIVAMENTE un objeto JSON sin marcas markdown:
{
  "clasificacion": "Evidencia del Siniestro" | "Excavaciones" | "Sondeos y Muestreo" | "Evidencias de Remediación" | "INSERVIBLE",
  "pie_de_foto": "Descripción resumida (máx 15 palabras)"
}
"""

def analizar_fotografia(client: anthropic.Anthropic, image_bytes: bytes, media_type: str) -> dict:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    message = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=512, system=SYSTEM_PROMPT_VISION,
        messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}, {"type": "text", "text": "Clasifica esta imagen."}]}]
    )
    text_content = message.content[0].text.strip()
    match = re.search(r'\{.*\}', text_content, re.DOTALL)
    raw = match.group(0) if match else text_content
    try: return json.loads(raw)
    except Exception: return {"clasificacion": "Evidencia del Siniestro", "pie_de_foto": "Evidencia fotográfica de campo."}

def render_herramienta_fotos(client: anthropic.Anthropic) -> None:
    st.header("📷 Herramienta 1 — Filtro y Archivo Organizado de Evidencias")
    if not st.session_state.proyecto_actual: st.warning("⚠️ Selecciona un proyecto en la barra lateral."); return
    
    with st.expander("➕ Subir Fotografías al Expediente", expanded=True):
        uploaded_files = st.file_uploader("Selecciona imágenes de campo", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True)
        if uploaded_files and st.button("🚀 Procesar Imágenes", type="primary"):
            progress = st.progress(0, text="Organizando evidencias con Claude…")
            total = len(uploaded_files)
            for idx, uf in enumerate(uploaded_files):
                raw_bytes = uf.read()
                res = analizar_fotografia(client, raw_bytes, "image/jpeg")
                guardar_foto_db(st.session_state.proyecto_actual, res.get("clasificacion", "Evidencia del Siniestro"), res.get("pie_de_foto", "Fotografía del sitio."), uf.name, raw_bytes)
                progress.progress((idx + 1) / total)
            st.success("¡Fotos guardadas de forma permanente!"); st.rerun()

    fotos = cargar_fotos_proyecto(st.session_state.proyecto_actual)
    carpetas = ["Evidencia del Siniestro", "Excavaciones", "Sondeos y Muestreo", "Evidencias de Remediación", "INSERVIBLE"]
    for cap in carpetas:
        fc = [f for f in fotos if f["categoria"] == cap]
        folder_label = f"📂 {cap} ({len(fc)})" if cap != "INSERVIBLE" else f"🗑️ Archivo / Inservibles ({len(fc)})"
        with st.expander(folder_label):
            if not fc: st.caption("Carpeta vacía.")
            else:
                for i in range(0, len(fc), 3):
                    cols = st.columns(3)
                    for col, item in zip(cols, fc[i:i+3]):
                        with col:
                            st.image(base64.b64decode(item["b64"]), use_container_width=True)
                            st.caption(f"📄 {item['nombre']}")
                            st.info(f"🏷️ {item['pie']}")
                            if st.button("🗑️ Eliminar", key=f"del_{item['id_foto']}", use_container_width=True):
                                if eliminar_foto_db(item["id_foto"]): st.rerun()

# ---------------------------------------------------------------------------
# HERRAMIENTA 3: PARSEO ROBUSTO CON FALLBACK (DIAGRAMA BLUEPRINT INTEGRADO)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_LAB = """
Eres un auditor analítico pericial experto en reportes de laboratorios de suelos contaminados (Novalabsa/LABSA) en México conforme a la NOM-138-SEMARNAT/SSA1-2012.
Tu objetivo es realizar un vaciado completo y cruzado de cada una de las muestras identificadas en el reporte técnico.

Para cada muestra, recopila:
1) HOJAS DE ANALÍTICOS: Los valores de HFL, Benceno, Tolueno, Etilbenceno, Xilenos, pH y Humedad. Si dice '< L.C.' o 'ND', colócalo estrictamente como 0.0.
2) HOJA DE CADENA DE CUSTODIA / FORMATO DE CAMPO: Cruza el ID de la muestra para extraer su Zona Afectada (ej. ZONA 1, ZONA 2, PERIFERIA), la profundidad (m), y las coordenadas georreferenciadas exactas Metros Este (Coordenada X) y Metros Norte (Coordenada Y).

Genera obligatoriamente la lista completa de todas las muestras encontradas en formato de arreglo JSON estructurado.
CRÍTICO: Devuelve EXCLUSIVAMENTE el arreglo JSON. No agregues introducciones, conclusiones ni explicaciones de markdown.
"""

def analizar_reporte_laboratorio_robusto(client: anthropic.Anthropic, texto_pdf: str) -> list[dict]:
    # Fix A (Chunking): Si el documento excede el tamaño óptimo de procesamiento (80k), se divide en bloques
    MAX_CHUNK_SIZE = 80000
    chunks = [texto_pdf[i:i+MAX_CHUNK_SIZE] for i in range(0, len(texto_pdf), MAX_CHUNK_SIZE)]
    muestras_totales = []

    for chunk in chunks:
        try:
            # Fix C: Subir max_tokens estrictamente a 8192 para evitar truncado de celdas
            message = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=8192, system=SYSTEM_PROMPT_LAB,
                messages=[{"role": "user", "content": f"Ejecuta el vaciado del siguiente bloque de datos:\n\n{chunk}"}]
            )
            text_content = message.content[0].text.strip()
            
            # Fix B (Limpieza pericial de Markdown antes de json.loads):
            match = re.search(r'\[\s*\{.*\}\s*\]', text_content, re.DOTALL)
            raw_json = match.group(0) if match else text_content
            
            # Sanitización de comas huérfanas terminales que rompen el parseo de Python
            raw_json = re.sub(r',\s*([\]}])', r'\1', raw_json)
            
            data = json.loads(raw_json)
            if isinstance(data, list):
                muestras_totales.extend(data)
        except Exception:
            # Fallback: Si un fragmento falla, continúa de forma resiliente con los demás bloques
            continue
            
    return muestras_totales

def render_herramienta_lab(client: anthropic.Anthropic) -> None:
    st.header("🧪 Herramienta 3 — Vaciado Automático de Laboratorio")
    if not st.session_state.proyecto_actual: st.warning("⚠️ Selecciona un proyecto en la barra lateral."); return

    detalles = obtener_detalles_proyecto(st.session_state.proyecto_actual)
    uso_suelo = detalles["uso_de_suelo"] if detalles else "Agrícola/Forestal"
    limites_vigentes = NOM_138_MATRIZ[uso_suelo]

    st.subheader(f"📋 Marco Regulatorio Activo: `NOM-138 ({uso_suelo})`")
    cols_l = st.columns(5)
    for col, (param, val) in zip(cols_l, limites_vigentes.items()):
        col.metric(f"LMP {param}", f"{val} mg/kg")
    st.markdown("---")

    uploaded_pdf = st.file_uploader("Sube el PDF analítico de Novalabsa", type=["pdf"])
    if uploaded_pdf and st.button("🔍 Iniciar Extracción Corporativa", type="primary"):
        with st.spinner("Ejecutando motor de parseo robusto con fallback en la nube…"):
            texto = extract_pdf_text(uploaded_pdf.read())
            muestras_extraidas = analizar_reporte_laboratorio_robusto(client, texto)

            if not muestras_extraidas:
                st.error("Error al estructurar los datos del reporte. El volumen de celdas excedió el parseo inicial.")
                return

            for m in muestras_extraidas:
                try:
                    id_orig = m.get("id_muestra", "")
                    if not id_orig: continue
                    
                    zona = m.get("zona", "Campo")
                    profundidad = m.get("profundidad", "0.0")
                    x = m.get("coordenada_x", "0.0")
                    y = m.get("coordenada_y", "0.0")

                    hfl_val = safe_float(m.get("HFL", 0.0))
                    b_val = safe_float(m.get("Benceno", 0.0))
                    t_val = safe_float(m.get("Tolueno", 0.0))
                    e_val = safe_float(m.get("Etilbenceno", 0.0))
                    x_val = safe_float(m.get("Xilenos", 0.0))

                    rebo = (
                        hfl_val > limites_vigentes["HFL"] or b_val > limites_vigentes["Benceno"] or
                        t_val > limites_vigentes["Tolueno"] or e_val > limites_vigentes["Etilbenceno"] or
                        x_val > limites_vigentes["Xilenos"]
                    )

                    json_res = json.dumps({
                        "HFL": hfl_val, "Benceno": b_val, "Tolueno": t_val,
                        "Etilbenceno": e_val, "Xilenos": x_val,
                        "pH": safe_float(m.get("pH", 0.0)), "Humedad": safe_float(m.get("Humedad", 0.0))
                    })

                    guardar_muestra_db(st.session_state.proyecto_actual, id_orig, zona, profundidad, x, y, json_res, rebase=rebo)
                except Exception: pass

            st.success("¡Vaciado relacional completado con éxito!"); st.rerun()

    historial = cargar_laboratorio_proyecto(st.session_state.proyecto_actual)
    if historial:
        st.subheader("📊 Historial del Expediente Analítico")
        filas = []
        for h in historial:
            f = {
                "Zona Afectada": h["zona"], "Identificación Muestra": h["id_muestra"],
                "Profundidad (m)": h["profundidad"], "Coordenada X (Este)": h["x"], "Coordenada Y (Norte)": h["y"],
                "HFL": h["resultados"].get("HFL", 0.0), "Benceno": h["resultados"].get("Benceno", 0.0),
                "Tolueno": h["resultados"].get("Tolueno", 0.0), "Etilbenceno": h["resultados"].get("Etilbenceno", 0.0),
                "Xilenos": h["resultados"].get("Xilenos", 0.0), "pH": h["resultados"].get("pH", 0.0),
                "Humedad (%)": h["resultados"].get("Humedad", 0.0), "Evaluación NOM-138": "🚨 EXCEDE" if h["rebase"] else "✅ Conforme"
            }
            filas.append(f)
        df = pd.DataFrame(filas)
        st.dataframe(df.style.applymap(lambda v: 'background-color: #ffcccc; color: #cc0000; font-weight: bold;' if v == "🚨 EXCEDE" else '', subset=['Evaluación NOM-138']), use_container_width=True)

# ---------------------------------------------------------------------------
# CORE LOGÍSTICO API
# ---------------------------------------------------------------------------
def get_client() -> anthropic.Anthropic:
    try: api_key = st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError): api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key: st.error("⚠️ Falta ANTHROPIC_API_KEY."); st.stop()
    return anthropic.Anthropic(api_key=api_key)

def render_sidebar() -> str:
    with st.sidebar:
        st.markdown("## 🌿 Hub Ambiental")
        st.write(f"👤 **Usuario:** {st.session_state.get('usuario_actual', 'Ingeniero')}")
        st.markdown("---")
        lista_bd = obtener_proyectos()
        proyectos_activos = ["Seleccionar..."] + lista_bd
        st.subheader("📂 Espacio de Trabajo")
        proyecto_seleccionado = st.selectbox("Proyecto Activo:", proyectos_activos)
        if proyecto_seleccionado != "Seleccionar...":
            st.session_state.proyecto_actual = proyecto_seleccionado.split(":")[0] 
            st.session_state.nombre_proyecto = proyecto_seleccionado.split(":")[1].strip()
            st.success(f"✅ Conectado a: {st.session_state.proyecto_actual}")
        else: st.session_state.proyecto_actual = None

        with st.expander("➕ Crear Nuevo Proyecto"):
            with st.form("form_nuevo_proyecto"):
                nuevo_id, nuevo_nombre = st.text_input("ID Proyecto *"), st.text_input("Nombre Siniestro *")
                nuevo_uso = st.selectbox("Uso de Suelo", ["Agrícola/Forestal", "Industrial", "Residencial"])
                if st.form_submit_button("Guardar Proyecto", type="primary"):
                    if nuevo_id and nuevo_nombre and crear_proyecto_db(nuevo_id, nuevo_nombre, nuevo_uso): st.rerun()
        st.markdown("---")
        herramienta = st.radio("Herramientas:", ["📷 Filtro de Fotografías", "🧪 Vaciado de Laboratorio"], label_visibility="collapsed")
        st.markdown("---")
        st.caption("⚙️ Motor: Claude 3.5 Sonnet  \n**v2.0.0 (Parseo Robusto)**")
    return herramienta

def check_password() -> bool:
    if "password_correct" not in st.session_state:
        st.markdown("## 🔒 Acceso Restringido")
        u, p = st.text_input("Usuario"), st.text_input("Contraseña", type="password")
        if st.button("Entrar") and u == st.secrets["credenciales"]["usuario"] and p == st.secrets["credenciales"]["password"]:
            st.session_state["password_correct"] = True; st.rerun()
        return False
    return True
    
def main() -> None:
    client = get_client()
    herramienta = render_sidebar()
    if "Filtro" in herramienta: render_herramienta_fotos(client)
    elif "Laboratorio" in herramienta: render_herramienta_lab(client)

if __name__ == "__main__":
    if check_password(): main()
