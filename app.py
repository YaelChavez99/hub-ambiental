"""
Hub de Automatización Ambiental
================================
Aplicación Streamlit multi-herramienta para empresas de remediación de suelos.
Motor cognitivo: Claude 3.5 Sonnet (Anthropic API).

Herramientas:
  1. Filtro y Etiquetado de Fotografías (Visión)
  2. Auditor de Machotes           (Validación de Consistencia)
  3. Vaciado Automático de Lab     (Parsing y Lógica NOM-138)
  4. Generador Capítulo 5          (Características del Sitio)

Autor  : Hub de Automatización Ambiental
Versión: 1.0.0
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any

import anthropic
import fitz  # PyMuPDF — renderizado de páginas PDF a imagen
import pandas as pd
import pdfplumber  # Sigue en uso para Herramienta 2 (extracción de texto)
import streamlit as st
from PIL import Image

# ---------------------------------------------------------------------------
# Configuración de página (DEBE ser la primera llamada a Streamlit)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Hub de Automatización Ambiental",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Constantes – NOM-138-SEMARNAT/SSA1-2012
# Uso de Suelo Agrícola, Forestal, Pecuario y de Conservación
# ---------------------------------------------------------------------------
NOM_138_LIMITES: dict[str, float] = {
    "HFL": 200.0,        # Hidrocarburos Fracción Ligera  (mg/kg)
    "Benceno": 6.0,
    "Tolueno": 40.0,
    "Etilbenceno": 10.0,
    "Xilenos": 40.0,
}

# ---------------------------------------------------------------------------
# Helpers de API
# ---------------------------------------------------------------------------

def get_client() -> anthropic.Anthropic:
    """Crea y devuelve el cliente de Anthropic usando st.secrets o variables de entorno."""
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError):
        import os
        api_key = os.getenv("ANTHROPIC_API_KEY", "")

    if not api_key:
        st.error(
            "⚠️ No se encontró la clave de API de Anthropic.  \n"
            "Agrégala en `.streamlit/secrets.toml` → `ANTHROPIC_API_KEY = 'sk-...'`  \n"
            "o como variable de entorno `ANTHROPIC_API_KEY`."
        )
        st.stop()
    return anthropic.Anthropic(api_key=api_key)


def image_to_b64(image_bytes: bytes) -> str:
    """Convierte bytes de imagen a cadena Base64."""
    return base64.standard_b64encode(image_bytes).decode("utf-8")


def resize_image_if_needed(image_bytes: bytes, max_px: int = 1_500) -> bytes:
    """
    Redimensiona la imagen si su lado mayor supera max_px.
    Devuelve los bytes JPEG resultantes.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_px:
        ratio = max_px / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """
    Extrae todo el texto del PDF usando pdfplumber.
    Devuelve el texto concatenado de todas las páginas.
    """
    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            text_parts.append(f"\n--- PÁGINA {i} ---\n{page_text}")
    return "\n".join(text_parts)


def extract_pdf_tables(pdf_bytes: bytes) -> list[list[list[str | None]]]:
    """
    Extrae todas las tablas del PDF.
    Devuelve una lista de tablas; cada tabla es lista de filas (lista de celdas).
    """
    all_tables: list[Any] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                all_tables.extend(tables)
    return all_tables


# ---------------------------------------------------------------------------
# HERRAMIENTA 1 – Filtro y Etiquetado de Fotografías
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_VISION = """
Eres un auditor técnico ambiental experto en inspección de sitios contaminados por derrames de hidrocarburos.
Tu tarea es analizar fotografías de campo tomadas durante la atención de un siniestro (derrame de gasolina u otro hidrocarburo).

Para CADA fotografía que recibas, debes:
1. Evaluar si la imagen es ÚTIL o INSERVIBLE para un informe técnico oficial.
   - INSERVIBLE: borrosa, sin contexto claro, duplicada sin valor, imagen de oficina, foto accidental.
   - ÚTIL: muestra el contaminante, el vehículo accidentado, el suelo afectado, el equipo de muestreo, testigos ambientales, etc.

2. Si es ÚTIL, redactar un "Pie de foto técnico" profesional en español, de entre 10 y 20 palabras,
   con lenguaje de informe ambiental (ej.: "Migración superficial del hidrocarburo sobre camino de escorrentía natural, Área Afectada A").

3. Devolver EXCLUSIVAMENTE un objeto JSON con esta estructura (sin markdown, sin texto extra):
{
  "clasificacion": "ÚTIL" | "INSERVIBLE",
  "razon": "Breve justificación en una oración",
  "pie_de_foto": "Texto técnico" | null
}

REGLAS ESTRICTAS:
- No inventes datos que no puedas ver en la imagen.
- Si la imagen es INSERVIBLE, "pie_de_foto" debe ser null.
- Responde ÚNICAMENTE con el JSON. Cero texto adicional.
"""


