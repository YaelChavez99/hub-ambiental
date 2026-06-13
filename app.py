"""
Hub de Automatización Ambiental
================================
Aplicación Streamlit multi-herramienta para empresas de remediación de suelos.
Motor cognitivo: Claude Sonnet (Entorno Corporativo Protegido).

Versión: 2.4.0 — Fase 3: Auditor Técnico Ambiental (H2)
Cambios Fase 2:
  - Módulo tabla_nom138.py integrado como generador de la tabla oficial
  - Herramienta 3 ahora muestra la tabla idéntica a la imagen de referencia:
      · Encabezado azul oscuro con nombre del siniestro
      · Columna ZONA AFECTADA con rowspan (celda fusionada)
      · Valores < L.C. mostrados como texto (no como 0.0)
      · Valores que superan el LMP resaltados en cyan (#00B0F0)
      · Fila de LMP al pie con fondo amarillo (#FFFF99)
      · Leyenda cyan y nota de límites cuantificables
  - Dos tabs en H3: tabla oficial + tabla Streamlit (para filtros/búsqueda)
  - Campo "Nombre del siniestro" usado como encabezado de la tabla
Versión base: 2.2.0 — Fase 1 (todas las correcciones previas mantenidas)
"""

from __future__ import annotations

import base64
import io
import json
import re
import os
from typing import Any

import anthropic
import fitz          # PyMuPDF
import pandas as pd
import pdfplumber
import psycopg2
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

# Módulo de tabla NOM-138 (Fase 2)
from tabla_nom138 import render_tabla_nom138, generar_tabla_nom138_html

# Módulo de auditoría técnica (Fase 3)
from auditor_tecnico import render_herramienta_auditor

# ---------------------------------------------------------------------------
# Configuración de página  —  DEBE ser la primera llamada Streamlit
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Hub de Automatización Ambiental",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# CONSTANTES GLOBALES
# ---------------------------------------------------------------------------

# ── Corrección 1: modelo en un solo lugar ──────────────────────────────────
MODEL_ID = "claude-sonnet-4-6"

# ── Corrección 2: parámetros Vision ajustados ─────────────────────────────
# 12 páginas por lote → PDF de 86 págs = 7-8 llamadas (vs 17 con lote=5)
MAX_PAGINAS_VISION  = 12
# 1 500 chars útiles como umbral mínimo para considerar PDF con texto real
UMBRAL_CHARS_UTILES = 1_500
# 100 DPI: suficiente para leer tablas; más alto → error 413 de payload
VISION_DPI          = 100
MAX_CHARS_POR_CHUNK = 80_000

# ── Matriz NOM-138-SEMARNAT/SSA1-2012 (mg/kg base seca) ───────────────────
NOM_138_MATRIZ: dict[str, dict[str, float]] = {
    "Agrícola/Forestal": {
        "HFL": 200.0,  "Benceno": 6.0,  "Tolueno": 40.0,
        "Etilbenceno": 10.0, "Xilenos": 40.0,
    },
    "Residencial": {
        "HFL": 1200.0, "Benceno": 6.0,  "Tolueno": 40.0,
        "Etilbenceno": 10.0, "Xilenos": 40.0,
    },
    "Industrial": {
        "HFL": 3000.0, "Benceno": 15.0, "Tolueno": 100.0,
        "Etilbenceno": 50.0, "Xilenos": 200.0,
    },
}

# ---------------------------------------------------------------------------
# BASE DE DATOS  —  PostgreSQL en la nube
# ---------------------------------------------------------------------------

def _obtener_conexion() -> psycopg2.extensions.connection:
    """
    Abre y devuelve una conexión nueva a PostgreSQL.
    Lanza excepción con mensaje claro si falla; el llamador decide cómo manejarlo.
    """
    try:
        url = st.secrets["DATABASE_URL"]
    except (KeyError, FileNotFoundError) as exc:
        raise RuntimeError(
            "DATABASE_URL no encontrada en secrets.toml. "
            "Agrega: DATABASE_URL = 'postgresql://user:pass@host:5432/dbname'"
        ) from exc
    return psycopg2.connect(url)


