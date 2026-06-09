"""
Hub de Automatización Ambiental
================================
Aplicación Streamlit multi-herramienta para empresas de remediación de suelos.
Motor cognitivo: Claude Sonnet (Entorno Corporativo Protegido).

Versión: 2.1.0 (Doble Motor: Extracción por Texto fitz + Fallback de Visión Óptica)
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
import fitz  # PyMuPDF - Motor Principal
import pandas as pd
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

NOM_138_MATRIZ: dict[str, dict[str, float]] = {
    "Agrícola/Forestal": {"HFL": 200.0, "Benceno": 6.0, "Tolueno": 40.0, "Etilbenceno": 10.0, "Xilenos": 40.0},
    "Residencial":       {"HFL": 1200.0, "Benceno": 6.0, "Tolueno": 40.0, "Etilbenceno": 10.0, "Xilenos": 40.0},
    "Industrial":        {"HFL": 3000.0, "Benceno": 15.0, "Tolueno": 100.0, "Etilbenceno": 50.0, "Xilenos": 200.0},
}

MAX_CHARS_POR_CHUNK = 80_000

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
        c.execute("INSERT INTO proyectos (id_proyecto, nombre_siniestro, uso_de_suelo, estado) VALUES (%s, %s, %s, %s)", (id_proj.strip(), nombre.strip(), uso, "Activo"))
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception: return False

def guardar_foto_db(id_proyecto: str, category: str, pie: str, archivo: str, foto_bytes: bytes):
    try:
        b64_str = base64.b64encode(foto_bytes).decode("utf-8")
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
            (id_proyecto, id_muestra, zona, prof, x, y, json_res, rebase),
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
# Helpers de Procesamiento e Inferencia Híbrida
# ---------------------------------------------------------------------------
def safe_float(val: Any) -> float:
    try:
        if isinstance(val, str): val = val.replace(",", "").strip()
        return float(val)
    except Exception: return 0.0

def extract_pdf_text_fitz(pdf_bytes: bytes) -> str:
    """Usa PyMuPDF (fitz) para extraer texto digital incrustado de forma veloz."""
    text_parts: list[str] = []
    exclusiones = ["TRACE 1310", "INTENSITY", "RT(MIN)", "TRACEFINDER", "MASS SPECTROMETER", "TIC MS"]
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for i, page in enumerate(doc, start=1):
        text = page.get_text() or ""
        text_upper = text.upper()
        if any(k in text_upper for k in exclusiones): continue
        text_parts.append(f"\n--- PÁGINA {i} ---\n{text}")
    return "\n".join(text_parts)

def limpiar_json_response(raw: str) -> str:
    raw = re.sub(r"```json\s*", "", raw)
    raw = raw.strip().strip("`").strip()
    return raw

def parsear_json_lista(text_content: str) -> list[dict]:
    cleaned = limpiar_json_response(text_content)
    match = re.search(r'\[\s*\{.*\}\s*\]', cleaned, re.DOTALL)
    raw = match.group(0) if match else cleaned
    raw = re.sub(r',\s*([\]}])', r'\1', raw)
    try: return json.loads(raw)
    except json.JSONDecodeError:
        try:
            raw_sanitized = re.sub(r'[\x00-\x1f\x7f]', ' ', raw)
            raw_sanitized = re.sub(r',\s*([\]}])', r'\1', raw_sanitized)
            return json.loads(raw_sanitized)
        except Exception: return []

# ---------------------------------------------------------------------------
# HERRAMIENTA 1: FILTRO DE FOTOGRAFÍAS
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_VISION = """
Eres un auditor ambiental experto. Clasifica la imagen únicamente en una de estas categorías fijas:
"Evidencia del Siniestro", "Excavaciones", "Sondeos y Muestreo", "Evidencias de Remediación", "INSERVIBLE".
Devuelve EXCLUSIVAMENTE un objeto JSON: {"clasificacion": "...", "pie_de_foto": "máx 15 palabras"}
"""

def analizar_fotografia(client: anthropic.Anthropic, image_bytes: bytes, media_type: str) -> dict:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    message = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=512, system=SYSTEM_PROMPT_VISION,
        messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}, {"type": "text", "text": "Clasifica."}]}]
    )
    try: return json.loads(limpiar_json_response(message.content[0].text.strip()))
    except Exception: return {"clasificacion": "Evidencia del Siniestro", "pie_de_foto": "Fotografía de campo."}

def render_herramienta_fotos(client: anthropic.Anthropic) -> None:
    st.header("📷 Herramienta 1 — Filtro y Archivo Organizado de Evidencias")
    if not st.session_state.proyecto_actual: st.warning("⚠️ Selecciona un proyecto en la barra lateral."); return
    with st.expander("➕ Subir Fotografías al Expediente", expanded=True):
        uploaded_files = st.file_uploader("Selecciona imágenes", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True)
        if uploaded_files and st.button("🚀 Procesar Imágenes", type="primary"):
            for uf in uploaded_files:
                raw_bytes = uf.read()
                res = analizar_fotografia(client, raw_bytes, "image/jpeg")
                guardar_foto_db(st.session_state.proyecto_actual, res.get("clasificacion", "Evidencia del Siniestro"), res.get("pie_de_foto", "Fotografía."), uf.name, raw_bytes)
            st.success("¡Fotos guardadas!"); st.rerun()

    fotos = cargar_fotos_proyecto(st.session_state.proyecto_actual)
    for cap in ["Evidencia del Siniestro", "Excavaciones", "Sondeos y Muestreo", "Evidencias de Remediación", "INSERVIBLE"]:
        fc = [f for f in fotos if f["categoria"] == cap]
        with st.expander(f"📂 {cap} ({len(fc)})"):
            for i in range(0, len(fc), 3):
                cols = st.columns(3)
                for col, item in zip(cols, fc[i:i+3]):
                    with col:
                        st.image(base64.b64decode(item["b64"]), use_container_width=True)
                        if st.button("🗑️ Eliminar", key=f"del_{item['id_foto']}", use_container_width=True):
                            if eliminar_foto_db(item["id_foto"]): st.rerun()

# ---------------------------------------------------------------------------
# HERRAMIENTA 3: VACIADO INTELIGENTE (TEXTO + RESPALDO OCR VISUAL)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_LAB = """
Eres un auditor analítico pericial experto en reportes de laboratorios ambientales conforme a la NOM-138-SEMARNAT/SSA1-2012.
Genera la lista de todas las muestras encontradas en este formato de arreglo JSON estructurado:
[
  {
    "id_muestra": "ID de la muestra (ej: P1 0.6)",
    "zona": "Zona (ej: ZONA 1)",
    "profundidad": "Profundidad (ej: 0.60)",
    "coordenada_x": "UTM X (ej: 250037.32)",
    "coordenada_y": "UTM Y (ej: 2420516.68)",
    "HFL": 1876.25, "Benceno": 0.0, "Tolueno": 0.0, "Etilbenceno": 0.0, "Xilenos": 0.0, "pH": 7.87, "Humedad": 16.797
  }
]
Devuelve ÚNICAMENTE el arreglo JSON sin marcas markdown externas.
"""

def _llamar_claude_texto(client: anthropic.Anthropic, texto: str) -> list[dict]:
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=8192, system=SYSTEM_PROMPT_LAB,
            messages=[{"role": "user", "content": f"Vaciado desde texto:\n\n{texto}"}]
        )
        return parsear_json_lista(message.content[0].text.strip())
    except Exception: return []

def _llamar_claude_vision_paginas(client: anthropic.Anthropic, doc: fitz.Document, paginas_indices: list[int]) -> list[dict]:
    """Renderiza las páginas escaneadas a imágenes y las envía directo a Claude para OCR Óptico Nacio."""
    contenido_usuario = [{"type": "text", "text": "Realiza el OCR visual y el vaciado cruzado analítico de estas páginas escaneadas del laboratorio:"}]
    for idx in paginas_indices:
        if idx < 0 or idx >= len(doc): continue
        pix = doc[idx].get_pixmap(dpi=130)
        img_bytes = pix.tobytes("jpeg")
        b64_img = base64.b64encode(img_bytes).decode("utf-8")
        contenido_usuario.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_img}
        })
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=8192, system=SYSTEM_PROMPT_LAB,
            messages=[{"role": "user", "content": contenido_usuario}]
        )
        return parsear_json_lista(message.content[0].text.strip())
    except Exception: return []

def render_herramienta_lab(client: anthropic.Anthropic) -> None:
    st.header("🧪 Herramienta 3 — Vaciado Automático de Laboratorio")
    if not st.session_state.proyecto_actual: st.warning("⚠️ Selecciona un proyecto."); return

    detalles = obtener_detalles_proyecto(st.session_state.proyecto_actual)
    uso_suelo = detalles["uso_de_suelo"] if detalles else "Agrícola/Forestal"
    limites_vigentes = NOM_138_MATRIZ[uso_suelo]

    st.subheader(f"📋 Marco Regulatorio Activo: `NOM-138 ({uso_suelo})`")
    cols_l = st.columns(5)
    for col, (param, val) in zip(cols_l, limites_vigentes.items()):
        col.metric(f"LMP {param}", f"{val} mg/kg")
    st.markdown("---")

    uploaded_pdf = st.file_uploader("Sube el PDF analítico integral de Novalabsa", type=["pdf"])
    if uploaded_pdf and st.button("🔍 Iniciar Extracción Corporativa", type="primary"):
        pdf_bytes = uploaded_pdf.read()
        
        # Intentar primero por Texto Digital Corp con Fitz
        with st.spinner("Intentando lectura digital acelerada..."):
            texto_digital = extract_pdf_text_fitz(pdf_bytes)
            
        if len(texto_digital.strip()) > 200:
            with st.spinner("Procesando estructura digital de celdas..."):
                muestras_extraidas = _llamar_claude_texto(client, texto_digital)
        else:
            # 🧠 MODO ESCANEADO EN ACCIÓN: Si viene vacío, Claude activa sus ojos y lee las imágenes
            st.warning("📸 Detectamos un PDF escaneado (Imagen pura). Activando Motor de Visión Óptica...")
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            total_pags = len(doc)
            
            # Mapeamos los rangos clave de tu reporte de LABSA (Químicos al inicio, Formato de campo al final)
            hojas_quimicas = list(range(2, min(14, total_pags))) 
            hojas_campo = list(range(max(0, total_pags - 12), total_pags))
            paginas_objetivo = hojas_quimicas + hojas_campo
            
            with st.spinner(f"Claude realizando OCR visual sobre las {len(paginas_objetivo)} páginas clave del reporte..."):
                muestras_extraidas = _llamar_claude_vision_paginas(client, doc, paginas_objetivo)

        if not muestras_extraidas:
            st.error("❌ El volumen de celdas no pudo ser estructurado. Verifica que el PDF no esté borroso.")
            return

        st.success(f"✅ {len(muestras_extraidas)} muestras procesadas."); con = obtener_conexion()
        for m in muestras_extraidas:
            try:
                id_orig = str(m.get("id_muestra", "")).strip()
                if not id_orig: continue
                zona = str(m.get("zona", "Campo")).strip()
                profundidad = str(m.get("profundidad", "0.0")).strip()
                x = str(m.get("coordenada_x", "0.0")).strip()
                y = str(m.get("coordenada_y", "0.0")).strip()

                hfl_val = safe_float(m.get("HFL", 0.0))
                b_val, t_val, e_val, x_val = safe_float(m.get("Benceno", 0.0)), safe_float(m.get("Tolueno", 0.0)), safe_float(m.get("Etilbenceno", 0.0)), safe_float(m.get("Xilenos", 0.0))

                rebo = (hfl_val > limites_vigentes["HFL"] or b_val > limites_vigentes["Benceno"] or t_val > limites_vigentes["Tolueno"] or e_val > limites_vigentes["Etilbenceno"] or x_val > limites_vigentes["Xilenos"])
                json_res = json.dumps({"HFL": hfl_val, "Benceno": b_val, "Tolueno": t_val, "Etilbenceno": e_val, "Xilenos": x_val, "pH": safe_float(m.get("pH", 0.0)), "Humedad": safe_float(m.get("Humedad", 0.0))})
                guardar_muestra_db(st.session_state.proyecto_actual, id_orig, zona, profundidad, x, y, json_res, rebase=rebo)
            except Exception: pass
        st.rerun()

    historial = cargar_laboratorio_proyecto(st.session_state.proyecto_actual)
    if historial:
        st.subheader("📊 Historial del Expediente Analítico")
        filas = []
        for h in historial:
            filas.append({
                "Zona Afectada": h["zona"], "Identificación Muestra": h["id_muestra"], "Profundidad (m)": h["profundidad"], "Coordenada X (Este)": h["x"], "Coordenada Y (Norte)": h["y"],
                "HFL": h["resultados"].get("HFL", 0.0), "Benceno": h["resultados"].get("Benceno", 0.0), "Tolueno": h["resultados"].get("Tolueno", 0.0), "Etilbenceno": h["resultados"].get("Etilbenceno", 0.0), "Xilenos": h["resultados"].get("Xilenos", 0.0),
                "pH": h["resultados"].get("pH", 0.0), "Humedad (%)": h["resultados"].get("Humedad", 0.0), "Evaluación NOM-138": "🚨 EXCEDE" if h["rebase"] else "✅ Conforme"
            })
        df = pd.DataFrame(filas)
        st.dataframe(df.style.applymap(lambda v: "background-color: #ffcccc; color: #cc0000; font-weight: bold;" if v == "🚨 EXCEDE" else "", subset=["Evaluación NOM-138"]), use_container_width=True)

# ---------------------------------------------------------------------------
# CORE LOGÍSTICO INTERFAZ
# ---------------------------------------------------------------------------
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
            partes = proyecto_seleccionado.split(":", 1)
            st.session_state.proyecto_actual = partes[0].strip()
            st.session_state.nombre_proyecto = partes[1].strip() if len(partes) > 1 else ""
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
        st.caption("⚙️ Motor: Claude Sonnet 4.6 \n**v1.8.2 (Estable e Híbrido)**")
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