def analizar_fotografia(client: anthropic.Anthropic, image_bytes: bytes, media_type: str) -> dict:
    """
    Envía una imagen a Claude Vision y retorna el JSON de clasificación.
    """
    b64 = image_to_b64(image_bytes)
    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=512,
        system=SYSTEM_PROMPT_VISION,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Analiza esta fotografía de campo y devuelve el JSON solicitado.",
                    },
                ],
            }
        ],
    )
    raw = message.content[0].text.strip()
    # Limpiar posibles bloques de código markdown
    raw = re.sub(r"```json|```", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "clasificacion": "ERROR",
            "razon": f"No se pudo parsear la respuesta: {raw[:200]}",
            "pie_de_foto": None,
        }


def render_herramienta_fotos(client: anthropic.Anthropic) -> None:
    """Renderiza la Herramienta 1: Filtro y Etiquetado de Fotografías."""
    st.header("📷 Herramienta 1 — Filtro y Etiquetado de Fotografías")
    st.caption(
        "Sube las fotos de campo. Claude evaluará cada una y generará pies de foto técnicos "
        "para las imágenes útiles, descartando las inservibles."
    )

    uploaded_files = st.file_uploader(
        "Selecciona una o varias fotografías",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="uploader_fotos",
    )

    if not uploaded_files:
        st.info("👆 Sube al menos una fotografía para comenzar.")
        return

    if st.button("🔍 Analizar fotografías", type="primary", key="btn_fotos"):
        utiles: list[dict] = []
        inservibles: list[dict] = []

        progress = st.progress(0, text="Analizando imágenes…")
        total = len(uploaded_files)

        for idx, uf in enumerate(uploaded_files):
            raw_bytes = uf.read()
            # Detectar media type
            suffix = Path(uf.name).suffix.lower()
            mt_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                      ".png": "image/png", ".webp": "image/webp"}
            media_type = mt_map.get(suffix, "image/jpeg")

            # Redimensionar para reducir tokens
            try:
                processed = resize_image_if_needed(raw_bytes)
                mt_send = "image/jpeg"
            except Exception:
                processed = raw_bytes
                mt_send = media_type

            resultado = analizar_fotografia(client, processed, mt_send)
            resultado["nombre"] = uf.name
            resultado["bytes_orig"] = raw_bytes

            if resultado.get("clasificacion") == "ÚTIL":
                utiles.append(resultado)
            else:
                inservibles.append(resultado)

            progress.progress((idx + 1) / total, text=f"Procesando {idx + 1}/{total}…")

        progress.empty()

        # --- Resultados ---
        col_u, col_i = st.columns([3, 1])
        col_u.metric("✅ Fotografías útiles", len(utiles))
        col_i.metric("🗑️ Fotografías inservibles", len(inservibles))

        if utiles:
            st.subheader("✅ Fotografías Útiles")
            cols_per_row = 3
            for row_start in range(0, len(utiles), cols_per_row):
                row_items = utiles[row_start : row_start + cols_per_row]
                cols = st.columns(len(row_items))
                for col, item in zip(cols, row_items):
                    with col:
                        st.image(
                            item["bytes_orig"],
                            use_container_width=True,
                        )
                        st.success(f"**{item['nombre']}**")
                        st.markdown(f"🏷️ *{item.get('pie_de_foto', '')}*")
                        with st.expander("Detalle"):
                            st.write(f"**Razón:** {item.get('razon', '')}")

        if inservibles:
            st.subheader("🗑️ Fotografías Descartadas")
            with st.expander("Ver lista de descartadas"):
                for item in inservibles:
                    st.warning(f"**{item['nombre']}** — {item.get('razon', '')}")

        # Exportar pies de foto
        if utiles:
            df_export = pd.DataFrame(
                [
                    {"Archivo": i["nombre"],
                     "Clasificación": i["clasificacion"],
                     "Pie de foto sugerido": i.get("pie_de_foto", ""),
                     "Razón": i.get("razon", "")}
                    for i in utiles
                ]
            )
            csv = df_export.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Descargar pies de foto (CSV)",
                data=csv,
                file_name="pies_de_foto.csv",
                mime="text/csv",
            )


