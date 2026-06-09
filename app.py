"""
Hub de Automatización Ambiental
================================
Aplicación Streamlit multi-herramienta para empresas de remediación de suelos.
Motor cognitivo: Claude Sonnet (Entorno Corporativo Protegido).

Versión: 2.1.0 (Soporte PDFs escaneados via Vision + Parseo robusto con chunking)
"""

from __future__ import annotations

import base64
import io
import json
import re
import psycopg2
import os
from typing import Any

import anthropic
import fitz  # PyMuPDF
import pandas as pd
import pdfplumber
import streamlit as st

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

MAX_CHARS_POR_CHUNK = 80_000
MAX_PAGINAS_VISION  = 20
VISION_DPI          = 150

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
    raw = re.sub(r"```(?:json)?\s*", "", raw)
    return raw.strip().strip("`").strip()

def parsear_json_lista(text_content: str) -> list[dict]:
    cleaned = limpiar_json_response(text_content)
    match = re.search(r'\[\s*\{.*\}\s*\]', cleaned, re.DOTALL)
    raw = match.group(0) if match else cleaned
    raw = re.sub(r',\s*([\]}])', r'\1', raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raw2 = re.sub(r'[\x00-\x1f\x7f]', ' ', raw)
        raw2 = re.sub(r',\s*([\]}])', r'\1', raw2)
        try:
            return json.loads(raw2)
        except json.JSONDecodeError as e:
            st.warning(f"⚠️ No se pudo parsear el JSON. Error: {e}\n\nPrimeros 800 chars:\n```\n{raw[:800]}\n```")
            return []

# ---------------------------------------------------------------------------
# Extracción de texto del PDF
# ---------------------------------------------------------------------------
def extract_pdf_text(pdf_bytes: bytes) -> str:
    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            text_upper = text.upper()
            if ("TRACE 1310" in text_upper or "INTENSITY" in text_upper
                    or "RT(MIN)" in text_upper or "TRACEFINDER" in text_upper):
                continue
            text_parts.append(f"\n--- PÁGINA {i} ---\n{text}")
    return "\n".join(text_parts)

# ---------------------------------------------------------------------------
# Renderizado de PDF a imágenes para modo Vision
# ---------------------------------------------------------------------------
def pdf_a_imagenes_b64(pdf_bytes: bytes, dpi: int = VISION_DPI) -> list[str]:
    """Renderiza cada página del PDF como PNG base64. Excluye cromatogramas por proporción."""
    imagenes: list[str] = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    for page in doc:
        rect = page.rect
        ratio = rect.height / max(rect.width, 1)
        if ratio > 3.5:  # página tipo cromatograma (muy alta y angosta)
            continue
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB)
        png_bytes = pix.tobytes("png")
        imagenes.append(base64.b64encode(png_bytes).decode("utf-8"))
    doc.close()
    return imagenes

# ---------------------------------------------------------------------------
# SYSTEM PROMPTS
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_VISION_FOTO = """
Eres un auditor técnico ambiental experto en inspección de sitios contaminados por derrames de hidrocarburos en México.
Analiza fotografías de campo y clasifícalas con rigor técnico.

Elige OBLIGATORIAMENTE una de estas categorías:
- "Evidencia del Siniestro"
- "Excavaciones"
- "Sondeos y Muestreo"
- "Evidencias de Remediación"
- "INSERVIBLE"

Descripción resumida de máximo 15 palabras.

Devuelve EXCLUSIVAMENTE un JSON sin markdown:
{"clasificacion": "...", "pie_de_foto": "..."}
"""

SYSTEM_PROMPT_LAB = """
Eres un auditor analítico pericial experto en reportes de laboratorios de suelos contaminados (Novalabsa/LABSA) en México conforme a la NOM-138-SEMARNAT/SSA1-2012.
Realiza un vaciado completo y cruzado de cada muestra identificada en el reporte.

Para cada muestra recopila:
1) ANALÍTICOS: HFL, Benceno, Tolueno, Etilbenceno, Xilenos, pH y Humedad. Si dice '< L.C.' o 'ND' o celda vacía → 0.0.
2) CADENA DE CUSTODIA: Zona Afectada, profundidad (m), Coordenada X (Metros Este), Coordenada Y (Metros Norte).

IMPORTANTE: Devuelve ÚNICAMENTE el arreglo JSON, sin texto adicional, sin markdown.

Formato requerido:
[
  {
    "id_muestra": "P1 0.6",
    "zona": "ZONA 1",
    "profundidad": "0.60",
    "coordenada_x": "250037.32",
    "coordenada_y": "2420516.68",
    "HFL": 1876.25,
    "Benceno": 0.0,
    "Tolueno": 0.0,
    "Etilbenceno": 0.0,
    "Xilenos": 0.0,
    "pH": 7.87,
    "Humedad": 16.797
  }
]
"""