def inicializar_db() -> bool:
    """
    Corrección 3: crea las tablas si no existen.
    Retorna True si todo bien, False si hay error (sin llamar st.stop).
    """
    try:
        conn = _obtener_conexion()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS proyectos (
                id_proyecto    TEXT PRIMARY KEY,
                nombre_siniestro TEXT NOT NULL,
                uso_de_suelo   TEXT NOT NULL DEFAULT 'Agrícola/Forestal',
                estado         TEXT NOT NULL DEFAULT 'Activo',
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS fotos_sistema (
                id_foto       SERIAL PRIMARY KEY,
                id_proyecto   TEXT NOT NULL REFERENCES proyectos(id_proyecto) ON DELETE CASCADE,
                categoria_ia  TEXT,
                pie_de_foto   TEXT,
                nombre_archivo TEXT,
                foto_b64      TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS datos_laboratorio (
                id_registro   SERIAL PRIMARY KEY,
                id_proyecto   TEXT NOT NULL REFERENCES proyectos(id_proyecto) ON DELETE CASCADE,
                id_muestra    TEXT,
                zona          TEXT,
                profundidad   TEXT,
                coordenada_x  TEXT,
                coordenada_y  TEXT,
                json_resultados TEXT,
                rebase_nom    BOOLEAN DEFAULT FALSE
            )
        """)
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception as exc:
        # Guarda el error para mostrarlo una sola vez en la UI
        st.session_state["_db_error"] = str(exc)
        return False


# ── CRUD proyectos ──────────────────────────────────────────────────────────

def obtener_proyectos() -> list[str]:
    try:
        conn = _obtener_conexion(); c = conn.cursor()
        c.execute(
            "SELECT id_proyecto, nombre_siniestro FROM proyectos "
            "ORDER BY fecha_creacion DESC"
        )
        rows = c.fetchall(); c.close(); conn.close()
        return [f"{r[0]}: {r[1]}" for r in rows]
    except Exception:
        return []


def obtener_detalles_proyecto(id_proyecto: str) -> dict | None:
    try:
        conn = _obtener_conexion(); c = conn.cursor()
        c.execute(
            "SELECT nombre_siniestro, uso_de_suelo "
            "FROM proyectos WHERE id_proyecto = %s",
            (id_proyecto,),
        )
        row = c.fetchone(); c.close(); conn.close()
        if row:
            return {"nombre": row[0], "uso_de_suelo": row[1]}
    except Exception:
        pass
    return None


def crear_proyecto_db(id_proj: str, nombre: str, uso: str) -> bool:
    try:
        conn = _obtener_conexion(); c = conn.cursor()
        c.execute(
            "INSERT INTO proyectos (id_proyecto, nombre_siniestro, uso_de_suelo, estado) "
            "VALUES (%s, %s, %s, %s)",
            (id_proj.strip(), nombre.strip(), uso, "Activo"),
        )
        conn.commit(); c.close(); conn.close()
        return True
    except psycopg2.errors.UniqueViolation:
        st.error(f"Ya existe un proyecto con el ID '{id_proj}'. Usa otro ID.")
        return False
    except Exception as exc:
        st.error(f"Error al crear proyecto: {exc}")
        return False


# ── CRUD fotos ──────────────────────────────────────────────────────────────

def guardar_foto_db(
    id_proyecto: str, category: str, pie: str,
    archivo: str, foto_bytes: bytes
) -> None:
    try:
        b64 = base64.b64encode(foto_bytes).decode("utf-8")
        conn = _obtener_conexion(); c = conn.cursor()
        c.execute(
            "INSERT INTO fotos_sistema "
            "(id_proyecto, categoria_ia, pie_de_foto, nombre_archivo, foto_b64) "
            "VALUES (%s, %s, %s, %s, %s)",
            (id_proyecto, category, pie, archivo, b64),
        )
        conn.commit(); c.close(); conn.close()
    except Exception as exc:
        st.error(f"Error al guardar foto '{archivo}': {exc}")


def cargar_fotos_proyecto(id_proyecto: str) -> list[dict]:
    try:
        conn = _obtener_conexion(); c = conn.cursor()
        c.execute(
            "SELECT id_foto, categoria_ia, pie_de_foto, nombre_archivo, foto_b64 "
            "FROM fotos_sistema WHERE id_proyecto = %s ORDER BY id_foto DESC",
            (id_proyecto,),
        )
        rows = c.fetchall(); c.close(); conn.close()
        return [
            {"id_foto": r[0], "categoria": r[1], "pie": r[2],
             "nombre": r[3], "b64": r[4]}
            for r in rows
        ]
    except Exception:
        return []


def eliminar_foto_db(id_foto: int) -> bool:
    try:
        conn = _obtener_conexion(); c = conn.cursor()
        c.execute("DELETE FROM fotos_sistema WHERE id_foto = %s", (id_foto,))
        conn.commit(); c.close(); conn.close()
        return True
    except Exception:
        return False


# ── CRUD laboratorio ────────────────────────────────────────────────────────

def guardar_muestra_db(
    id_proyecto: str, id_muestra: str, zona: str, prof: str,
    x: str, y: str, json_res: str, rebase: bool
) -> None:
    try:
        conn = _obtener_conexion(); c = conn.cursor()
        # Upsert: elimina la versión anterior antes de insertar
        c.execute(
            "DELETE FROM datos_laboratorio "
            "WHERE id_proyecto = %s AND id_muestra = %s",
            (id_proyecto, id_muestra),
        )
        c.execute(
            "INSERT INTO datos_laboratorio "
            "(id_proyecto, id_muestra, zona, profundidad, coordenada_x, "
            " coordenada_y, json_resultados, rebase_nom) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (id_proyecto, id_muestra, zona, prof, x, y, json_res, rebase),
        )
        conn.commit(); c.close(); conn.close()
    except Exception as exc:
        st.error(f"Error al guardar muestra '{id_muestra}': {exc}")


def cargar_laboratorio_proyecto(id_proyecto: str) -> list[dict]:
    try:
        conn = _obtener_conexion(); c = conn.cursor()
        c.execute(
            "SELECT id_muestra, zona, profundidad, coordenada_x, coordenada_y, "
            "       json_resultados, rebase_nom "
            "FROM datos_laboratorio "
            "WHERE id_proyecto = %s ORDER BY id_registro ASC",
            (id_proyecto,),
        )
        rows = c.fetchall(); c.close(); conn.close()
        return [
            {
                "id_muestra":  r[0], "zona":       r[1],
                "profundidad": r[2], "x":          r[3],
                "y":           r[4],
                "resultados":  json.loads(r[5]) if r[5] else {},
                "rebase":      r[6],
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# HELPERS  —  Parseo y conversión
# ---------------------------------------------------------------------------

def safe_float(val: Any) -> float:
    """Convierte cualquier valor a float de forma segura. Devuelve 0.0 si falla."""
    try:
        if isinstance(val, str):
            val = val.replace(",", "").strip()
        return float(val)
    except Exception:
        return 0.0


def limpiar_json_response(raw: str) -> str:
    """Elimina bloques de código markdown que Claude a veces agrega."""
    raw = re.sub(r"```(?:json)?\s*", "", raw)
    return raw.strip().strip("`").strip()


def parsear_json_lista(text_content: str) -> list[dict]:
    """
    Intenta parsear un JSON array desde la respuesta de Claude.
    Aplica dos capas de limpieza antes de fallar con un warning.
    """
    cleaned = limpiar_json_response(text_content)
    # Buscar el array JSON aunque haya texto antes o después
    match = re.search(r'\[\s*\{.*\}\s*\]', cleaned, re.DOTALL)
    raw = match.group(0) if match else cleaned
    # Limpiar comas finales antes de cierre (error común en JSON generado por LLMs)
    raw = re.sub(r',\s*([\]}])', r'\1', raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Segunda capa: eliminar caracteres de control invisibles
        raw2 = re.sub(r'[\x00-\x1f\x7f]', ' ', raw)
        raw2 = re.sub(r',\s*([\]}])', r'\1', raw2)
        try:
            return json.loads(raw2)
        except json.JSONDecodeError as exc:
            st.warning(
                f"⚠️ No se pudo parsear la respuesta JSON.\n\n"
                f"**Error:** {exc}\n\n"
                f"**Fragmento recibido:**\n```\n{raw[:600]}\n```"
            )
            return []


# ---------------------------------------------------------------------------
# EXTRACCIÓN DE TEXTO DEL PDF  —  Herramientas 2, 3, 4
# ---------------------------------------------------------------------------

def extract_pdf_text(pdf_bytes: bytes) -> str:
    """
    Extrae texto de todas las páginas con pdfplumber.
    Filtra páginas de cromatogramas (TRACE, INTENSITY, RT(MIN)).
    """
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            upper = text.upper()
            if any(kw in upper for kw in
                   ("TRACE 1310", "INTENSITY", "RT(MIN)", "TRACEFINDER")):
                continue
            parts.append(f"\n--- PÁGINA {i} ---\n{text}")
    return "\n".join(parts)


def texto_tiene_datos_analiticos(texto: str) -> bool:
    """
    Corrección 4: umbral más estricto para diferenciar PDFs con texto real
    de PDFs escaneados que tienen solo metadatos embebidos.
    Requiere ≥4 keywords analíticas Y ≥10 patrones numéricos.
    """
    upper = texto.upper()
    keywords = [
        "HFL", "BENCENO", "TOLUENO", "PROFUNDIDAD",
        "COORDENADA", "ZONA", "L.C.", "MG/KG", "MUESTRA",
    ]
    hits     = sum(1 for kw in keywords if kw in upper)
    num_hits = len(re.findall(r'\d{3,}[\.,]\d{2,}', texto))
    return hits >= 4 and num_hits >= 10


# ---------------------------------------------------------------------------
# RENDERIZADO PDF → IMÁGENES  —  Modo Vision
# ---------------------------------------------------------------------------

def pdf_a_imagenes_b64(pdf_bytes: bytes, dpi: int = VISION_DPI) -> list[str]:
    """
    Renderiza cada página relevante del PDF como JPEG base64.
    - JPEG (~5× más liviano que PNG) evita el error 413 de payload.
    - Excluye cromatogramas (proporción altura/ancho > 3.5).
    """
    imagenes: list[str] = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    for page in doc:
        rect = page.rect
        if (rect.height / max(rect.width, 1)) > 3.5:
            continue   # Cromatograma vertical — omitir
        pix       = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB)
        jpeg_bytes = pix.tobytes("jpeg", jpg_quality=85)
        imagenes.append(base64.b64encode(jpeg_bytes).decode("utf-8"))
    doc.close()
    return imagenes


# ---------------------------------------------------------------------------
# SYSTEM PROMPTS
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_VISION_FOTO = """
Eres un auditor técnico ambiental experto en inspección de sitios contaminados
por derrames de hidrocarburos en México.

Clasifica la fotografía en UNA de estas categorías:
- "Evidencia del Siniestro"
- "Excavaciones"
- "Sondeos y Muestreo"
- "Evidencias de Remediación"
- "INSERVIBLE"

Pie de foto: máximo 15 palabras, técnico, sin frases como "En esta fotografía se observa".

Devuelve ÚNICAMENTE el JSON sin markdown:
{"clasificacion": "...", "pie_de_foto": "..."}
"""

SYSTEM_PROMPT_LAB = """
Eres un auditor analítico pericial experto en reportes de laboratorio de suelos
contaminados (Novalabsa / LABSA) conforme a la NOM-138-SEMARNAT/SSA1-2012.

Realiza el vaciado cruzado completo de cada muestra identificada en el reporte.

Para cada muestra extrae:
1. ANALÍTICOS: HFL, Benceno, Tolueno, Etilbenceno, Xilenos, pH, Humedad.
   Si el valor dice "< L.C.", "< l.c.", "ND", o la celda está vacía → 0.0
2. CADENA DE CUSTODIA: zona_afectada, profundidad (m), coordenada_x (Metros Este),
   coordenada_y (Metros Norte).
   Si no encuentras coordenadas → usa "0.0"

REGLAS:
- Incluye TODAS las muestras, incluyendo duplicados (sufijo -DUP o -Dup).
- No repitas la misma id_muestra dos veces.
- Devuelve ÚNICAMENTE el arreglo JSON, sin texto adicional, sin markdown.

Formato requerido (ejemplo):
[
  {
    "id_muestra":   "P1 0.6",
    "zona":         "ZONA 1",
    "profundidad":  "0.60",
    "coordenada_x": "250037.32",
    "coordenada_y": "2420516.68",
    "HFL":          1876.25,
    "Benceno":      0.0,
    "Tolueno":      0.0,
    "Etilbenceno":  0.0,
    "Xilenos":      0.0,
    "pH":           7.87,
    "Humedad":      16.797
  }
]
"""


# ---------------------------------------------------------------------------
# LLAMADAS A CLAUDE  —  Laboratorio
# ---------------------------------------------------------------------------

def _llamar_claude_lab_texto(
    client: anthropic.Anthropic, texto: str
) -> list[dict]:
    """Envía un bloque de texto al modelo y retorna la lista de muestras."""
    try:
        msg = client.messages.create(
            model=MODEL_ID,
            max_tokens=8192,
            system=SYSTEM_PROMPT_LAB,
            messages=[{
                "role": "user",
                "content": f"Efectúa el vaciado cruzado analítico:\n\n{texto}",
            }],
        )
        if msg.stop_reason == "max_tokens":
            st.warning("⚠️ Respuesta truncada por límite de tokens en este bloque.")
        return parsear_json_lista(msg.content[0].text.strip())
    except anthropic.APIStatusError as exc:
        st.error(f"Error de API (texto): {exc.status_code} — {exc.message}")
        return []
    except Exception as exc:
        st.error(f"Error inesperado (texto): {exc}")
        return []


def _llamar_claude_lab_vision(
    client: anthropic.Anthropic, imagenes_b64: list[str]
) -> list[dict]:
    """Envía un lote de páginas como imágenes y retorna la lista de muestras."""
    if not imagenes_b64:
        return []
    content: list[dict] = []
    for b64 in imagenes_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
    content.append({
        "type": "text",
        "text": (
            "Estas imágenes son páginas de un reporte analítico de laboratorio.\n"
            "Efectúa el vaciado cruzado de TODAS las muestras en las tablas.\n"
            "Si el valor dice '< L.C.' o 'ND' → 0.0.\n"
            "Devuelve ÚNICAMENTE el arreglo JSON sin texto adicional."
        ),
    })
    try:
        msg = client.messages.create(
            model=MODEL_ID,
            max_tokens=8192,
            system=SYSTEM_PROMPT_LAB,
            messages=[{"role": "user", "content": content}],
        )
        if msg.stop_reason == "max_tokens":
            st.warning("⚠️ Respuesta truncada (lote de imágenes).")
        return parsear_json_lista(msg.content[0].text.strip())
    except anthropic.BadRequestError as exc:
        # Error 400 típico cuando el payload de imágenes es demasiado grande
        st.warning(
            f"⚠️ Lote rechazado (payload demasiado grande o imagen inválida): {exc}\n"
            "Considera reducir VISION_DPI o MAX_PAGINAS_VISION."
        )
        return []
    except anthropic.APIStatusError as exc:
        st.error(f"Error de API (visión): {exc.status_code} — {exc.message}")
        return []
    except Exception as exc:
        st.error(f"Error inesperado (visión): {exc}")
        return []


def analizar_reporte_laboratorio(
    client: anthropic.Anthropic, pdf_bytes: bytes
) -> list[dict]:
    """
    Estrategia adaptativa mejorada:
    1. Extrae texto con pdfplumber y evalúa si tiene datos analíticos reales.
    2. Si sí → modo texto con chunking automático.
    3. Si no → modo visión: renderiza páginas como JPEG y envía en lotes.
    Deduplica por id_muestra en ambos casos.
    """
    texto       = extract_pdf_text(pdf_bytes)
    chars_utiles = len(texto.replace(" ", "").replace("\n", "").replace("-", ""))
    st.caption(f"📊 Caracteres útiles en el PDF: {chars_utiles:,}")

    todas:     list[dict] = []
    ids_vistos: set[str]  = set()

    def acumular(muestras: list[dict]) -> None:
        for m in muestras:
            iid = str(m.get("id_muestra", "")).strip()
            if iid and iid not in ids_vistos:
                todas.append(m)
                ids_vistos.add(iid)

    # ── Modo texto ────────────────────────────────────────────────────────
    if chars_utiles >= UMBRAL_CHARS_UTILES and texto_tiene_datos_analiticos(texto):
        st.info("📄 Modo texto: capa de texto con datos analíticos detectada.")
        if len(texto) <= MAX_CHARS_POR_CHUNK:
            acumular(_llamar_claude_lab_texto(client, texto))
        else:
            paginas = texto.split("\n--- PÁGINA ")
            chunks: list[str] = []
            chunk_actual = ""
            for pag in paginas:
                frag = ("\n--- PÁGINA " + pag
                        if pag and not pag.startswith("\n") else pag)
                if len(chunk_actual) + len(frag) > MAX_CHARS_POR_CHUNK:
                    if chunk_actual.strip():
                        chunks.append(chunk_actual)
                    chunk_actual = frag
                else:
                    chunk_actual += frag
            if chunk_actual.strip():
                chunks.append(chunk_actual)
            st.info(f"📄 PDF dividido en {len(chunks)} bloques de texto.")
            for i, chunk in enumerate(chunks, 1):
                st.caption(f"⚙️ Bloque {i}/{len(chunks)}…")
                antes = len(todas)
                acumular(_llamar_claude_lab_texto(client, chunk))
                st.caption(f"   ↳ {len(todas) - antes} muestra(s) nueva(s).")

    # ── Modo visión ────────────────────────────────────────────────────────
    else:
        motivo = (
            "Texto con < 4 keywords analíticas o < 10 valores numéricos"
            if chars_utiles >= UMBRAL_CHARS_UTILES
            else "PDF escaneado (sin capa de texto)"
        )
        st.info(f"🖼️ Modo visión activado — {motivo}. Renderizando páginas…")
        imagenes = pdf_a_imagenes_b64(pdf_bytes, dpi=VISION_DPI)
        total    = len(imagenes)
        if total == 0:
            st.error("❌ No se pudieron renderizar páginas del PDF.")
            return []
        st.caption(f"📸 {total} página(s) a procesar en lotes de {MAX_PAGINAS_VISION}.")
        lotes = [
            imagenes[i : i + MAX_PAGINAS_VISION]
            for i in range(0, total, MAX_PAGINAS_VISION)
        ]
        for i, lote in enumerate(lotes, 1):
            st.caption(f"⚙️ Lote {i}/{len(lotes)} ({len(lote)} página(s))…")
            antes = len(todas)
            acumular(_llamar_claude_lab_vision(client, lote))
            st.caption(f"   ↳ {len(todas) - antes} muestra(s) nueva(s).")

    return todas


# ---------------------------------------------------------------------------
# HERRAMIENTA 1 — Filtro y Archivo de Fotografías
# ---------------------------------------------------------------------------

def analizar_fotografia(
    client: anthropic.Anthropic, image_bytes: bytes, media_type: str
) -> dict:
    """Clasifica una imagen de campo y genera su pie de foto técnico."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    try:
        msg = client.messages.create(
            model=MODEL_ID,
            max_tokens=512,
            system=SYSTEM_PROMPT_VISION_FOTO,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": "Clasifica esta imagen."},
                ],
            }],
        )
        text_content = msg.content[0].text.strip()
        cleaned      = limpiar_json_response(text_content)
        match        = re.search(r'\{.*\}', cleaned, re.DOTALL)
        raw          = match.group(0) if match else cleaned
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "clasificacion": "Evidencia del Siniestro",
            "pie_de_foto":   "Evidencia fotográfica de campo.",
        }
    except Exception as exc:
        st.warning(f"Error al clasificar imagen: {exc}")
        return {
            "clasificacion": "Evidencia del Siniestro",
            "pie_de_foto":   "Evidencia fotográfica de campo.",
        }