# ---------------------------------------------------------------------------
# HERRAMIENTA 2 – Auditor de Machotes
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_AUDITOR = """
Eres un auditor técnico ambiental senior con 20 años de experiencia revisando informes de caracterización
de sitios contaminados con hidrocarburos en México. Tu especialidad es detectar errores de "copiar y pegar"
y discrepancias entre secciones de un mismo documento.

Se te proporcionará el texto completo de un informe técnico (puede estar dividido en páginas).
Tu tarea tiene DOS fases:

**FASE 1 – EXTRACCIÓN DE ENTIDADES CLAVE**
Identifica y extrae el valor ÚNICO y CORRECTO de cada una de las siguientes entidades tal como
aparece en la sección de Antecedentes o la descripción principal del siniestro:
- Volumen derramado (litros)
- Coordenadas UTM o Geográficas del siniestro
- Área total afectada (m²)
- Volumen total de suelo contaminado (m³)
- Municipio
- Estado
- Km de la autopista (ej. Km 176+500)
- Nombre de la autopista
- Empresa propietaria del vehículo
- Fecha del siniestro
- Número de pozos de muestreo
- Nombre del Responsable Técnico

**FASE 2 – AUDITORÍA DE CONSISTENCIA**
Busca cada entidad extraída en TODO el documento. Reporta ÚNICAMENTE las discrepancias reales:
casos donde el mismo dato aparece con un valor diferente en otra sección (Conclusiones, Plan de
Saneamiento, Tablas, etc.). Ignora variaciones de redacción que sean equivalentes semánticamente.

**FORMATO DE RESPUESTA OBLIGATORIO (JSON estricto, sin markdown):**
{
  "entidades": {
    "volumen_derramado_litros": "<valor>",
    "coordenadas": "<valor>",
    "area_afectada_m2": "<valor>",
    "volumen_suelo_m3": "<valor>",
    "municipio": "<valor>",
    "estado": "<valor>",
    "km_autopista": "<valor>",
    "nombre_autopista": "<valor>",
    "empresa_vehiculo": "<valor>",
    "fecha_siniestro": "<valor>",
    "numero_pozos_muestreo": "<valor>",
    "responsable_tecnico": "<valor>"
  },
  "discrepancias": [
    {
      "entidad": "<nombre del campo>",
      "valor_referencia": "<valor encontrado en Antecedentes>",
      "valor_discrepante": "<valor diferente encontrado en otra sección>",
      "ubicacion_discrepante": "<Sección/Capítulo donde aparece el error>",
      "gravedad": "ALTA" | "MEDIA" | "BAJA",
      "recomendacion": "<qué debe corregir el redactor>"
    }
  ],
  "resumen": {
    "total_discrepancias": <número>,
    "estado_general": "APROBADO" | "OBSERVACIONES MENORES" | "REQUIERE CORRECCIÓN",
    "comentario_general": "<párrafo de diagnóstico>"
  }
}

REGLAS ESTRICTAS:
- No inventes discrepancias. Solo reporta las que están realmente presentes en el texto.
- Si no hay discrepancias, devuelve "discrepancias": [].
- Responde ÚNICAMENTE con el JSON. Cero texto adicional fuera del JSON.
"""


def auditar_informe(client: anthropic.Anthropic, texto_pdf: str) -> dict:
    """Envía el texto del PDF a Claude y retorna el reporte de auditoría."""
    # Limitar a ~180k caracteres para no exceder ventana de contexto
    texto_truncado = texto_pdf[:180_000]

    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=4096,
        system=SYSTEM_PROMPT_AUDITOR,
        messages=[
            {
                "role": "user",
                "content": (
                    "A continuación el texto completo del informe técnico a auditar. "
                    "Realiza la auditoría completa y devuelve únicamente el JSON solicitado.\n\n"
                    f"{texto_truncado}"
                ),
            }
        ],
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r"```json|```", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "entidades": {},
            "discrepancias": [],
            "resumen": {
                "total_discrepancias": 0,
                "estado_general": "ERROR",
                "comentario_general": f"No se pudo parsear la respuesta: {raw[:300]}",
            },
        }