# ---------------------------------------------------------------------------
# Llamadas a Claude para laboratorio
# ---------------------------------------------------------------------------
def _llamar_claude_lab_texto(client: anthropic.Anthropic, texto: str) -> list[dict]:
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=8192, system=SYSTEM_PROMPT_LAB,
            messages=[{"role": "user", "content": f"Efectúa el vaciado cruzado analítico:\n\n{texto}"}],
        )
        if message.stop_reason == "max_tokens":
            st.warning("⚠️ Respuesta truncada por límite de tokens en este bloque.")
        return parsear_json_lista(message.content[0].text.strip())
    except Exception as e:
        st.error(f"Error Claude (texto): {e}"); return []


def _llamar_claude_lab_vision(client: anthropic.Anthropic, imagenes_b64: list[str]) -> list[dict]:
    """Envía páginas del PDF como imágenes PNG para que Claude las lea visualmente."""
    if not imagenes_b64:
        return []
    content: list[dict] = []
    for b64 in imagenes_b64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}})
    content.append({"type": "text", "text": (
        "Estas imágenes son páginas de un reporte analítico de laboratorio de suelos.\n"
        "Efectúa el vaciado cruzado de TODAS las muestras en las tablas.\n"
        "Si el valor dice '< L.C.' o 'ND' → 0.0.\n"
        "Devuelve ÚNICAMENTE el arreglo JSON sin ningún texto adicional."
    )})
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=8192, system=SYSTEM_PROMPT_LAB,
            messages=[{"role": "user", "content": content}],
        )
        if message.stop_reason == "max_tokens":
            st.warning("⚠️ Respuesta truncada por límite de tokens en este lote de imágenes.")
        return parsear_json_lista(message.content[0].text.strip())
    except Exception as e:
        st.error(f"Error Claude (visión): {e}"); return []


