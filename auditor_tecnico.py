"""
Módulo: auditor_tecnico.py
Herramienta 2 — Revisión Técnica Ambiental

Responsabilidad única: recibir el texto de un informe preliminar de
caracterización de sitio contaminado y producir:
  1. Extracción de entidades clave
  2. Detección de discrepancias (copy-paste entre secciones)
  3. Vacíos regulatorios ante SEMARNAT / ASEA / PROFEPA
  4. Debilidades técnicas por sección
  5. Índice de Calidad Técnica del Informe (ICTI 0-100)

No depende de app.py — se importa como módulo.
Compatible con: Python 3.10+, anthropic>=0.28, streamlit>=1.35
"""

from __future__ import annotations

import io
import json
import re
from typing import Any

import anthropic
import pdfplumber
import streamlit as st

# ---------------------------------------------------------------------------
# Constantes de visualización
# ---------------------------------------------------------------------------
_ICTI_COLORES = {
    "APROBADO":     ("#1a7a1a", "#d4edda", "🟢"),
    "OBSERVACIONES":("#856404", "#fff3cd", "🟡"),
    "DEFICIENTE":   ("#7d3c00", "#fde8d0", "🟠"),
    "RECHAZABLE":   ("#7b0000", "#f8d7da", "🔴"),
}

_GRAVEDAD_COLORES = {
    "ALTA":       ("#cc0000", "#ffcccc"),
    "MEDIA":      ("#7d5a00", "#fff0b3"),
    "BAJA":       ("#1a5c1a", "#d6f0d6"),
    "BLOQUEANTE": ("#cc0000", "#ffcccc"),
    "IMPORTANTE": ("#7d5a00", "#fff0b3"),
    "MENOR":      ("#1a5c1a", "#d6f0d6"),
}

