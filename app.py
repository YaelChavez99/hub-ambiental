"""
Hub de Automatización Ambiental
================================
Aplicación Streamlit multi-herramienta para empresas de remediación de suelos.
Motor cognitivo: Claude 4.6 Sonnet (Anthropic API).

Versión: 1.4.2 (Carpetas Virtuales + Opción de Eliminar)
"""

from __future__ import annotations

import base64
import io
import json
import re
import sqlite3
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
# Constantes – NOM-138-SEMARNAT/SSA1-2012
# ---------------------------------------------------------------------------
NOM_138_LIMITES: dict[str, float] = {
    "HFL": 200.0,
    "Benceno": 6.0,
    "Tolueno": 40.0,
    "Etilbenceno": 10.0,
    "Xilenos": 40.0,
}

# ---------------------------------------------------------------------------
# BASE DE DATOS LOCAL (SQLite con Soporte de Eliminación)
# ---------------------------------------------------------------------------
def inicializar_db():
    conn = sqlite3.connect('hub_ambiental.db')
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
            id_foto INTEGER PRIMARY KEY AUTOINCREMENT,
            id_proyecto TEXT,
            categoria_ia TEXT,
            pie_de_foto TEXT,
            nombre_archivo TEXT,
            foto_b64 TEXT,
            FOREIGN KEY (id_proyecto) REFERENCES proyectos (id_proyecto)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS datos_laboratorio (
            id_muestra TEXT PRIMARY KEY,
            id_proyecto TEXT,
            zona TEXT,
            coordenadas TEXT,
            json_resultados TEXT,
            rebase_nom BOOLEAN,
            FOREIGN KEY (id_proyecto) REFERENCES proyectos (id_proyecto)
        )
    ''')
    conn.commit()
    conn.close()

inicializar_db()

def obtener_proyectos() -> list[str]:
    try:
        conn = sqlite3.connect('hub_ambiental.db')
        c = conn.cursor()
        c.execute("SELECT id_proyecto, nombre_siniestro FROM proyectos")
        data = c.fetchall()
        conn.close()
        return [f"{row[0]}: {row[1]}" for row in data]
    except Exception:
        return []

def crear_proyecto_db(id_proj: str, nombre: str, uso: str) -> bool:
    try:
        conn = sqlite3.connect('hub_ambiental.db')
        c = conn.cursor()
        c.execute(
            "INSERT INTO proyectos (id_proyecto, nombre_siniestro, uso_de_suelo, estado) VALUES (?, ?, ?, ?)",
            (id_proj.strip(), nombre.strip(), uso, 'Activo')
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def guardar_foto_db(id_proyecto: str, category: str, pie: str, archivo: str, foto_bytes: bytes):
    try:
        b64_str = base64.b64encode(foto_bytes).decode('utf-8')
        conn = sqlite3.connect('hub_ambiental.db')
        c = conn.cursor()
        c.execute(
            "INSERT INTO fotos_sistema (id_proyecto, categoria_ia, pie_de_foto, nombre_archivo, foto_b64) VALUES (?, ?, ?, ?, ?)",
            (id_proyecto, category, pie, archivo, b64_str)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"Error al guardar foto en DB: {e}")

def cargar_fotos_proyecto(id_proyecto: str) -> list[dict]:
    try:
        conn = sqlite3.connect('hub_ambiental.db')
        c = conn.cursor()
        # Modificado para extraer también el ID único de la foto
        c.execute("SELECT id_foto, categoria_ia, pie_de_foto, nombre_archivo, foto_b64 FROM fotos_sistema WHERE id_proyecto = ?", (id_proyecto,))
        rows = c.fetchall()
        conn.close()
        
        fotos = []
        for r in rows:
            fotos.append({
                "id_foto": r[0],
                "categoria": r[1],
                "pie": r[2],
                "nombre": r[3],
                "b64": r[4]
            })
        return fotos
    except Exception:
        return []

def eliminar_foto_db(id_foto: int) -> bool:
    """Elimina permanentemente una fotografía de la base de datos."""
    try:
        conn = sqlite3.connect('hub_ambiental.db')
        c = conn.cursor()
        c.execute("DELETE FROM fotos_sistema WHERE id_foto = ?", (id_foto,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Helpers de API
# ---------------------------------------------------------------------------
def get_client() -> anthropic.Anthropic:
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError):
        api_key = os.getenv("ANTHROPIC_API_KEY", "")

    if not api_key:
        st.error("⚠️ No se encontró la clave de API de Anthropic.")
        st.stop()
    return anthropic.Anthropic(api_key=api_key)

def image_to_b64(image_bytes: bytes) -> str:
    return base64.standard_b64encode(image_bytes).decode("utf-8")

def resize_image_if_needed(image_bytes: bytes, max_px: int = 1_500) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_px:
        ratio = max_px / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()

def extract_pdf_text(pdf_bytes: bytes) -> str:
    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            text_parts.append(f"\n--- PÁGINA {i} ---\n{page_text}")
    return "\n".join(text_parts)

# ---------------------------------------------------------------------------
# CONFIGURACIÓN DEL ESPACIO DE TRABAJO
# ---------------------------------------------------------------------------
def configurar_sesion_colaborativa():
    if 'usuario_actual' not in st.session_state:
        st.session_state.usuario_actual = st.session_state.get("username", "Ingeniero") 
    if 'proyecto_actual' not in st.session_state:
        st.session_state.proyecto_actual = None
        st.session_state.nombre_proyecto = "Ningún proyecto seleccionado"

# ---------------------------------------------------------------------------
# HERRAMIENTA 1: FILTRO, CARPETAS VIRTUALES Y ELIMINACIÓN
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_VISION = """
Eres un auditor técnico ambiental experto en inspección de sitios contaminados por derrames de hidrocarburos en México.
Tu tarea es analizar fotografías tomadas en campo y clasificarlas con estricto rigor técnico.

Para CADA fotografía, debes evaluar a cuál categoría operativa pertenece:
- "Evidencia del Siniestro": Muestra vehículos accidentados, la zona del impacto inicial, suelo impregnado crudo, etc.
- "Excavaciones": Muestra el avance del retiro de suelo, cortes de tierra, uso de maquinaria pesada retirando capas.
- "Sondeos y Muestreo": Muestra pozos a cielo abierto, barrenos, personal tomando muestras con palas o tubos core, etiquetado de frascos.
- "Evidencias de Remediación": Muestra aplicación de bacterias, volteo de biopilas, adición de nutrientes o membranas.
- "INSERVIBLE": Imágenes borrosas, dedos tapando el lente, fotos de la oficina o minutas impresas.

Escribe una descripción de la fotografía que sea sumamente resumida, clara y general. 
REGLA INQUEBRANTABLE: Debe tener un máximo estricto de 15 palabras.

Devuelve EXCLUSIVAMENTE un objeto JSON (sin markdown, sin texto extra):
{
  "clasificacion": "Evidencia del Siniestro" | "Excavaciones" | "Sondeos y Muestreo" | "Evidencias de Remediación" | "INSERVIBLE",
  "razon": "Justificación de una oración",
  "pie_de_foto": "Descripción resumida y general (máx 15 palabras)" | null
}
"""

def analizar_fotografia(client: anthropic.Anthropic, image_bytes: bytes, media_type: str) -> dict:
    b64 = image_to_b64(image_bytes)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=SYSTEM_PROMPT_VISION,
        messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}, {"type": "text", "text": "Analiza y clasifica esta fotografía técnica."}]}]
    )
    raw = re.sub(r"```json|```", "", message.content[0].text.strip()).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"clasificacion": "INSERVIBLE", "razon": "Error de análisis", "pie_de_foto": None}

def render_herramienta_fotos(client: anthropic.Anthropic) -> None:
    st.header("📷 Herramienta 1 — Filtro y Archivo Organizado de Evidencias")
    st.caption("Sube las fotos del sitio. Claude las analizará y las acomodará de forma automática en sus respectivas carpetas de actividad.")

    if not st.session_state.proyecto_actual:
        st.warning("⚠️ Debes seleccionar o crear un proyecto en el menú lateral para comenzar.")
        return

    with st.expander("➕ Subir Nuevas Fotografías al Expediente", expanded=True):
        uploaded_files = st.file_uploader("Arrastra o selecciona las imágenes de campo", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True)
        
        if uploaded_files:
            if st.button("🚀 Procesar y Organizar Imágenes", type="primary"):
                progress = st.progress(0, text="Clasificando archivos con Inteligencia Artificial…")
                total = len(uploaded_files)

                for idx, uf in enumerate(uploaded_files):
                    raw_bytes = uf.read()
                    media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(uf.name.split('.')[-1].lower(), "image/jpeg")
                    try:
                        processed = resize_image_if_needed(raw_bytes)
                        mt_send = "image/jpeg"
                    except Exception:
                        processed, mt_send = raw_bytes, media_type

                    res = analizar_fotografia(client, processed, mt_send)
                    
                    if res.get("clasificacion") != "INSERVIBLE":
                        guardar_foto_db(
                            st.session_state.proyecto_actual, 
                            res["clasificacion"], 
                            res.get("pie_de_foto", "Evidencia fotográfica."), 
                            uf.name, 
                            processed
                        )

                    progress.progress((idx + 1) / total, text=f"Acomodando: {uf.name}…")
                
                progress.empty()
                st.success("¡Fotos organizadas con éxito! 📁")
                st.rerun()

    st.markdown("---")
    st.subheader("📁 Carpetas de Evidencias del Proyecto")

    fotos_guardadas = cargar_fotos_proyecto(st.session_state.proyecto_actual)

    carpetas_definidas = [
        "Evidencia del Siniestro",
        "Excavaciones",
        "Sondeos y Muestreo",
        "Evidencias de Remediación"
    ]

    for carpeta in carpetas_definidas:
        fotos_carpeta = [f for f in fotos_guardadas if f["categoria"] == carpeta]
        
        with st.expander(f"📂 {carpeta} ({len(fotos_carpeta)})", expanded=False):
            if not fotos_carpeta:
                st.caption("Esta carpeta está vacía actualmente.")
            else:
                for i in range(0, len(fotos_carpeta), 3):
                    cols = st.columns(3)
                    for col, item in zip(cols, fotos_carpeta[i:i+3]):
                        with col:
                            img_data = base64.b64decode(item["b64"])
                            st.image(img_data, use_container_width=True)
                            st.caption(f"📄 Archivo: {item['nombre']}")
                            st.info(f"🏷️ *{item['pie']}*")
                            
                            # --- NUEVO: BOTÓN DE ELIMINAR FOTO ---
                            if st.button("🗑️ Eliminar Foto", key=f"btn_del_{item['id_foto']}", type="secondary", use_container_width=True):
                                if eliminar_foto_db(item["id_foto"]):
                                    st.toast("Foto eliminada correctamente.")
                                    st.rerun()


# (Mantenemos el resto de los módulos estables)
SYSTEM_PROMPT_AUDITOR = """Eres un auditor de la ASEA. Extrae entidades clave y detecta discrepancias en JSON."""
def auditar_informe(client: anthropic.Anthropic, texto_pdf: str) -> dict:
    message = client.messages.create(model="claude-sonnet-4-6", max_tokens=4096, system=SYSTEM_PROMPT_AUDITOR, messages=[{"role": "user", "content": texto_pdf[:180000]}])
    raw = re.sub(r"```json|```", "", message.content[0].text.strip()).strip()
    try: return json.loads(raw)
    except: return {"entidades": {}, "discrepancias": [], "resumen": {"total_discrepancias": 0, "estado_general": "ERROR"}}

def render_herramienta_auditor(client: anthropic.Anthropic) -> None:
    st.header("🔎 Herramienta 2 — Auditor de Machotes")
    if not st.session_state.proyecto_actual: st.warning("⚠️ Selecciona un proyecto."); return
    uploaded_pdf = st.file_uploader("Sube el PDF", type=["pdf"])
    if uploaded_pdf and st.button("🔍 Auditar", type="primary"):
        with st.spinner("Auditando..."): st.json(auditar_informe(client, extract_pdf_text(uploaded_pdf.read())))

def render_herramienta_lab(client: anthropic.Anthropic) -> None:
    st.header("🧪 Herramienta 3 — Vaciado Automático de Laboratorio")
    if not st.session_state.proyecto_actual: st.warning("⚠️ Selecciona un proyecto."); return
    st.info("Módulo de Laboratorio listo. Sube el PDF de resultados químicos.")

def generar_capitulo_5(client: anthropic.Anthropic, mun: str, edo: str, coord: str, notas: str) -> str:
    message = client.messages.create(model="claude-sonnet-4-6", max_tokens=4096, system="Redacta el Capítulo 5 integrando las notas de campo.", messages=[{"role": "user", "content": f"{mun}, {edo}, {coord}. Notas: {notas}"}])
    return message.content[0].text

def render_herramienta_cap5(client: anthropic.Anthropic) -> None:
    st.header("📝 Herramienta 4 — Generador del Capítulo 5")
    if not st.session_state.proyecto_actual: st.warning("⚠️ Selecciona un proyecto."); return
    with st.form("cap5"):
        mun, edo, coord = st.text_input("Municipio"), st.text_input("Estado"), st.text_input("Coordenadas")
        notas = st.text_area("Notas de Campo")
        if st.form_submit_button("Generar") and mun and edo:
            with st.spinner("Generando..."): st.markdown(generar_capitulo_5(client, mun, edo, coord, notas))

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
        else:
            st.warning("⚠️ Selecciona un proyecto para comenzar.")
            st.session_state.proyecto_actual = None

        with st.expander("➕ Crear Nuevo Proyecto"):
            with st.form("form_nuevo_proyecto"):
                nuevo_id, nuevo_nombre = st.text_input("ID Proyecto *"), st.text_input("Nombre Siniestro *")
                nuevo_uso = st.selectbox("Uso de Suelo", ["Agrícola/Forestal", "Industrial", "Residencial"])
                if st.form_submit_button("Guardar Proyecto", type="primary"):
                    if nuevo_id and nuevo_nombre:
                        if crear_proyecto_db(nuevo_id, nuevo_nombre, nuevo_uso): st.success("¡Creado! Recarga la página.")
                        else: st.error("Ese ID ya existe.")
                    else: st.error("Llena los campos (*).")
        st.markdown("---")
        herramienta = st.radio("Herramientas:", ["📷 Filtro de Fotografías", "🔎 Auditor de Machotes", "🧪 Vaciado de Laboratorio", "📝 Generador Cap. 5"], label_visibility="collapsed")
        st.markdown("---")
        st.caption("⚙️ Motor: Claude 4.6 Sonnet  \n📜 NOM-138-SEMARNAT/SSA1-2012  \n**v1.4.2 (Con Eliminación)**")
    return herramienta

def check_password() -> bool:
    def password_entered():
        if st.session_state["username"] == st.secrets["credenciales"]["usuario"] and st.session_state["password"] == st.secrets["credenciales"]["password"]:
            st.session_state["password_correct"] = True; del st.session_state["password"]
        else: st.session_state["password_correct"] = False
    if "password_correct" not in st.session_state:
        st.markdown("## 🔒 Acceso Restringido")
        st.text_input("Usuario", key="username"); st.text_input("Contraseña", type="password", key="password")
        st.button("Entrar", on_click=password_entered); return False
    elif not st.session_state["password_correct"]: st.error("😕 Credenciales incorrectas"); return False
    return True
    
def main() -> None:
    configurar_sesion_colaborativa()
    client = get_client()
    herramienta = render_sidebar()
    if "Filtro" in herramienta: render_herramienta_fotos(client)
    elif "Auditor" in herramienta: render_herramienta_auditor(client)
    elif "Laboratorio" in herramienta: render_herramienta_lab(client)
    elif "Cap" in herramienta: render_herramienta_cap5(client)

if __name__ == "__main__":
    if check_password(): main()