def render_herramienta_fotos(client: anthropic.Anthropic) -> None:
    st.header("📷 Herramienta 1 — Filtro y Archivo de Evidencias")

    if not st.session_state.proyecto_actual:
        st.warning("⚠️ Selecciona o crea un proyecto en la barra lateral para continuar.")
        return

    with st.expander("➕ Subir Fotografías al Expediente", expanded=True):
        uploaded_files = st.file_uploader(
            "Selecciona imágenes de campo",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key="uploader_fotos",
        )
        if uploaded_files and st.button("🚀 Procesar Imágenes", type="primary"):
            progress = st.progress(0, text="Clasificando evidencias…")
            total    = len(uploaded_files)
            for idx, uf in enumerate(uploaded_files):
                raw_bytes = uf.read()
                res       = analizar_fotografia(client, raw_bytes, "image/jpeg")
                guardar_foto_db(
                    st.session_state.proyecto_actual,
                    res.get("clasificacion", "Evidencia del Siniestro"),
                    res.get("pie_de_foto",   "Fotografía del sitio."),
                    uf.name,
                    raw_bytes,
                )
                progress.progress((idx + 1) / total)
            st.success("¡Fotos guardadas permanentemente!")
            st.rerun()

    fotos   = cargar_fotos_proyecto(st.session_state.proyecto_actual)
    carpetas = [
        "Evidencia del Siniestro", "Excavaciones",
        "Sondeos y Muestreo", "Evidencias de Remediación", "INSERVIBLE",
    ]
    for cap in carpetas:
        fc    = [f for f in fotos if f["categoria"] == cap]
        label = (
            f"📂 {cap} ({len(fc)})"
            if cap != "INSERVIBLE"
            else f"🗑️ Archivo / Inservibles ({len(fc)})"
        )
        with st.expander(label):
            if not fc:
                st.caption("Carpeta vacía.")
            else:
                for i in range(0, len(fc), 3):
                    cols = st.columns(3)
                    for col, item in zip(cols, fc[i : i + 3]):
                        with col:
                            st.image(
                                base64.b64decode(item["b64"]),
                                use_container_width=True,
                            )
                            st.caption(f"📄 {item['nombre']}")
                            st.info(f"🏷️ {item['pie']}")
                            if st.button(
                                "🗑️ Eliminar",
                                key=f"del_{item['id_foto']}",
                                use_container_width=True,
                            ):
                                if eliminar_foto_db(item["id_foto"]):
                                    st.rerun()