# ---------------------------------------------------------------------------
# System Prompt del Auditor
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_AUDITOR = """
Eres un auditor técnico ambiental senior con 20 años de experiencia revisando
informes de caracterización de sitios contaminados con hidrocarburos en México,
para presentación ante SEMARNAT, ASEA y PROFEPA.

Tu especialidad es detectar:
1. Errores de "copiar y pegar" donde el mismo dato aparece con valores distintos
   en diferentes secciones del mismo documento.
2. Vacíos de información que generarían observaciones o rechazos por parte
   de las autoridades ambientales mexicanas.
3. Secciones técnicamente débiles, ambiguas o con redacción insuficiente.

Se te proporcionará el texto completo de un informe técnico (puede estar
dividido en páginas con marcadores "--- PÁGINA N ---").

REALIZA EL SIGUIENTE ANÁLISIS EN 5 FASES:

═══ FASE 1: EXTRACCIÓN DE ENTIDADES CLAVE ═══
Identifica el valor ÚNICO y CORRECTO de cada entidad, tomando como referencia
la sección de Antecedentes o la descripción principal del siniestro.
Si no encuentras un valor claro, usa null.

═══ FASE 2: AUDITORÍA DE CONSISTENCIA ═══
Busca CADA entidad extraída en TODO el documento.
Reporta ÚNICAMENTE discrepancias REALES donde el mismo dato aparece con
un valor DIFERENTE en otra sección.
NO reportes variaciones de redacción equivalentes (ej: "32,077 L" y "32.077 litros"
son equivalentes). Solo reporta diferencias numéricas o de hecho real.

═══ FASE 3: VACÍOS REGULATORIOS ═══
Identifica información que FALTA y que la autoridad ambiental exigiría.
Considera los requisitos de la NOM-138-SEMARNAT/SSA1-2012 y las guías de
caracterización de ASEA para derrames en carretera.
Clasifica cada vacío por la autoridad que lo observaría y su criticidad.

═══ FASE 4: DEBILIDADES TÉCNICAS ═══
Evalúa la solidez técnica de cada sección del informe.
Identifica redacciones vagas, conclusiones sin sustento en datos,
metodologías no especificadas, o interpretaciones incorrectas.

═══ FASE 5: ÍNDICE DE CALIDAD TÉCNICA (ICTI) ═══
Calcula el ICTI de 0 a 100 con los siguientes pesos:
- consistencia_datos (25 pts): resta 5 pts por cada discrepancia ALTA,
  3 pts por MEDIA, 1 pt por BAJA. Mínimo 0.
- completitud_regulatoria (30 pts): resta 10 pts por cada vacío BLOQUEANTE,
  5 pts por IMPORTANTE, 2 pts por MENOR. Mínimo 0.
- solidez_tecnica (30 pts): promedio de la evaluación de cada sección (0-5 pts c/u),
  escalado a 30 pts. Secciones: Caracterización, Afectaciones, Metodología,
  Resultados, Riesgos, Conclusiones, Recomendaciones.
- formato_presentacion (15 pts): evalúa estructura general, índice, numeración,
  referencias normativas citadas. Escala 0-15.

FORMATO DE RESPUESTA OBLIGATORIO — JSON puro, sin markdown, sin texto adicional:
{
  "entidades": {
    "numero_informe":          "<valor o null>",
    "fecha_siniestro":         "<valor o null>",
    "fecha_muestreo":          "<valor o null>",
    "municipio":               "<valor o null>",
    "estado":                  "<valor o null>",
    "km_autopista":            "<valor o null>",
    "nombre_autopista":        "<valor o null>",
    "volumen_derramado_litros":"<valor o null>",
    "contaminante":            "<valor o null>",
    "coordenadas_siniestro":   "<valor o null>",
    "area_afectada_m2":        "<valor o null>",
    "volumen_suelo_m3":        "<valor o null>",
    "numero_pozos_muestreo":   "<valor o null>",
    "empresa_vehiculo":        "<valor o null>",
    "responsable_tecnico":     "<valor o null>",
    "uso_de_suelo":            "<valor o null>",
    "tipo_muestreo":           "<valor o null>"
  },
  "discrepancias": [
    {
      "entidad":             "<nombre del campo>",
      "valor_referencia":    "<valor correcto de Antecedentes>",
      "valor_discrepante":   "<valor diferente encontrado>",
      "seccion_referencia":  "<sección donde está el valor correcto>",
      "seccion_error":       "<sección donde está el valor incorrecto>",
      "gravedad":            "ALTA|MEDIA|BAJA",
      "recomendacion":       "<qué debe corregir el redactor>"
    }
  ],
  "vacios_regulatorios": [
    {
      "seccion_afectada":       "<nombre de la sección>",
      "informacion_faltante":   "<qué falta exactamente>",
      "autoridad":              "SEMARNAT|ASEA|PROFEPA|Estatal",
      "criticidad":             "BLOQUEANTE|IMPORTANTE|MENOR",
      "recomendacion":          "<cómo subsanar el vacío>"
    }
  ],
  "debilidades_tecnicas": [
    {
      "seccion":     "<nombre de la sección evaluada>",
      "tipo":        "INSUFICIENTE|AMBIGUO|CONTRADICTORIO|INCOMPLETO",
      "descripcion": "<qué está mal o falta>",
      "sugerencia":  "<cómo mejorar la redacción o el contenido>"
    }
  ],
  "icti": {
    "puntaje_total":              <número 0-100>,
    "consistencia_datos":         <número 0-25>,
    "completitud_regulatoria":    <número 0-30>,
    "solidez_tecnica":            <número 0-30>,
    "formato_presentacion":       <número 0-15>,
    "nivel":                      "APROBADO|OBSERVACIONES|DEFICIENTE|RECHAZABLE",
    "comentario_ejecutivo":       "<párrafo de diagnóstico general para el director>"
  }
}

REGLAS ESTRICTAS:
- NO inventes discrepancias. Solo reporta las que están textualmente en el documento.
- Si no hay discrepancias, devuelve "discrepancias": [].
- Si no hay vacíos regulatorios evidentes, devuelve "vacios_regulatorios": [].
- El ICTI debe reflejar fielmente la calidad real del documento.
- Responde ÚNICAMENTE con el JSON. CERO texto fuera del JSON.
"""

# ---------------------------------------------------------------------------
# Extracción de texto del PDF del informe
# ---------------------------------------------------------------------------