def analizar_reporte_laboratorio(client: anthropic.Anthropic, pdf_bytes: bytes) -> list[dict]:
    """
    Estrategia adaptativa:
    - Si el PDF tiene texto extraíble → modo texto con chunking automático.
    - Si el PDF es escaneado (sin texto) → modo visión: renderiza páginas como imágenes PNG.
    Deduplica por id_muestra en ambos casos.
    """
    texto = extract_pdf_text(pdf_bytes)
    chars_utiles = len(texto.replace(" ","").replace("\n","").replace("-",""))
    st.caption(f"📊 Caracteres útiles extraídos del PDF: {chars_utiles:,}")

    todas: list[dict] = []
    ids_vistos: set[str] = set()

    def acumular(muestras: list[dict]):
        for m in muestras:
            id_m = str(m.get("id_muestra","")).strip()
            if id_m and id_m not in ids_vistos:
                todas.append(m); ids_vistos.add(id_m)

    def texto_tiene_datos_analiticos(t: str) -> bool:
        """
        Verifica que el texto extraído contenga realmente datos analíticos.
        Un PDF de 10MB con solo 679 chars es escaneado aunque técnicamente tenga 'texto'.
        Busca patrones típicos de reportes: IDs de muestra (P1, P2...), valores numéricos
        decimales, o palabras clave de cabecera de tabla.
        """
        t_upper = t.upper()
        # Palabras clave que aparecen en tablas analíticas reales
        keywords = ["HFL", "BENCENO", "TOLUENO", "PROFUNDIDAD", "COORDENADA",
                    "ZONA", "L.C.", "MG/KG", "MUESTRA"]
        hits = sum(1 for kw in keywords if kw in t_upper)
        # Patrones numéricos de coordenadas o valores analíticos (ej: 250,037.32 o 1876.25)
        num_matches = len(re.findall(r'\d{3,}[\.,]\d{2,}', t))
        # Necesitamos al menos 3 keywords Y al menos 5 números para considerar texto válido
        return hits >= 3 and num_matches >= 5

    # ── MODO TEXTO ──────────────────────────────────────────────────────────
    if chars_utiles > 500 and texto_tiene_datos_analiticos(texto):
        st.info("📄 Modo texto: PDF con capa de texto detectada.")
        if len(texto) <= MAX_CHARS_POR_CHUNK:
            acumular(_llamar_claude_lab_texto(client, texto))
        else:
            paginas = texto.split("\n--- PÁGINA ")
            chunks: list[str] = []; chunk_actual = ""
            for pag in paginas:
                frag = "\n--- PÁGINA " + pag if pag and not pag.startswith("\n") else pag
                if len(chunk_actual) + len(frag) > MAX_CHARS_POR_CHUNK:
                    if chunk_actual.strip(): chunks.append(chunk_actual)
                    chunk_actual = frag
                else:
                    chunk_actual += frag
            if chunk_actual.strip(): chunks.append(chunk_actual)
            st.info(f"📄 PDF dividido en {len(chunks)} bloques de texto.")
            for i, chunk in enumerate(chunks, 1):
                st.caption(f"⚙️ Procesando bloque {i}/{len(chunks)}…")
                antes = len(todas); acumular(_llamar_claude_lab_texto(client, chunk))
                st.caption(f"   ↳ {len(todas)-antes} muestra(s) nueva(s).")

    # ── MODO VISIÓN (PDF escaneado o tablas como imagen) ───────────────────
    else:
        if chars_utiles > 500:
            st.info("🖼️ Texto extraído insuficiente para analíticos (tablas embebidas como imagen). Cambiando a modo visión…")
        else:
            st.info("🖼️ PDF escaneado detectado. Renderizando páginas como imágenes…")
        imagenes = pdf_a_imagenes_b64(pdf_bytes, dpi=VISION_DPI)
        total_imgs = len(imagenes)
        st.caption(f"📸 {total_imgs} página(s) a procesar.")
        if total_imgs == 0:
            st.error("❌ No se pudieron renderizar páginas del PDF."); return []
        lotes = [imagenes[i:i+MAX_PAGINAS_VISION] for i in range(0, total_imgs, MAX_PAGINAS_VISION)]
        for i, lote in enumerate(lotes, 1):
            st.caption(f"⚙️ Enviando lote {i}/{len(lotes)} ({len(lote)} página(s)) a Claude Vision…")
            antes = len(todas); acumular(_llamar_claude_lab_vision(client, lote))
            st.caption(f"   ↳ {len(todas)-antes} muestra(s) nueva(s).")

    return todas

# ---------------------------------------------------------------------------
# HERRAMIENTA 1: FILTRO DE FOTOGRAFÍAS
# ---------------------------------------------------------------------------
def analizar_fotografia(client: anthropic.Anthropic, image_bytes: bytes, media_type: str) -> dict:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    message = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=512, system=SYSTEM_PROMPT_VISION_FOTO,
        messages=[{"role":"user","content":[
            {"type":"image","source":{"type":"base64","media_type":media_type,"data":b64}},
            {"type":"text","text":"Clasifica esta imagen."}
        ]}],
    )
    text_content = message.content[0].text.strip()
    cleaned = limpiar_json_response(text_content)
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    raw = match.group(0) if match else cleaned
    try: return json.loads(raw)
    except Exception: return {"clasificacion":"Evidencia del Siniestro","pie_de_foto":"Evidencia fotográfica de campo."}

def render_herramienta_fotos(client: anthropic.Anthropic) -> None:
    st.header("📷 Herramienta 1 — Filtro y Archivo Organizado de Evidencias")
    if not st.session_state.proyecto_actual:
        st.warning("⚠️ Selecciona un proyecto en la barra lateral."); return
    with st.expander("➕ Subir Fotografías al Expediente", expanded=True):
        uploaded_files = st.file_uploader("Selecciona imágenes de campo", type=["jpg","jpeg","png","webp"], accept_multiple_files=True)
        if uploaded_files and st.button("🚀 Procesar Imágenes", type="primary"):
            progress = st.progress(0, text="Organizando evidencias con Claude…")
            total = len(uploaded_files)
            for idx, uf in enumerate(uploaded_files):
                raw_bytes = uf.read()
                res = analizar_fotografia(client, raw_bytes, "image/jpeg")
                guardar_foto_db(st.session_state.proyecto_actual, res.get("clasificacion","Evidencia del Siniestro"),
                                res.get("pie_de_foto","Fotografía del sitio."), uf.name, raw_bytes)
                progress.progress((idx+1)/total)
            st.success("¡Fotos guardadas de forma permanente!"); st.rerun()
    fotos = cargar_fotos_proyecto(st.session_state.proyecto_actual)
    carpetas = ["Evidencia del Siniestro","Excavaciones","Sondeos y Muestreo","Evidencias de Remediación","INSERVIBLE"]
    for cap in carpetas:
        fc = [f for f in fotos if f["categoria"]==cap]
        label = f"📂 {cap} ({len(fc)})" if cap!="INSERVIBLE" else f"🗑️ Archivo / Inservibles ({len(fc)})"
        with st.expander(label):
            if not fc: st.caption("Carpeta vacía.")
            else:
                for i in range(0, len(fc), 3):
                    cols = st.columns(3)
                    for col, item in zip(cols, fc[i:i+3]):
                        with col:
                            st.image(base64.b64decode(item["b64"]), use_container_width=True)
                            st.caption(f"📄 {item['nombre']}"); st.info(f"🏷️ {item['pie']}")
                            if st.button("🗑️ Eliminar", key=f"del_{item['id_foto']}", use_container_width=True):
                                if eliminar_foto_db(item["id_foto"]): st.rerun()