def render_herramienta_auditor(client: anthropic.Anthropic) -> None:
    """Renderiza la Herramienta 2: Auditor de Machotes."""
    st.header("🔎 Herramienta 2 — Auditor de Machotes")
    st.caption(
        "Sube el PDF del informe preliminar. Claude extraerá las entidades clave y detectará "
        "discrepancias por errores de copiar-pegar entre secciones."
    )

    uploaded_pdf = st.file_uploader(
        "Selecciona el PDF del informe",
        type=["pdf"],
        key="uploader_auditor",
    )

    if not uploaded_pdf:
        st.info("👆 Sube el PDF del informe para comenzar la auditoría.")
        return

    if st.button("🔍 Auditar informe", type="primary", key="btn_auditor"):
        with st.spinner("Extrayendo texto del PDF…"):
            texto = extract_pdf_text(uploaded_pdf.read())

        if len(texto.strip()) < 100:
            st.error("No se pudo extraer texto del PDF. ¿Es un PDF escaneado sin OCR?")
            return

        st.caption(f"📄 Texto extraído: {len(texto):,} caracteres")

        with st.spinner("Claude está auditando el informe… (puede tardar 30-60 seg)"):
            resultado = auditar_informe(client, texto)

        # --- Resumen general ---
        resumen = resultado.get("resumen", {})
        estado = resumen.get("estado_general", "DESCONOCIDO")
        color_map = {
            "APROBADO": "success",
            "OBSERVACIONES MENORES": "warning",
            "REQUIERE CORRECCIÓN": "error",
            "ERROR": "error",
        }
        getattr(st, color_map.get(estado, "info"))(
            f"**Estado general: {estado}** — {resumen.get('comentario_general', '')}"
        )
        st.metric("Total de discrepancias detectadas", resumen.get("total_discrepancias", 0))

        # --- Entidades clave ---
        st.subheader("📋 Entidades Clave Extraídas")
        entidades = resultado.get("entidades", {})
        if entidades:
            df_ent = pd.DataFrame(
                [(k.replace("_", " ").title(), v) for k, v in entidades.items()],
                columns=["Campo", "Valor"],
            )
            st.dataframe(df_ent, use_container_width=True, hide_index=True)
        else:
            st.warning("No se pudieron extraer entidades del documento.")

        # --- Discrepancias ---
        discrepancias = resultado.get("discrepancias", [])
        st.subheader(f"⚠️ Discrepancias Encontradas ({len(discrepancias)})")

        if not discrepancias:
            st.success("✅ No se detectaron discrepancias. El informe es consistente.")
        else:
            for disc in discrepancias:
                gravedad = disc.get("gravedad", "MEDIA")
                icon = {"ALTA": "🔴", "MEDIA": "🟡", "BAJA": "🟢"}.get(gravedad, "⚪")
                with st.expander(
                    f"{icon} [{gravedad}] {disc.get('entidad', '').replace('_', ' ').title()}",
                    expanded=(gravedad == "ALTA"),
                ):
                    col1, col2 = st.columns(2)
                    col1.markdown(f"**Valor correcto (referencia):** \n`{disc.get('valor_referencia', '')}`")
                    col2.markdown(f"**Valor discrepante encontrado:** \n`{disc.get('valor_discrepante', '')}`")
                    st.markdown(f"**Ubicación del error:** {disc.get('ubicacion_discrepante', '')}")
                    st.info(f"💡 **Recomendación:** {disc.get('recomendacion', '')}")

        # --- Botón de descarga ---
        st.download_button(
            "⬇️ Descargar reporte completo (JSON)",
            data=json.dumps(resultado, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name="reporte_auditoria.json",
            mime="application/json",
        )


# ---------------------------------------------------------------------------
# HERRAMIENTA 3 – Vaciado Automático de Laboratorio  (Vision API)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_LAB = """
Eres un químico ambiental experto en lectura e interpretación de reportes de laboratorio
para suelos contaminados con hidrocarburos en México, con amplia experiencia en documentos
de LABSA y laboratorios acreditados ante la EMA y la PROFEPA.

Se te enviarán UNA O VARIAS imágenes de páginas de un reporte de laboratorio. El documento
puede ser un PDF escaneado, un formato impreso fotografiado, o incluso tablas llenadas
a mano con pluma. Tu capacidad de visión debe superar cualquier dificultad de legibilidad.

TAREA PRINCIPAL:
Leer visualmente TODAS las imágenes recibidas, identificar las tablas de resultados de muestras
de suelo y extraer CADA fila de muestra con los siguientes campos:
  - zona          : sección o área afectada (ej. "A-1", "B-2", "Periferia Zona A")
  - punto         : identificador del pozo de muestreo (ej. "P1", "FA", "F9-DUP")
  - profundidad_m : profundidad de la muestra en metros (número decimal, ej. 0.50)
  - HFL           : Hidrocarburos Fracción Ligera en mg/kg (número decimal)
  - Benceno       : concentración en mg/kg (número decimal)
  - Tolueno       : concentración en mg/kg (número decimal)
  - Etilbenceno   : concentración en mg/kg (número decimal)
  - Xilenos       : concentración en mg/kg (número decimal)
  - pH            : valor de pH (número decimal)
  - Humedad_pct   : porcentaje de humedad (número decimal)
  - notas         : texto libre para aclaraciones (ej. "< L.C.", "duplicado", "muestra dañada")

REGLAS DE INTERPRETACIÓN:
1. Los valores escritos como "< L.C.", "<LC", "< l.c.", "BDL", "ND" o similares indican
   que la concentración está por debajo del límite cuantificable. En el campo numérico
   escribe 0.0 y en "notas" escribe "< L.C.".
2. Si un campo no es legible o no aparece en la tabla, usa null (no inventes valores).
3. Las muestras duplicadas se identifican con sufijo "-DUP" en el punto; inclúyelas como
   filas separadas.
4. Si hay varias tablas distribuidas en distintas páginas, consolida TODAS las filas en
   un único array JSON sin omitir ninguna.
5. Ignora encabezados repetidos, filas de totales, filas de límites máximos permisibles
   y cualquier fila que no sea un resultado de muestra.

LÍMITES NOM-138-SEMARNAT/SSA1-2012 (solo para tu referencia interna, no los incluyas
en la salida):
  HFL: 200.0 mg/kg | Benceno: 6.0 | Tolueno: 40.0 | Etilbenceno: 10.0 | Xilenos: 40.0

FORMATO DE RESPUESTA OBLIGATORIO — JSON array puro, sin markdown, sin texto adicional:
[
  {
    "zona": "A-1",
    "punto": "P1",
    "profundidad_m": 0.50,
    "HFL": 7550.52,
    "Benceno": 8.45,
    "Tolueno": 48.25,
    "Etilbenceno": 17.52,
    "Xilenos": 58.17,
    "pH": 6.98,
    "Humedad_pct": 20.88,
    "notes": ""
  }
]

RESPONDE ÚNICAMENTE CON EL JSON ARRAY. CERO TEXTO ANTES O DESPUÉS DEL ARRAY.
"""

# Resolución de renderizado (DPI). 150 es el balance óptimo calidad/tokens.
# Aumentar a 200 si el documento tiene letra muy pequeña o escritura a mano apretada.
_PDF_RENDER_DPI: int = 150
# Límite de páginas por llamada API para no exceder la ventana de contexto.
# Con DPI=150, cada página consume ~1,600 tokens; 20 págs ≈ 32k tokens de imagen.
_MAX_PAGES_PER_CALL: int = 20


def pdf_a_imagenes(pdf_bytes: bytes, dpi: int = _PDF_RENDER_DPI) -> list[bytes]:
    """
    Convierte cada página del PDF en una imagen JPEG usando PyMuPDF (fitz).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    paginas_jpeg: list[bytes] = []

    zoom = dpi / 72.0
    matriz = fitz.Matrix(zoom, zoom)

    for num_pag in range(len(doc)):
        page = doc[num_pag]
        pix = page.get_pixmap(matrix=matriz, alpha=False)
        jpeg_bytes = pix.tobytes(output="jpeg", jpg_quality=88)
        paginas_jpeg.append(jpeg_bytes)

    doc.close()
    return paginas_jpeg


def _construir_contenido_vision(paginas_jpeg: list[bytes], num_lote: int, total_lotes: int) -> list[dict]:
    """
    Constuye la lista de bloques de contenido para la API multimodal de Claude.
    """
    contenido: list[dict] = [
        {
            "type": "text",
            "text": (
                f"A continuación están las imágenes de las páginas del reporte de laboratorio "
                f"(lote {num_lote} de {total_lotes}). Lee TODAS las tablas visibles y extrae "
                f"cada fila de muestra de suelo. Devuelve ÚNICAMENTE el JSON array solicitado."
            ),
        }
    ]

    for i, jpeg_bytes in enumerate(paginas_jpeg, start=1):
        b64 = base64.standard_b64encode(jpeg_bytes).decode("utf-8")
        contenido.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64,
            },
        })
        contenido.append({
            "type": "text",
            "text": f"[Página {i} del lote {num_lote}]",
        })

    return contenido