def _extraer_texto_informe(pdf_bytes: bytes) -> str:
    """
    Extrae el texto completo del PDF del informe.
    A diferencia del módulo de laboratorio, NO filtra páginas —
    el informe puede tener datos relevantes en cualquier sección.
    """
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            parts.append(f"\n--- PÁGINA {i}/{total} ---\n{text}")
    return "\n".join(parts)


def _tiene_texto_suficiente(texto: str) -> bool:
    """Verifica que el PDF tiene texto extraíble suficiente para analizar."""
    chars_utiles = len(texto.replace(" ", "").replace("\n", "").replace("-", ""))
    return chars_utiles >= 2_000


# ---------------------------------------------------------------------------
# Llamada a Claude — análisis del informe
# ---------------------------------------------------------------------------

def _limpiar_json(raw: str) -> str:
    raw = re.sub(r"```(?:json)?\s*", "", raw)
    raw = raw.strip().strip("`").strip()
    # Limpiar comas finales antes de cierre
    raw = re.sub(r',\s*([\]}])', r'\1', raw)
    return raw


def _parsear_respuesta_auditor(raw: str) -> dict:
    """Parsea la respuesta JSON del auditor con dos capas de fallback."""
    cleaned = _limpiar_json(raw)
    # Intentar extraer el objeto JSON aunque haya texto alrededor
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Segunda capa: limpiar caracteres de control
        cleaned2 = re.sub(r'[\x00-\x1f\x7f]', ' ', cleaned)
        cleaned2 = re.sub(r',\s*([\]}])', r'\1', cleaned2)
        try:
            return json.loads(cleaned2)
        except json.JSONDecodeError as exc:
            return {
                "_error": str(exc),
                "_raw_fragment": raw[:500],
                "entidades": {},
                "discrepancias": [],
                "vacios_regulatorios": [],
                "debilidades_tecnicas": [],
                "icti": {
                    "puntaje_total": 0,
                    "consistencia_datos": 0,
                    "completitud_regulatoria": 0,
                    "solidez_tecnica": 0,
                    "formato_presentacion": 0,
                    "nivel": "RECHAZABLE",
                    "comentario_ejecutivo": f"Error al procesar la respuesta: {exc}"
                }
            }


def auditar_informe(
    client: anthropic.Anthropic,
    texto: str,
    model_id: str,
) -> dict:
    """
    Envía el texto del informe a Claude y retorna el resultado estructurado.
    Implementa desbloqueo de 8K tokens y filtro anticorte por saturación.
    """
    MAX_CHARS = 180_000
    truncado = False
    if len(texto) > MAX_CHARS:
        texto = texto[:MAX_CHARS]
        truncado = True

    # Instrucción defensiva anti-corte para evitar que el JSON se rompa a la mitad
    instruccion_anticorte = (
        "\n\nCRÍTICO: Sé extremadamente conciso. Limita las listas de 'discrepancias', "
        "'vacios_regulatorios' y 'debilidades_tecnicas' a un MÁXIMO de 10 elementos "
        "por categoría, enfocándote exclusivamente en los hallazgos ALTA, IMPORTANTE o BLOQUEANTE."
    )

    try:
        # Aquí habilitamos los 8192 tokens con el extra_header oficial de Anthropic
        msg = client.messages.create(
            model=model_id,
            max_tokens=8192,
            extra_headers={"anthropic-beta": "max-tokens-2024-07-17"},
            system=SYSTEM_PROMPT_AUDITOR + instruccion_anticorte,
            messages=[{
                "role": "user",
                "content": (
                    "A continuación el texto completo del informe técnico a auditar.\n"
                    "Realiza el análisis completo en las 5 fases y devuelve "
                    "únicamente el JSON solicitado.\n\n"
                    f"{texto}"
                ),
            }],
        )
        if msg.stop_reason == "max_tokens":
            st.warning(
                "⚠️ La respuesta alcanzó el límite físico de tokens. "
                "El análisis se optimizó para mostrar los hallazgos más críticos."
            )
            
        resultado = _parsear_respuesta_auditor(msg.content[0].text.strip())
        
        if truncado:
            resultado["_advertencia_truncado"] = (
                f"El informe fue truncado a {MAX_CHARS:,} caracteres. "
                "Las últimas páginas no fueron analizadas."
            )
        return resultado

    except anthropic.APIStatusError as exc:
        st.error(f"Error de API al auditar: {exc.status_code} — {exc.message}")
        return {}
    except Exception as exc:
        st.error(f"Error inesperado al auditar: {exc}")
        return {}
      