# ---------------------------------------------------------------------------
# HERRAMIENTA 3: VACIADO INTEGRAL DIRECTO (NOM-138 EXTRACTOR)
# ---------------------------------------------------------------------------
def render_herramienta_lab(client: anthropic.Anthropic) -> None:
    st.header("🧪 Herramienta 3 — Vaciado Automático de Laboratorio")
    if not st.session_state.proyecto_actual:
        st.warning("⚠️ Selecciona un proyecto en la barra lateral."); return

    detalles = obtener_detalles_proyecto(st.session_state.proyecto_actual)
    uso_suelo = detalles["uso_de_suelo"] if detalles else "Agrícola/Forestal"
    limites_vigentes = NOM_138_MATRIZ[uso_suelo]

    st.subheader(f"📋 Marco Regulatorio Activo: `NOM-138 ({uso_suelo})`")
    cols_l = st.columns(5)
    for col, (param, val) in zip(cols_l, limites_vigentes.items()):
        col.metric(f"LMP {param}", f"{val} mg/kg")
    st.markdown("---")

    uploaded_pdf = st.file_uploader(
        "Sube el PDF analítico integral de Novalabsa (texto nativo o escaneado)", type=["pdf"])

    if uploaded_pdf and st.button("🔍 Iniciar Extracción Cruzada", type="primary"):
        pdf_bytes = uploaded_pdf.read()
        with st.spinner("Analizando PDF y extrayendo muestras…"):
            muestras_extraidas = analizar_reporte_laboratorio(client, pdf_bytes)

        if not muestras_extraidas:
            st.error("❌ No se encontraron muestras. Verifica que el PDF contenga tablas analíticas legibles.")
            return

        st.success(f"✅ {len(muestras_extraidas)} muestra(s) identificadas. Guardando…")
        errores_guardado = 0
        for m in muestras_extraidas:
            try:
                hfl_val = safe_float(m.get("HFL",0.0)); b_val = safe_float(m.get("Benceno",0.0))
                t_val   = safe_float(m.get("Tolueno",0.0)); e_val = safe_float(m.get("Etilbenceno",0.0))
                x_val   = safe_float(m.get("Xilenos",0.0))
                rebo = (hfl_val>limites_vigentes["HFL"] or b_val>limites_vigentes["Benceno"]
                        or t_val>limites_vigentes["Tolueno"] or e_val>limites_vigentes["Etilbenceno"]
                        or x_val>limites_vigentes["Xilenos"])
                json_res = json.dumps({"HFL":hfl_val,"Benceno":b_val,"Tolueno":t_val,
                    "Etilbenceno":e_val,"Xilenos":x_val,"pH":safe_float(m.get("pH",0.0)),"Humedad":safe_float(m.get("Humedad",0.0))})
                guardar_muestra_db(st.session_state.proyecto_actual, str(m.get("id_muestra","")).strip(),
                    str(m.get("zona","Campo")).strip(), str(m.get("profundidad","0.0")).strip(),
                    str(m.get("coordenada_x","0.0")).strip(), str(m.get("coordenada_y","0.0")).strip(),
                    json_res, rebase=rebo)
            except Exception as ex:
                errores_guardado += 1; st.warning(f"⚠️ Error al guardar `{m.get('id_muestra','?')}`: {ex}")

        if errores_guardado:
            st.warning(f"Guardadas {len(muestras_extraidas)-errores_guardado} muestras. {errores_guardado} con errores.")
        else:
            st.success("¡Todas las muestras archivadas de forma permanente!")
        st.rerun()

    historial = cargar_laboratorio_proyecto(st.session_state.proyecto_actual)
    if historial:
        st.subheader("📊 Historial del Expediente Analítico")
        filas = []
        for h in historial:
            filas.append({"Zona Afectada":h["zona"],"Identificación Muestra":h["id_muestra"],
                "Profundidad (m)":h["profundidad"],"Coordenada X (Este)":h["x"],"Coordenada Y (Norte)":h["y"],
                "HFL":h["resultados"].get("HFL",0.0),"Benceno":h["resultados"].get("Benceno",0.0),
                "Tolueno":h["resultados"].get("Tolueno",0.0),"Etilbenceno":h["resultados"].get("Etilbenceno",0.0),
                "Xilenos":h["resultados"].get("Xilenos",0.0),"pH":h["resultados"].get("pH",0.0),
                "Humedad (%)":h["resultados"].get("Humedad",0.0),
                "Evaluación NOM-138":"🚨 EXCEDE" if h["rebase"] else "✅ Conforme"})
        df = pd.DataFrame(filas)
        st.dataframe(df.style.applymap(
            lambda v: "background-color:#ffcccc;color:#cc0000;font-weight:bold;" if v=="🚨 EXCEDE" else "",
            subset=["Evaluación NOM-138"]), use_container_width=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar expediente como CSV", data=csv,
            file_name=f"expediente_{st.session_state.proyecto_actual}.csv", mime="text/csv")