def parsear_resultados_lab(client: anthropic.Anthropic, paginas_jpeg: list[bytes]) -> list[dict]:
    """
    Envía las páginas del PDF como imágenes a Claude Vision y retorna los datos estructurados.
    """
    todas_las_filas: list[dict] = []

    lotes = [
        paginas_jpeg[i : i + _MAX_PAGES_PER_CALL]
        for i in range(0, len(paginas_jpeg), _MAX_PAGES_PER_CALL)
    ]
    total_lotes = len(lotes)

    for num_lote, lote in enumerate(lotes, start=1):
        contenido = _construir_contenido_vision(lote, num_lote, total_lotes)

        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=8192,
            system=SYSTEM_PROMPT_LAB,
            messages=[{"role": "user", "content": contenido}],
        )

        raw = message.content[0].text.strip()
        raw = re.sub(r"```json|```", "", raw).strip()

        try:
            filas_lote = json.loads(raw)
            if isinstance(filas_lote, list):
                todas_las_filas.extend(filas_lote)
            else:
                st.warning(f"Lote {num_lote}: la respuesta no era un array JSON. Se omitió.")
        except json.JSONDecodeError:
            st.warning(
                f"⚠️ Lote {num_lote}/{total_lotes}: no se pudo parsear la respuesta. "
                f"Fragmento recibido: `{raw[:300]}`"
            )

    return todas_las_filas


def highlight_exceedances(df: pd.DataFrame) -> pd.DataFrame.style:  # type: ignore[type-arg]
    """
    Aplica estilo condicional: rojo si supera el límite, naranja si está cerca (80-100%).
    """
    def color_cell(val: Any, limit: float) -> str:
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        if v > limit:
            return "background-color: #FF4B4B; color: white; font-weight: bold;"
        if v > limit * 0.8:
            return "background-color: #FFA500; color: black;"
        return "background-color: #21BA45; color: white;"

    col_limits = {
        "HFL": NOM_138_LIMITES["HFL"],
        "Benceno": NOM_138_LIMITES["Benceno"],
        "Tolueno": NOM_138_LIMITES["Tolueno"],
        "Etilbenceno": NOM_138_LIMITES["Etilbenceno"],
        "Xilenos": NOM_138_LIMITES["Xilenos"],
    }

    styler = df.style
    for col, limit in col_limits.items():
        if col in df.columns:
            styler = styler.map(lambda v, lim=limit: color_cell(v, lim), subset=[col])
    return styler