# ---------------------------------------------------------------------------
# HERRAMIENTA 3 — Vaciado Automático de Laboratorio (NOM-138)
# ---------------------------------------------------------------------------

def _highlight_excedencias(df: pd.DataFrame, limites: dict[str, float]):
    """
    Aplica estilo condicional a la tabla:
    - Rojo  : valor > LMP
    - Naranja: 80-100% del LMP (zona de alerta)
    - Verde : dentro de norma
    """
    param_cols = [p for p in limites if p in df.columns]

    def color_cell(val: Any, lmp: float) -> str:
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        if v > lmp:
            return "background-color:#FF4B4B;color:white;font-weight:bold;"
        if v > lmp * 0.8:
            return "background-color:#FFA500;color:#333;"
        return "background-color:#21BA45;color:white;"

    styler = df.style
    for col in param_cols:
        styler = styler.map(
            lambda v, lmp=limites[col]: color_cell(v, lmp),
            subset=[col],
        )
    return styler


def render_herramienta_lab(client: anthropic.Anthropic) -> None:
    st.header("🧪 Herramienta 3 — Vaciado Automático de Laboratorio")

    if not st.session_state.proyecto_actual:
        st.warning("⚠️ Selecciona o crea un proyecto en la barra lateral para continuar.")
        return

    detalles     = obtener_detalles_proyecto(st.session_state.proyecto_actual)
    uso_suelo    = detalles["uso_de_suelo"]    if detalles else "Agrícola/Forestal"
    nombre_sin   = detalles["nombre"]          if detalles else ""
    lim_vigentes = NOM_138_MATRIZ[uso_suelo]

    # ── Métricas de LMP activos ─────────────────────────────────────────────
    cols_l = st.columns(5)
    for col, (param, val) in zip(cols_l, lim_vigentes.items()):
        col.metric(f"LMP {param}", f"{val} mg/kg")
    st.caption(
        f"**NOM-138 — {uso_suelo}** &nbsp;|&nbsp; "
        "LC NOVALABSA: HFL=4.68 · Benceno=0.030 · Tolueno=0.10 · "
        "Etilbenceno=0.20 · m,p-Xilenos=0.30 mg/kg"
    )
    st.markdown("---")

    # ── Subida y extracción ─────────────────────────────────────────────────
    uploaded_pdf = st.file_uploader(
        "Sube el PDF analítico de NOVALABSA (texto nativo o escaneado)",
        type=["pdf"],
        key="uploader_lab",
    )

    if uploaded_pdf and st.button("🔍 Iniciar extracción cruzada", type="primary"):
        pdf_bytes = uploaded_pdf.read()
        with st.spinner("Analizando PDF con Claude…"):
            muestras = analizar_reporte_laboratorio(client, pdf_bytes)

        if not muestras:
            st.error(
                "❌ No se encontraron muestras. "
                "Verifica que el PDF contenga tablas analíticas de laboratorio."
            )
            return

        st.success(f"✅ {len(muestras)} muestra(s) identificadas. Guardando en base de datos…")
        errores = 0
        for m in muestras:
            try:
                hfl = safe_float(m.get("HFL", 0))
                ben = safe_float(m.get("Benceno", 0))
                tol = safe_float(m.get("Tolueno", 0))
                etb = safe_float(m.get("Etilbenceno", 0))
                xil = safe_float(m.get("Xilenos", 0))
                rebase = (
                    hfl > lim_vigentes["HFL"]         or
                    ben > lim_vigentes["Benceno"]      or
                    tol > lim_vigentes["Tolueno"]      or
                    etb > lim_vigentes["Etilbenceno"]  or
                    xil > lim_vigentes["Xilenos"]
                )
                json_res = json.dumps({
                    "HFL":         hfl,
                    "Benceno":     ben,
                    "Tolueno":     tol,
                    "Etilbenceno": etb,
                    "Xilenos":     xil,
                    "pH":          safe_float(m.get("pH",      0)),
                    "Humedad":     safe_float(m.get("Humedad", 0)),
                })
                guardar_muestra_db(
                    st.session_state.proyecto_actual,
                    str(m.get("id_muestra",   "")).strip(),
                    str(m.get("zona",         "Campo")).strip(),
                    str(m.get("profundidad",  "0.0")).strip(),
                    str(m.get("coordenada_x", "0.0")).strip(),
                    str(m.get("coordenada_y", "0.0")).strip(),
                    json_res,
                    rebase=rebase,
                )
            except Exception as exc:
                errores += 1
                st.warning(f"⚠️ Error en muestra '{m.get('id_muestra','?')}': {exc}")

        if errores:
            st.warning(
                f"Guardadas {len(muestras) - errores} muestras. "
                f"{errores} con errores."
            )
        else:
            st.success("¡Todas las muestras archivadas permanentemente!")
        st.rerun()

    # ── Historial ───────────────────────────────────────────────────────────
    historial = cargar_laboratorio_proyecto(st.session_state.proyecto_actual)
    if not historial:
        st.info(
            "El expediente analítico está vacío. "
            "Sube el PDF del laboratorio para comenzar."
        )
        return

    # ── Métricas de excedencias ─────────────────────────────────────────────
    params_exc = list(lim_vigentes.keys())
    exc_counts = {p: 0 for p in params_exc}
    for h in historial:
        res = h.get("resultados", {})
        for p in params_exc:
            if safe_float(res.get(p, 0)) > lim_vigentes[p]:
                exc_counts[p] += 1

    st.subheader(f"📊 Expediente analítico — {len(historial)} muestras")
    cols_m = st.columns(len(exc_counts) + 1)
    total_exc = sum(1 for h in historial if h.get("rebase"))
    cols_m[0].metric("Total fuera de norma", total_exc,
                     delta="muestras", delta_color="inverse")
    for col, (param, count) in zip(cols_m[1:], exc_counts.items()):
        col.metric(
            label=f"{param} > LMP",
            value=count,
            delta=f"LMP {lim_vigentes[param]}",
            delta_color="inverse",
        )

    # ── Dos tabs: tabla oficial + tabla interactiva ─────────────────────────
    tab_oficial, tab_interactiva, tab_descarga = st.tabs([
        "📋 Tabla Oficial (estilo NOVALABSA)",
        "🔍 Tabla Interactiva",
        "⬇️ Descargas",
    ])

    # ────────────────────────────────────────────────────────────────────────
    # TAB 1 — TABLA OFICIAL NOVALABSA
    # ────────────────────────────────────────────────────────────────────────
    with tab_oficial:
        st.caption(
            "Formato idéntico al reporte NOVALABSA: "
            "zonas fusionadas · cyan en excedencias · LMP al pie."
        )
        # Altura dinámica según número de muestras (aprox 22px por fila + 250 de header)
        altura = min(250 + len(historial) * 22, 900)
        render_tabla_nom138(
            historial        = historial,
            lim_vigentes     = lim_vigentes,
            titulo_proyecto  = st.session_state.proyecto_actual,
            uso_suelo        = uso_suelo,
            nombre_siniestro = nombre_sin,
            altura_px        = altura,
        )

    # ────────────────────────────────────────────────────────────────────────
    # TAB 2 — TABLA INTERACTIVA STREAMLIT (filtros, búsqueda, highlight)
    # ────────────────────────────────────────────────────────────────────────
    with tab_interactiva:
        # Filtro rápido por zona
        zonas_disponibles = sorted({
            str(h.get("zona", "")).upper().strip()
            for h in historial
        })
        zona_filtro = st.selectbox(
            "Filtrar por zona:",
            ["Todas"] + zonas_disponibles,
            key="zona_filtro_lab",
        )

        historial_filtrado = (
            historial if zona_filtro == "Todas"
            else [h for h in historial
                  if str(h.get("zona","")).upper().strip() == zona_filtro]
        )

        filas: list[dict] = []
        for h in historial_filtrado:
            res = h.get("resultados", {})
            filas.append({
                "Zona":        h["zona"],
                "Muestra":     h["id_muestra"],
                "Prof. (m)":   h["profundidad"],
                "X (Este)":    h["x"],
                "Y (Norte)":   h["y"],
                "HFL":         safe_float(res.get("HFL",         0)),
                "Benceno":     safe_float(res.get("Benceno",     0)),
                "Tolueno":     safe_float(res.get("Tolueno",     0)),
                "Etilbenceno": safe_float(res.get("Etilbenceno", 0)),
                "Xilenos":     safe_float(res.get("Xilenos",     0)),
                "pH":          safe_float(res.get("pH",          0)),
                "Humedad (%)": safe_float(res.get("Humedad",     0)),
                "NOM-138":     "🚨 EXCEDE" if h["rebase"] else "✅ Conforme",
            })

        df = pd.DataFrame(filas)
        for col in ["HFL","Benceno","Tolueno","Etilbenceno","Xilenos","pH","Humedad (%)"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        l1, l2, l3 = st.columns(3)
        l1.markdown("🔴 **Supera el LMP**")
        l2.markdown("🟠 **80–100 % del LMP**")
        l3.markdown("🟢 **Dentro de norma**")

        st.dataframe(
            _highlight_excedencias(df, lim_vigentes),
            use_container_width=True,
            hide_index=True,
        )

        # Muestras fuera de norma
        df_exc = df[df["NOM-138"] == "🚨 EXCEDE"]
        if not df_exc.empty:
            with st.expander(f"🚨 Ver solo las {len(df_exc)} muestras que exceden el LMP"):
                st.dataframe(
                    _highlight_excedencias(df_exc, lim_vigentes),
                    use_container_width=True,
                    hide_index=True,
                )

    # ────────────────────────────────────────────────────────────────────────
    # TAB 3 — DESCARGAS
    # ────────────────────────────────────────────────────────────────────────
    with tab_descarga:
        # Construir df completo para exportar
        filas_exp: list[dict] = []
        for h in historial:
            res = h.get("resultados", {})
            filas_exp.append({
                "Zona Afectada":   h["zona"],
                "Muestra":         h["id_muestra"],
                "Profundidad (m)": h["profundidad"],
                "X (Metros Este)": h["x"],
                "Y (Metros Norte)":h["y"],
                "HFL (mg/kg)":     safe_float(res.get("HFL",         0)),
                "Benceno (mg/kg)": safe_float(res.get("Benceno",     0)),
                "Tolueno (mg/kg)": safe_float(res.get("Tolueno",     0)),
                "Etilbenceno (mg/kg)": safe_float(res.get("Etilbenceno", 0)),
                "Xilenos (mg/kg)": safe_float(res.get("Xilenos",     0)),
                "pH":              safe_float(res.get("pH",          0)),
                "Humedad (%)":     safe_float(res.get("Humedad",     0)),
                "Evaluación NOM-138": "EXCEDE" if h["rebase"] else "CONFORME",
            })
        df_exp = pd.DataFrame(filas_exp)

        col_csv, col_excel, col_html = st.columns(3)

        with col_csv:
            st.download_button(
                "⬇️ Descargar CSV",
                data=df_exp.to_csv(index=False).encode("utf-8"),
                file_name=f"NOM138_{st.session_state.proyecto_actual}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with col_excel:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df_exp.to_excel(writer, index=False, sheet_name="Resultados NOM-138")
            st.download_button(
                "⬇️ Descargar Excel",
                data=buf.getvalue(),
                file_name=f"NOM138_{st.session_state.proyecto_actual}.xlsx",
                mime=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"
                ),
                use_container_width=True,
            )

        with col_html:
            # Exportar la tabla oficial como HTML independiente (para incluir en informes)
            html_tabla = generar_tabla_nom138_html(
                historial        = historial,
                lim_vigentes     = lim_vigentes,
                titulo_proyecto  = st.session_state.proyecto_actual,
                uso_suelo        = uso_suelo,
                nombre_siniestro = nombre_sin,
            )
            html_completo = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<title>NOM-138 — {st.session_state.proyecto_actual}</title>
</head><body style="margin:20px;font-family:Arial,sans-serif">
{html_tabla}
</body></html>"""
            st.download_button(
                "⬇️ Descargar HTML (tabla oficial)",
                data=html_completo.encode("utf-8"),
                file_name=f"NOM138_{st.session_state.proyecto_actual}.html",
                mime="text/html",
                use_container_width=True,
            )

        st.caption(
            "El archivo HTML puede abrirse en cualquier navegador o pegarse "
            "directamente en Word para mantener el formato de la tabla."
        )


# ---------------------------------------------------------------------------
# CORE  —  API, sidebar, autenticación, main
# ---------------------------------------------------------------------------

def get_client() -> anthropic.Anthropic:
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError):
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        st.error(
            "⚠️ No se encontró ANTHROPIC_API_KEY.\n\n"
            "Agrégala en `.streamlit/secrets.toml`:\n"
            "```\nANTHROPIC_API_KEY = 'sk-ant-...'\n```"
        )
        st.stop()
    return anthropic.Anthropic(api_key=api_key)


def render_sidebar() -> str:
    with st.sidebar:
        st.markdown("## 🌿 Hub Ambiental")
        st.write(f"👤 **Usuario:** {st.session_state.usuario_actual}")
        st.markdown("---")

        # Selector de proyecto
        lista_bd = obtener_proyectos()
        opciones = ["Seleccionar…"] + lista_bd
        seleccion = st.selectbox("Proyecto activo:", opciones)

        if seleccion != "Seleccionar…":
            partes = seleccion.split(":", 1)
            st.session_state.proyecto_actual  = partes[0].strip()
            st.session_state.nombre_proyecto  = (
                partes[1].strip() if len(partes) > 1 else ""
            )
            st.success(f"✅ {st.session_state.proyecto_actual}")
        else:
            st.session_state.proyecto_actual = None
            st.session_state.nombre_proyecto = ""

        # Crear nuevo proyecto
        with st.expander("➕ Crear nuevo proyecto"):
            with st.form("form_nuevo_proyecto"):
                nuevo_id     = st.text_input("ID Proyecto *",
                                             placeholder="ej. OT-2026-001")
                nuevo_nombre = st.text_input("Nombre del siniestro *",
                                             placeholder="ej. Derrame Km 75+550")
                nuevo_uso    = st.selectbox(
                    "Uso de suelo",
                    ["Agrícola/Forestal", "Industrial", "Residencial"],
                )
                if st.form_submit_button("Guardar proyecto", type="primary"):
                    if nuevo_id and nuevo_nombre:
                        if crear_proyecto_db(nuevo_id, nuevo_nombre, nuevo_uso):
                            st.success("Proyecto creado.")
                            st.rerun()
                    else:
                        st.warning("Completa ID y Nombre del siniestro.")

        st.markdown("---")
        herramienta = st.radio(
            "Herramientas:",
            [
                "📷 Filtro de Fotografías",
                "🔎 Revisión Técnica Ambiental",
                "🧪 Vaciado de Laboratorio",
            ],
            label_visibility="collapsed",
        )
        st.markdown("---")
        st.caption(
            f"⚙️ Motor: `{MODEL_ID}`  \n"
            f"Vision: lotes de {MAX_PAGINAS_VISION} págs · {VISION_DPI} DPI  \n"
            "**v2.4.0 — Fase 3**"
        )
    return herramienta


def check_password() -> bool:
    """
    Corrección 7: guard limpio de autenticación.
    Muestra solo el formulario de login hasta que las credenciales sean válidas.
    Retorna True cuando el usuario está autenticado.
    """
    if st.session_state.get("password_correct"):
        return True

    st.markdown("## 🔒 Hub de Automatización Ambiental")
    st.caption("Acceso restringido — ingresa tus credenciales para continuar.")

    with st.form("login_form"):
        usuario    = st.text_input("Usuario")
        contrasena = st.text_input("Contraseña", type="password")
        submitted  = st.form_submit_button("Entrar")

    if submitted:
        try:
            creds = st.secrets["credenciales"]
            if usuario == creds["usuario"] and contrasena == creds["password"]:
                st.session_state["password_correct"] = True
                st.session_state["usuario_actual"]   = usuario
                st.rerun()
            else:
                st.error("Usuario o contraseña incorrectos.")
        except (KeyError, FileNotFoundError):
            st.error(
                "Credenciales no configuradas. "
                "Agrega en secrets.toml:\n"
                "```\n[credenciales]\nusuario = '...'\npassword = '...'\n```"
            )
    return False


def main() -> None:
    """
    Corrección 1: inicializar session_state ANTES de cualquier render.
    Así ninguna función downstream encuentra KeyError en primera carga.
    """
    # ── Inicialización de estado de sesión ────────────────────────────────
    if "proyecto_actual"  not in st.session_state:
        st.session_state["proyecto_actual"]  = None
    if "nombre_proyecto"  not in st.session_state:
        st.session_state["nombre_proyecto"]  = ""
    if "usuario_actual"   not in st.session_state:
        st.session_state["usuario_actual"]   = "Ingeniero"
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    # ── Guard de autenticación ────────────────────────────────────────────
    if not check_password():
        return

    # ── Inicializar BD  (Corrección 3: retorna bool, no llama st.stop) ────
    if "_db_ok" not in st.session_state:
        db_ok = inicializar_db()
        st.session_state["_db_ok"] = db_ok
        if not db_ok:
            st.error(
                f"⚠️ No se pudo conectar a la base de datos.\n\n"
                f"**Detalle:** {st.session_state.get('_db_error', 'desconocido')}\n\n"
                "Verifica que `DATABASE_URL` en `secrets.toml` sea correcto."
            )
            st.stop()

    # ── Mostrar error de BD si ocurrió en recarga posterior ───────────────
    if not st.session_state.get("_db_ok", True):
        st.error("La base de datos no está disponible. Recarga la página para reintentar.")
        return

    # ── Construir interfaz principal ──────────────────────────────────────
    client      = get_client()
    herramienta = render_sidebar()

    if "Filtro"      in herramienta:
        render_herramienta_fotos(client)
    elif "Revisión"  in herramienta:
        render_herramienta_auditor(
            client          = client,
            model_id        = MODEL_ID,
            proyecto_actual = st.session_state.proyecto_actual,
        )
    elif "Laboratorio" in herramienta:
        render_herramienta_lab(client)


if __name__ == "__main__":
    main()