def _badge(texto: str, color_fg: str, color_bg: str) -> str:
    """Genera un badge HTML inline."""
    return (
        f'<span style="background:{color_bg};color:{color_fg};'
        f'padding:2px 8px;border-radius:4px;font-size:11px;'
        f'font-weight:bold;white-space:nowrap">{texto}</span>'
    )


def _render_icti(icti: dict) -> None:
    """Renderiza el Índice de Calidad Técnica con semáforo y desglose."""
    nivel   = icti.get("nivel", "RECHAZABLE")
    puntaje = icti.get("puntaje_total", 0)
    cfg     = _ICTI_COLORES.get(nivel, _ICTI_COLORES["RECHAZABLE"])
    color_t, color_bg, emoji = cfg

    # Tarjeta principal del ICTI
    st.markdown(f"""
    <div style="
        background:{color_bg};border:2px solid {color_t};
        border-radius:12px;padding:20px 24px;margin-bottom:16px;
    ">
      <div style="display:flex;align-items:center;gap:16px">
        <div style="font-size:48px;line-height:1">{emoji}</div>
        <div>
          <div style="font-size:13px;color:{color_t};font-weight:600;
                      text-transform:uppercase;letter-spacing:0.5px">
            Índice de Calidad Técnica del Informe
          </div>
          <div style="font-size:42px;font-weight:800;color:{color_t};
                      line-height:1.1">{puntaje}<span style="font-size:20px">/100</span>
          </div>
          <div style="font-size:16px;font-weight:700;color:{color_t}">{nivel}</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Desglose por componente
    componentes = [
        ("Consistencia de datos",     "consistencia_datos",          25, "🔄"),
        ("Completitud regulatoria",   "completitud_regulatoria",     30, "📋"),
        ("Solidez técnica",           "solidez_tecnica",             30, "🔬"),
        ("Formato y presentación",    "formato_presentacion",        15, "📄"),
    ]
    cols = st.columns(4)
    for col, (nombre, key, maximo, icon) in zip(cols, componentes):
        val = icti.get(key, 0)
        pct = int((val / maximo) * 100) if maximo > 0 else 0
        col.metric(
            label=f"{icon} {nombre}",
            value=f"{val}/{maximo}",
            delta=f"{pct}%",
            delta_color="normal" if pct >= 70 else "inverse",
        )

    # Comentario ejecutivo
    comentario = icti.get("comentario_ejecutivo", "")
    if comentario:
        st.info(f"💼 **Diagnóstico ejecutivo:** {comentario}")


def _render_entidades(entidades: dict) -> None:
    """Renderiza la tabla de entidades extraídas del informe."""
    if not entidades:
        st.caption("No se pudieron extraer entidades del documento.")
        return

    LABELS = {
        "numero_informe":           "Número de informe",
        "fecha_siniestro":          "Fecha del siniestro",
        "fecha_muestreo":           "Fecha de muestreo",
        "municipio":                "Municipio",
        "estado":                   "Estado",
        "km_autopista":             "Km autopista",
        "nombre_autopista":         "Nombre autopista",
        "volumen_derramado_litros": "Volumen derramado (L)",
        "contaminante":             "Contaminante",
        "coordenadas_siniestro":    "Coordenadas siniestro",
        "area_afectada_m2":         "Área afectada (m²)",
        "volumen_suelo_m3":         "Volumen suelo contaminado (m³)",
        "numero_pozos_muestreo":    "Número de pozos de muestreo",
        "empresa_vehiculo":         "Empresa / vehículo",
        "responsable_tecnico":      "Responsable técnico",
        "uso_de_suelo":             "Uso de suelo",
        "tipo_muestreo":            "Tipo de muestreo",
    }

    filas = []
    for key, label in LABELS.items():
        val = entidades.get(key)
        filas.append({
            "Campo":  label,
            "Valor":  val if val and val != "null" else "— no encontrado —",
            "Estado": "✅" if val and val != "null" else "⚠️",
        })

    import pandas as pd
    df = pd.DataFrame(filas)
    st.dataframe(
        df.style.map(
            lambda v: "color:#cc6600;font-style:italic" if v == "— no encontrado —" else "",
            subset=["Valor"],
        ),
        use_container_width=True,
        hide_index=True,
    )


def _render_discrepancias(discrepancias: list[dict]) -> None:
    """Renderiza las discrepancias encontradas con semáforo de gravedad."""
    if not discrepancias:
        st.success("✅ No se detectaron discrepancias. Los datos son consistentes.")
        return

    for disc in discrepancias:
        grav      = disc.get("gravedad", "MEDIA")
        cfg       = _GRAVEDAD_COLORES.get(grav, _GRAVEDAD_COLORES["MEDIA"])
        color_t, color_bg = cfg
        icon      = {"ALTA": "🔴", "MEDIA": "🟡", "BAJA": "🟢"}.get(grav, "⚪")
        entidad   = disc.get("entidad", "").replace("_", " ").title()

        with st.expander(
            f"{icon} **[{grav}]** {entidad}",
            expanded=(grav == "ALTA"),
        ):
            col1, col2 = st.columns(2)
            col1.markdown(
                f"**Valor correcto** *(referencia)*\n\n"
                f"```\n{disc.get('valor_referencia','—')}\n```\n"
                f"📍 *{disc.get('seccion_referencia','—')}*"
            )
            col2.markdown(
                f"**Valor discrepante** *(error)*\n\n"
                f"```\n{disc.get('valor_discrepante','—')}\n```\n"
                f"📍 *{disc.get('seccion_error','—')}*"
            )
            st.markdown(
                f"💡 **Recomendación:** {disc.get('recomendacion','—')}"
            )


def _render_vacios_regulatorios(vacios: list[dict]) -> None:
    """Renderiza los vacíos regulatorios agrupados por autoridad."""
    if not vacios:
        st.success("✅ No se detectaron vacíos regulatorios críticos.")
        return

    # Agrupar por autoridad
    from itertools import groupby
    vacios_sorted = sorted(vacios, key=lambda v: v.get("autoridad", "Otro"))

    for autoridad, items in groupby(vacios_sorted, key=lambda v: v.get("autoridad", "Otro")):
        items_list = list(items)
        st.markdown(f"#### 🏛️ {autoridad} — {len(items_list)} observación(es)")
        for item in items_list:
            crit      = item.get("criticidad", "IMPORTANTE")
            cfg       = _GRAVEDAD_COLORES.get(crit, _GRAVEDAD_COLORES["IMPORTANTE"])
            color_t, color_bg = cfg
            icon      = {
                "BLOQUEANTE": "🔴", "IMPORTANTE": "🟡", "MENOR": "🟢"
            }.get(crit, "⚪")

            with st.expander(
                f"{icon} **[{crit}]** {item.get('seccion_afectada','—')}",
                expanded=(crit == "BLOQUEANTE"),
            ):
                st.markdown(
                    f"**Información faltante:**\n\n"
                    f"{item.get('informacion_faltante','—')}"
                )
                st.info(f"💡 **Cómo subsanar:** {item.get('recomendacion','—')}")


def _render_debilidades(debilidades: list[dict]) -> None:
    """Renderiza las debilidades técnicas por sección."""
    if not debilidades:
        st.success("✅ No se detectaron debilidades técnicas importantes.")
        return

    TIPO_ICON = {
        "INSUFICIENTE":   "📉",
        "AMBIGUO":        "❓",
        "CONTRADICTORIO": "⚡",
        "INCOMPLETO":     "⬜",
    }

    for deb in debilidades:
        tipo    = deb.get("tipo", "INSUFICIENTE")
        icon    = TIPO_ICON.get(tipo, "⚠️")
        seccion = deb.get("seccion", "—")

        with st.expander(f"{icon} **{seccion}** — {tipo}"):
            st.markdown(f"**Problema detectado:**\n\n{deb.get('descripcion','—')}")
            st.info(f"✏️ **Sugerencia:** {deb.get('sugerencia','—')}")


# ---------------------------------------------------------------------------
# Función principal de renderizado (llamada desde app.py)
# ---------------------------------------------------------------------------

def render_herramienta_auditor(
    client: anthropic.Anthropic,
    model_id: str,
    proyecto_actual: str | None,
) -> None:
    """
    Renderiza la Herramienta 2 completa — Revisión Técnica Ambiental.

    Args:
        client:          Cliente Anthropic inicializado.
        model_id:        Constante MODEL_ID del app.py.
        proyecto_actual: ID del proyecto activo desde session_state.
    """
    st.header("🔎 Herramienta 2 — Revisión Técnica Ambiental")
    st.caption(
        "Sube el PDF del informe preliminar. Claude lo evaluará como "
        "auditor técnico con perspectiva de SEMARNAT/ASEA/PROFEPA y generará "
        "el Índice de Calidad Técnica del Informe (ICTI)."
    )

    if not proyecto_actual:
        st.warning("⚠️ Selecciona o crea un proyecto en la barra lateral para continuar.")
        return

    # ── Subida del PDF ──────────────────────────────────────────────────────
    uploaded_pdf = st.file_uploader(
        "Sube el PDF del informe preliminar",
        type=["pdf"],
        key="uploader_auditor",
        help="El informe debe tener texto extraíble (no escaneado puro). "
             "Máximo 200 páginas recomendado.",
    )

    if not uploaded_pdf:
        st.info(
            "👆 Sube el PDF del informe para comenzar la auditoría técnica.\n\n"
            "**¿Qué se analiza?**\n"
            "- Consistencia de datos entre secciones\n"
            "- Vacíos regulatorios ante SEMARNAT / ASEA / PROFEPA\n"
            "- Fortaleza técnica de cada capítulo\n"
            "- Índice de Calidad Técnica (ICTI 0-100)"
        )
        return

    if st.button("🔍 Iniciar auditoría técnica", type="primary", key="btn_auditor"):
        pdf_bytes = uploaded_pdf.read()

        # Extraer texto
        with st.spinner("Extrayendo texto del informe…"):
            texto = _extraer_texto_informe(pdf_bytes)

        chars = len(texto.replace(" ", "").replace("\n", ""))
        st.caption(f"📄 Caracteres útiles extraídos: {chars:,}")

        if not _tiene_texto_suficiente(texto):
            st.error(
                "❌ No se pudo extraer suficiente texto del PDF.\n\n"
                "El informe parece ser un PDF escaneado sin capa de texto. "
                "Para auditarlo necesitas una versión con texto seleccionable."
            )
            return

        # Auditoría con Claude
        with st.spinner(
            "Claude está auditando el informe… "
            "Esto puede tomar entre 30 y 90 segundos dependiendo de la extensión."
        ):
            resultado = auditar_informe(client, texto, model_id)

        if not resultado or "_error" in resultado:
            err = resultado.get("_error", "Error desconocido") if resultado else "Sin respuesta"
            raw = resultado.get("_raw_fragment", "") if resultado else ""
            st.error(
                f"❌ Error al procesar la respuesta del auditor.\n\n"
                f"**Detalle:** {err}\n\n"
                f"**Fragmento recibido:**\n```\n{raw}\n```"
            )
            return

        # Advertencia de truncado
        if "_advertencia_truncado" in resultado:
            st.warning(f"⚠️ {resultado['_advertencia_truncado']}")

        # Guardar en session_state para persistir entre reruns
        st.session_state["_auditoria_resultado"]  = resultado
        st.session_state["_auditoria_proyecto"]   = proyecto_actual
        st.session_state["_auditoria_nombre_pdf"] = uploaded_pdf.name

    # ── Mostrar resultados (de la sesión actual o de ejecución previa) ──────
    resultado = st.session_state.get("_auditoria_resultado")
    if (
        resultado
        and st.session_state.get("_auditoria_proyecto") == proyecto_actual
    ):
        nombre_pdf = st.session_state.get("_auditoria_nombre_pdf", "informe.pdf")
        st.markdown(f"**Informe analizado:** `{nombre_pdf}`")
        st.markdown("---")

        # ── ICTI siempre visible arriba ─────────────────────────────────────
        icti = resultado.get("icti", {})
        _render_icti(icti)
        st.markdown("---")

        # ── 4 tabs con el detalle ───────────────────────────────────────────
        tab_ent, tab_disc, tab_vac, tab_deb = st.tabs([
            "📋 Entidades extraídas",
            f"⚠️ Discrepancias ({len(resultado.get('discrepancias', []))})",
            f"🏛️ Vacíos regulatorios ({len(resultado.get('vacios_regulatorios', []))})",
            f"🔬 Debilidades técnicas ({len(resultado.get('debilidades_tecnicas', []))})",
        ])

        with tab_ent:
            st.subheader("Entidades clave extraídas del informe")
            _render_entidades(resultado.get("entidades", {}))

        with tab_disc:
            st.subheader("Discrepancias detectadas (errores copy-paste)")
            _render_discrepancias(resultado.get("discrepancias", []))

        with tab_vac:
            st.subheader("Vacíos regulatorios — lo que la autoridad observaría")
            _render_vacios_regulatorios(resultado.get("vacios_regulatorios", []))

        with tab_deb:
            st.subheader("Debilidades técnicas por sección")
            _render_debilidades(resultado.get("debilidades_tecnicas", []))

        # ── Descarga del reporte completo ───────────────────────────────────
        st.markdown("---")
        col_json, col_txt = st.columns(2)

        with col_json:
            st.download_button(
                "⬇️ Descargar reporte completo (JSON)",
                data=json.dumps(resultado, ensure_ascii=False, indent=2).encode("utf-8"),
                file_name=f"auditoria_{proyecto_actual}.json",
                mime="application/json",
                use_container_width=True,
            )

        with col_txt:
            # Generar resumen en texto plano
            icti_nivel   = icti.get("nivel", "—")
            icti_puntaje = icti.get("puntaje_total", 0)
            n_disc  = len(resultado.get("discrepancias", []))
            n_vac   = len(resultado.get("vacios_regulatorios", []))
            n_deb   = len(resultado.get("debilidades_tecnicas", []))
            comentario = icti.get("comentario_ejecutivo", "—")

            resumen_txt = (
                f"REPORTE DE AUDITORÍA TÉCNICA AMBIENTAL\n"
                f"Proyecto: {proyecto_actual}\n"
                f"Informe: {nombre_pdf}\n"
                f"{'=' * 55}\n\n"
                f"ICTI: {icti_puntaje}/100 — {icti_nivel}\n"
                f"Diagnóstico: {comentario}\n\n"
                f"RESUMEN:\n"
                f"  Discrepancias:        {n_disc}\n"
                f"  Vacíos regulatorios:  {n_vac}\n"
                f"  Debilidades técnicas: {n_deb}\n\n"
                f"{'=' * 55}\n"
                f"Componentes ICTI:\n"
                f"  Consistencia datos:      {icti.get('consistencia_datos',0)}/25\n"
                f"  Completitud regulatoria: {icti.get('completitud_regulatoria',0)}/30\n"
                f"  Solidez técnica:         {icti.get('solidez_tecnica',0)}/30\n"
                f"  Formato y presentación:  {icti.get('formato_presentacion',0)}/15\n"
            )

            # Agregar discrepancias al resumen
            if resultado.get("discrepancias"):
                resumen_txt += f"\n{'=' * 55}\nDISCREPANCIAS:\n"
                for d in resultado["discrepancias"]:
                    resumen_txt += (
                        f"\n[{d.get('gravedad','?')}] {d.get('entidad','—')}\n"
                        f"  Correcto: {d.get('valor_referencia','—')}\n"
                        f"  Error:    {d.get('valor_discrepante','—')}\n"
                        f"  Dónde:    {d.get('seccion_error','—')}\n"
                        f"  Fix:      {d.get('recomendacion','—')}\n"
                    )

            st.download_button(
                "⬇️ Descargar resumen ejecutivo (TXT)",
                data=resumen_txt.encode("utf-8"),
                file_name=f"auditoria_{proyecto_actual}.txt",
                mime="text/plain",
                use_container_width=True,
            )