def render_herramienta_lab(client: anthropic.Anthropic) -> None:
    """Renderiza la Herramienta 3: Vaciado Automático de Laboratorio (Vision API)."""
    st.header("🧪 Herramienta 3 — Vaciado Automático de Laboratorio")
    st.caption(
        "Sube el PDF del laboratorio — puede ser escaneado o con datos escritos a mano. "
        "Claude lo leerá visualmente y estructurará los datos contra los LMP de la NOM-138."
    )

    with st.expander("📏 Límites NOM-138 (Uso Agrícola/Forestal/Pecuario/Conservación)", expanded=False):
        df_lim = pd.DataFrame(
            [(k, f"{v} mg/kg") for k, v in NOM_138_LIMITES.items()],
            columns=["Parámetro", "Límite Máximo Permisible"],
        )
        st.dataframe(df_lim, hide_index=True, use_container_width=True)

    with st.expander("⚙️ Configuración avanzada", expanded=False):
        dpi = st.slider(
            "Resolución de renderizado (DPI)",
            min_value=100,
            max_value=250,
            value=150,
            step=25,
            help=(
                "150 DPI es ideal para PDFs impresos estándar. "
                "Aumenta a 200-250 si el documento tiene letra pequeña o escritura a mano."
            ),
        )

    uploaded_pdf = st.file_uploader(
        "Selecciona el PDF del laboratorio (impreso, escaneado o manuscrito)",
        type=["pdf"],
        key="uploader_lab",
    )

    if not uploaded_pdf:
        st.info("👆 Sube el PDF del laboratorio para comenzar el análisis.")
        return

    if st.button("🧬 Procesar resultados", type="primary", key="btn_lab"):
        pdf_bytes = uploaded_pdf.read()

        with st.spinner("Convirtiendo páginas del PDF a imágenes…"):
            try:
                paginas_jpeg = pdf_a_imagenes(pdf_bytes, dpi=dpi)
            except Exception as e:
                st.error(f"Error al procesar el PDF con PyMuPDF: {e}")
                return

        num_paginas = len(paginas_jpeg)
        st.caption(
            f"📄 {num_paginas} página(s) detectada(s) · "
            f"DPI: {dpi} · "
            f"Lotes de envío: {((num_paginas - 1) // _MAX_PAGES_PER_CALL) + 1}"
        )

        if num_paginas > 0:
            with st.expander(f"🔍 Previsualizar páginas ({min(num_paginas, 5)} de {num_paginas})", expanded=False):
                preview_cols = st.columns(min(num_paginas, 5))
                for idx, col in enumerate(preview_cols):
                    col.image(paginas_jpeg[idx], caption=f"Pág. {idx + 1}", use_container_width=True)

        lotes_totales = ((num_paginas - 1) // _MAX_PAGES_PER_CALL) + 1
        progress_bar = st.progress(0, text="Enviando imágenes a Claude Vision…")

        todas_las_filas: list[dict] = []
        lotes = [
            paginas_jpeg[i : i + _MAX_PAGES_PER_CALL]
            for i in range(0, num_paginas, _MAX_PAGES_PER_CALL)
        ]

        for num_lote, lote in enumerate(lotes, start=1):
            progress_bar.progress(
                num_lote / lotes_totales,
                text=f"Claude Vision procesando lote {num_lote}/{lotes_totales}…",
            )
            contenido = _construir_contenido_vision(lote, num_lote, lotes_totales)
            message = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=8192,
                system=SYSTEM_PROMPT_LAB,
                messages=[{"role": "user", "content": contenido}],
            )
            raw = message.content[0].text.strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            try:
                filas_lote = json.loads(raw)
                if isinstance(filas_lote, list):
                    todas_las_filas.extend(filas_lote)
                else:
                    st.warning(f"Lote {num_lote}: respuesta inesperada (no es array). Se omitió.")
            except json.JSONDecodeError:
                st.warning(
                    f"⚠️ Lote {num_lote}/{lotes_totales}: no se pudo parsear. "
                    f"Fragmento: `{raw[:200]}`"
                )

        progress_bar.empty()

        if not todas_las_filas:
            st.error("No se encontraron datos de muestras en el PDF.")
            return

        df = pd.DataFrame(todas_las_filas)

        numeric_cols = ["HFL", "Benceno", "Tolueno", "Etilbenceno", "Xilenos", "pH", "Humedad_pct", "profundidad_m"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        st.subheader("📊 Resumen de Resultados")
        st.success(f"✅ Se extrajeron **{len(df)} muestras** de {num_paginas} página(s).")

        exceedances: dict[str, int] = {}
        for param, limit in NOM_138_LIMITES.items():
            if param in df.columns:
                count = int((df[param] > limit).sum())
                exceedances[param] = count

        cols = st.columns(len(exceedances))
        for col_widget, (param, count) in zip(cols, exceedances.items()):
            col_widget.metric(
                label=f"{param} > LMP",
                value=count,
                delta=f"LMP: {NOM_138_LIMITES[param]} mg/kg",
                delta_color="inverse",
            )

        st.subheader("📋 Tabla de Resultados (valores fuera de norma en 🔴)")

        leyenda_cols = st.columns(3)
        leyenda_cols[0].markdown("🔴 **Supera el LMP**")
        leyenda_cols[1].markdown("🟠 **80-100% del LMP**")
        leyenda_cols[2].markdown("🟢 **Dentro de norma**")

        styled = highlight_exceedances(df)
        st.dataframe(styled, use_container_width=True, hide_index=True)

        param_cols = [p for p in NOM_138_LIMITES.keys() if p in df.columns]
        if param_cols:
            mask = df[param_cols].apply(
                lambda col: col > NOM_138_LIMITES.get(col.name, float("inf"))
            ).any(axis=1)
            df_exceedances = df[mask]

            if not df_exceedances.empty:
                st.subheader(f"🚨 Muestras Fuera de Norma ({len(df_exceedances)} muestras)")
                st.dataframe(
                    highlight_exceedances(df_exceedances),
                    use_container_width=True,
                    hide_index=True,
                )

        col_csv, col_excel = st.columns(2)
        with col_csv:
            st.download_button(
                "⬇️ Descargar CSV",
                data=df.to_csv(index=False).encode("utf-8"),
                file_name="resultados_laboratorio.csv",
                mime="text/csv",
            )
        with col_excel:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Resultados")
            st.download_button(
                "⬇️ Descargar Excel",
                data=buf.getvalue(),
                file_name="resultados_laboratorio.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


# ---------------------------------------------------------------------------
# HERRAMIENTA 4 – Generador del Capítulo 5
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_CAP5 = """
Eres un redactor técnico ambiental senior especializado en elaboración de estudios ambientales
para la Agencia de Seguridad, Energía y Ambiente (ASEA) y la SEMARNAT en México.

Tu escritura es precisa, formal y técnica, estructurada como los informes de caracterización
de sitios contaminados. Usas datos reales de INEGI, CONAGUA, CONAFOR, CONABIO y SMN.

Se te proporcionará: Municipio, Estado y Coordenadas del sitio afectado.

Redacta el **Capítulo 5 – Características Generales del Sitio Contaminado** con los siguientes sub-apartados.
Para cada sub-apartado, escribe entre 3 y 6 párrafos técnicos con datos realistas del municipio indicado.
Incluye, donde sea pertinente, menciones a cuerpos de agua, acuíferos (CONAGUA), tipos de clima (Köppen),
tipos de suelo (FAO/WRB), vegetación (INFyS/CONABIO), fauna (SEMARNAT), y datos poblacionales (INEGI 2020).

**ESTRUCTURA OBLIGATORIA:**

## 5. CARACTERÍSTICAS GENERALES DEL SITIO CONTAMINADO

### 5.1 Localización del Sitio
[Descripción de ubicación con municipio, estado, coordenadas, colindancias geográficas]

### 5.2 Orografía y Geología
[Tipo de relieve según INEGI, tipo de roca, características del piedemonte o terreno]

### 5.3 Hidrografía e Hidrología
[Cuenca hidrológica, subcuenca, cuerpos de agua superficiales y subterráneos, acuífero de CONAGUA]

### 5.4 Clima
[Tipo de clima Köppen, temperatura media anual, precipitación media anual, temporada de lluvias]

### 5.5 Flora
[Tipos de vegetación predominante, géneros dominantes, vegetación secundaria en el sitio]

### 5.6 Fauna
[Fauna reportada en la región, especies bajo protección NOM-059, fauna observada en el sitio]

### 5.7 Clasificación y Uso de Suelo
[Tipo de suelo FAO, uso actual, clasificación según NOM-138-SEMARNAT/SSA1-2012]

### 5.8 Edafología
[Tipo edafológico (INEGI), características físicas: textura, permeabilidad, profundidad]

### 5.9 Población
[Datos poblacionales INEGI 2020 del municipio, localidades cercanas al sitio]

### 5.10 Economía
[Actividades económicas principales: agricultura, ganadería, comercio, industria]

REGLAS ESTRICTAS:
- Escribe siempre en tercera persona y tiempo presente.
- No inventes coordenadas específicas ni datos que claramente no correspondan al municipio dado.
- Cuando menciones datos numéricos de fuentes oficiales (población, temperaturas, etc.),
  indícalos como valores típicos o referenciales del municipio.
- Usa terminología técnica ambiental mexicana.
- NO uses bullet points; redacta en prosa continua dentro de cada sub-apartado.
- Extensión total esperada: entre 1,500 y 3,000 palabras.
"""


def generar_capitulo_5(
    client: anthropic.Anthropic,
    municipio: str,
    estado: str,
    coordenadas: str,
    km_autopista: str,
    nombre_autopista: str,
    contaminante: str,
) -> str:
    """Genera el texto del Capítulo 5 usando Claude."""
    prompt_usuario = (
        f"Genera el Capítulo 5 completo para el informe de caracterización del siguiente sitio:\n\n"
        f"- **Municipio:** {municipio}\n"
        f"- **Estado:** {estado}\n"
        f"- **Coordenadas del siniestro:** {coordenadas}\n"
        f"- **Km de la autopista:** {km_autopista}\n"
        f"- **Nombre de la autopista:** {nombre_autopista}\n"
        f"- **Contaminante derramado:** {contaminante}\n\n"
        "Redacta el capítulo completo siguiendo la estructura y el formato indicados en el system prompt."
    )

    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=8192,
        system=SYSTEM_PROMPT_CAP5,
        messages=[{"role": "user", "content": prompt_usuario}],
    )
    return message.content[0].text


def render_herramienta_cap5(client: anthropic.Anthropic) -> None:
    """Renderiza la Herramienta 4: Generador del Capítulo 5."""
    st.header("📝 Herramienta 4 — Generador del Capítulo 5")
    st.caption(
        "Ingresa los datos del sitio. Claude redactará un borrador profesional del Capítulo 5 "
        "(Características Generales del Sitio) listo para integrar al informe."
    )

    with st.form("form_cap5"):
        col1, col2 = st.columns(2)
        with col1:
            municipio = st.text_input(
                "Municipio *",
                placeholder="ej. La Huacana",
                help="Municipio donde ocurrió el siniestro",
            )
            estado = st.text_input(
                "Estado *",
                placeholder="ej. Michoacán",
            )
            coordenadas = st.text_input(
                "Coordenadas del siniestro *",
                placeholder="ej. 18°47'44.8\"N, 102°03'34.3\"O",
            )
        with col2:
            km_autopista = st.text_input(
                "Km de la autopista",
                placeholder="ej. Km 176+500",
                value="N/A",
            )
            nombre_autopista = st.text_input(
                "Nombre de la autopista / vialidad",
                placeholder="ej. Autopista Siglo XXI Morelia – Lázaro Cárdenas",
                value="N/A",
            )
            contaminante = st.selectbox(
                "Contaminante derramado",
                options=[
                    "Gasolina Premium",
                    "Gasolina Regular",
                    "Diésel",
                    "Combustóleo",
                    "Petróleo crudo",
                    "Otro hidrocarburo",
                ],
            )

        submitted = st.form_submit_button("✍️ Generar Capítulo 5", type="primary")

    if submitted:
        if not municipio or not estado or not coordenadas:
            st.error("Por favor completa los campos obligatorios: Municipio, Estado y Coordenadas.")
            return

        with st.spinner(
            f"Redactando Capítulo 5 para {municipio}, {estado}… (puede tardar 30-60 seg)"
        ):
            texto = generar_capitulo_5(
                client,
                municipio=municipio,
                estado=estado,
                coordenadas=coordenadas,
                km_autopista=km_autopista,
                nombre_autopista=nombre_autopista,
                contaminante=contaminante,
            )

        st.success("✅ Capítulo 5 generado correctamente.")
        st.subheader(f"Borrador — Capítulo 5: {municipio}, {estado}")

        st.markdown(texto)

        st.download_button(
            "⬇️ Descargar borrador (.txt)",
            data=texto.encode("utf-8"),
            file_name=f"capitulo5_{municipio.replace(' ', '_')}_{estado.replace(' ', '_')}.txt",
            mime="text/plain",
        )

        st.download_button(
            "⬇️ Descargar borrador (.md)",
            data=texto.encode("utf-8"),
            file_name=f"capitulo5_{municipio.replace(' ', '_')}_{estado.replace(' ', '_')}.md",
            mime="text/markdown",
        )


# ---------------------------------------------------------------------------
# Sidebar y Navegación Principal
# ---------------------------------------------------------------------------

def render_sidebar() -> str:
    """Renderiza el sidebar y retorna la herramienta seleccionada."""
    with st.sidebar:
        st.markdown("## 🌿 Hub Ambiental")
        st.markdown("---")

        st.markdown("### 🛠️ Herramientas")
        herramienta = st.radio(
            label="Selecciona una herramienta:",
            options=[
                "📷  Filtro de Fotografías",
                "🔎  Auditor de Machotes",
                "🧪  Vaciado de Laboratorio",
                "📝  Generador Cap. 5",
            ],
            label_visibility="collapsed",
        )

        st.markdown("---")
        st.markdown("### 📏 NOM-138 — LMP (mg/kg)")
        for param, limit in NOM_138_LIMITES.items():
            st.markdown(f"- **{param}:** {limit}")

        st.markdown("---")
        st.caption(
            "⚙️ Motor: Claude 3.5 Sonnet  \n"
            "📜 NOM-138-SEMARNAT/SSA1-2012  \n"
            "v1.0.0"
        )

    return herramienta


# ---------------------------------------------------------------------------
# Sistema de Autenticación (Login)
# ---------------------------------------------------------------------------
def check_password() -> bool:
    """Retorna True si el usuario ingresó las credenciales correctas."""
    
    def password_entered():
        if (st.session_state["username"] == st.secrets["credenciales"]["usuario"]
            and st.session_state["password"] == st.secrets["credenciales"]["password"]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.markdown("## 🔒 Acceso Restringido")
        st.markdown("Por favor, ingresa tus credenciales para usar el Hub Ambiental.")
        st.text_input("Usuario", key="username")
        st.text_input("Contraseña", type="password", key="password")
        st.button("Entrar", on_click=password_entered)
        return False
    
    elif not st.session_state["password_correct"]:
        st.markdown("## 🔒 Acceso Restringido")
        st.text_input("Usuario", key="username")
        st.text_input("Contraseña", type="password", key="password")
        st.button("Entrar", on_click=password_entered)
        st.error("😕 Usuario o contraseña incorrectos")
        return False
    
    else:
        return True
    
def main() -> None:
    """Punto de entrada principal de la aplicación."""
    client = get_client()
    herramienta = render_sidebar()

    if "Filtro" in herramienta:
        render_herramienta_fotos(client)
    elif "Auditor" in herramienta:
        render_herramienta_auditor(client)
    elif "Laboratorio" in herramienta:
        render_herramienta_lab(client)
    elif "Cap" in herramienta:
        render_herramienta_cap5(client)


if __name__ == "__main__":
    if check_password():
        main()