# ---------------------------------------------------------------------------
# CORE LOGÍSTICO API Y RENDERS
# ---------------------------------------------------------------------------
def get_client() -> anthropic.Anthropic:
    try: api_key = st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError): api_key = os.getenv("ANTHROPIC_API_KEY","")
    if not api_key: st.error("⚠️ Falta ANTHROPIC_API_KEY."); st.stop()
    return anthropic.Anthropic(api_key=api_key)

def render_sidebar() -> str:
    with st.sidebar:
        st.markdown("## 🌿 Hub Ambiental")
        st.write(f"👤 **Usuario:** {st.session_state.get('usuario_actual','Ingeniero')}")
        st.markdown("---")
        lista_bd = obtener_proyectos()
        proyecto_seleccionado = st.selectbox("Proyecto Activo:", ["Seleccionar..."]+lista_bd)
        if proyecto_seleccionado != "Seleccionar...":
            partes = proyecto_seleccionado.split(":",1)
            st.session_state.proyecto_actual = partes[0].strip()
            st.session_state.nombre_proyecto = partes[1].strip() if len(partes)>1 else ""
            st.success(f"✅ Conectado a: {st.session_state.proyecto_actual}")
        else:
            st.session_state.proyecto_actual = None
        with st.expander("➕ Crear Nuevo Proyecto"):
            with st.form("form_nuevo_proyecto"):
                nuevo_id=st.text_input("ID Proyecto *"); nuevo_nombre=st.text_input("Nombre Siniestro *")
                nuevo_uso=st.selectbox("Uso de Suelo",["Agrícola/Forestal","Industrial","Residencial"])
                if st.form_submit_button("Guardar Proyecto",type="primary"):
                    if nuevo_id and nuevo_nombre and crear_proyecto_db(nuevo_id,nuevo_nombre,nuevo_uso): st.rerun()
        st.markdown("---")
        herramienta = st.radio("Herramientas:",["📷 Filtro de Fotografías","🧪 Vaciado de Laboratorio"],label_visibility="collapsed")
        st.markdown("---")
        st.caption("⚙️ Motor: Claude Sonnet 4.6  \n**v2.1.0 (Vision + Texto)**")
    return herramienta

def check_password() -> bool:
    if "password_correct" not in st.session_state:
        st.markdown("## 🔒 Acceso Restringido")
        u=st.text_input("Usuario"); p=st.text_input("Contraseña",type="password")
        if (st.button("Entrar") and u==st.secrets["credenciales"]["usuario"]
                and p==st.secrets["credenciales"]["password"]):
            st.session_state["password_correct"]=True; st.rerun()
        return False
    return True

def main() -> None:
    client = get_client()
    herramienta = render_sidebar()
    if "Filtro" in herramienta: render_herramienta_fotos(client)
    elif "Laboratorio" in herramienta: render_herramienta_lab(client)

if __name__ == "__main__":
    if check_password(): main()
