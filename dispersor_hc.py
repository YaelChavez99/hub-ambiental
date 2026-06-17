"""
Módulo: dispersor_hc.py
Herramienta 4 — Análisis de Dispersión de Hidrocarburos + Capítulo 5

Responsabilidad única:
  1. Calcular estadísticas de la pluma de contaminación desde los datos
     reales de laboratorio ya guardados en BD.
  2. Analizar la dispersión potencial del contaminante con Claude,
     considerando todos los factores ambientales del sitio.
  3. Generar el Capítulo 5 del informe con datos reales del proyecto
     (no texto genérico por municipio — usa coordenadas, HFL, zonas reales).

Compatible con: Python 3.10+, anthropic>=0.28, streamlit>=1.35, pandas>=2.2
"""

from __future__ import annotations

import io
import json
import re
import statistics
from typing import Any

import anthropic
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_DISPERSION = """
Eres un ingeniero ambiental senior especializado en modelación de dispersión
de hidrocarburos en suelo y subsuelo en México, con experiencia en casos de
derrames en carretera según la NOM-138-SEMARNAT/SSA1-2012.

Se te proporcionarán los datos reales de un proyecto de caracterización:
- Información del siniestro (municipio, autopista, volumen, contaminante)
- Resultados analíticos de laboratorio por pozo y profundidad
- Estadísticas calculadas de la pluma de contaminación
- Uso de suelo y condiciones del sitio

Tu tarea es generar un análisis técnico de dispersión estructurado en JSON:

{
  "resumen_pluma": {
    "descripcion_general": "<párrafo técnico de 3-5 oraciones sobre la distribución>",
    "zona_epicentro": "<zona con mayor concentración y su valor máximo>",
    "extension_estimada": "<dimensiones aproximadas de la pluma en metros>",
    "profundidad_maxima_afectada": "<profundidad en metros donde aún hay HC detectables>",
    "patron_distribucion": "VERTICAL|LATERAL|MIXTO",
    "tendencia_migracion": "<dirección probable de migración con justificación>"
  },
  "factores_dispersion": [
    {
      "factor": "<nombre del factor>",
      "condicion_sitio": "<cómo aplica en este sitio específico>",
      "efecto_sobre_pluma": "<qué hace a la dispersión: acelera/retarda/dirige>",
      "nivel_riesgo": "ALTO|MEDIO|BAJO"
    }
  ],
  "rutas_migracion": [
    {
      "ruta": "<nombre de la ruta>",
      "descripcion": "<hacia dónde y por qué>",
      "receptor_potencial": "<qué podría verse afectado>",
      "probabilidad": "ALTA|MEDIA|BAJA",
      "distancia_estimada_m": <número o null>
    }
  ],
  "escenarios": [
    {
      "nombre": "<nombre del escenario>",
      "descripcion": "<descripción del escenario>",
      "condicion_desencadenante": "<qué lo activaría>",
      "impacto_potencial": "<consecuencias ambientales>"
    }
  ],
  "conclusiones_dispersion": "<párrafo de 4-6 oraciones con conclusión técnica>",
  "recomendaciones_urgentes": [
    "<acción inmediata 1>",
    "<acción inmediata 2>",
    "<acción inmediata 3>"
  ]
}

REGLAS:
- Basa CADA afirmación en los datos reales proporcionados.
- No inventes valores numéricos que no puedas derivar de los datos.
- Usa terminología técnica ambiental mexicana.
- Responde ÚNICAMENTE con el JSON. Sin markdown. Sin texto adicional.
- Máximo 3 factores, 3 rutas y 2 escenarios para mantener concisión.
"""

SYSTEM_PROMPT_CAPITULO5 = """
Eres un redactor técnico ambiental senior especializado en elaboración de
informes de caracterización de sitios contaminados para presentación ante
SEMARNAT y ASEA en México.

Se te proporcionarán los datos REALES del proyecto:
- Municipio, estado, coordenadas exactas del siniestro
- Autopista, Km, fecha del evento
- Resultados analíticos con concentraciones reales
- Análisis de dispersión ya elaborado
- Uso de suelo confirmado por laboratorio

Redacta el CAPÍTULO 5 — CARACTERÍSTICAS GENERALES DEL SITIO CONTAMINADO
con los siguientes sub-apartados. Para cada uno escribe 3-5 párrafos técnicos
en prosa continua (sin bullets) usando los datos reales proporcionados.
Donde no tengas el dato exacto, usa información técnica verídica del
municipio/región indicada según fuentes INEGI, CONAGUA, SMN.

ESTRUCTURA OBLIGATORIA:

## 5. CARACTERÍSTICAS GENERALES DEL SITIO CONTAMINADO

### 5.1 Localización del Sitio
Describir ubicación exacta con coordenadas UTM reales, municipio, estado,
colindancias con la autopista y el Derecho de Vía (DDV).

### 5.2 Orografía y Geomorfología
Tipo de relieve según INEGI, pendientes, características del terreno
que influyan en la dispersión del contaminante.

### 5.3 Hidrografía e Hidrología
Cuenca hidrológica, subcuenca, cuerpos de agua superficiales cercanos,
acuífero sobreyacente según CONAGUA. Riesgo de afectación.

### 5.4 Clima
Tipo de clima Köppen, temperatura media anual, precipitación media,
temporada de lluvias y su influencia en la movilidad del HC.

### 5.5 Flora y Vegetación
Tipos de vegetación del Derecho de Vía y zona aledaña.
Afectación visible a la cobertura vegetal por el derrame.

### 5.6 Fauna
Fauna reportada en la región. Especies bajo protección NOM-059.
Riesgo de exposición por el contaminante.

### 5.7 Edafología y Tipo de Suelo
Tipo edafológico INEGI del sitio. Textura, permeabilidad, pH medido
en campo. Relación con la movilidad del HC en el perfil del suelo.

### 5.8 Uso de Suelo y Vegetación (USV)
Clasificación INEGI. Uso actual del predio. Clasificación NOM-138
que determina los límites aplicables.

### 5.9 Población
Datos INEGI 2020 del municipio y localidades cercanas al sitio.
Distancia a la población más cercana.

### 5.10 Contexto Económico
Actividades económicas de la región. Relevancia de la autopista
para el transporte y la economía local.

REGLAS:
- Escribe en tercera persona, tiempo presente, estilo técnico formal.
- Integra los datos numéricos reales (coordenadas, HFL, área) donde corresponda.
- NO uses bullets ni listas dentro de los sub-apartados — solo prosa.
- Extensión total: 2,000 a 3,500 palabras.
- Devuelve ÚNICAMENTE el texto del capítulo. Sin JSON. Sin markdown extra.
  Solo el capítulo formateado con ## y ### para los encabezados.
"""

# ---------------------------------------------------------------------------
# Cálculos de la pluma — sin API, solo matemáticas
# ---------------------------------------------------------------------------

def calcular_estadisticas_pluma(historial: list[dict]) -> dict:
    """
    Calcula estadísticas geométricas y analíticas de la pluma de HC
    a partir de los datos reales de laboratorio guardados en BD.

    Returns:
        Dict con todas las estadísticas necesarias para alimentar los prompts.
    """
    if not historial:
        return {}

    def sf(v: Any) -> float:
        try:
            return float(str(v).replace(",", "")) if v else 0.0
        except (ValueError, TypeError):
            return 0.0

    # Construir dataset completo
    filas = []
    for h in historial:
        res  = h.get("resultados", {})
        cx   = sf(h.get("x", 0))
        cy   = sf(h.get("y", 0))
        hfl  = sf(res.get("HFL",  0))
        ph   = sf(res.get("pH",   0))
        hum  = sf(res.get("Humedad", 0))
        prof = sf(h.get("profundidad", 0))
        zona = str(h.get("zona", "")).upper().strip()
        filas.append({
            "id_muestra": h.get("id_muestra", ""),
            "zona":        zona,
            "profundidad": prof,
            "x":           cx,
            "y":           cy,
            "HFL":         hfl,
            "pH":          ph,
            "Humedad":     hum,
            "rebase":      bool(h.get("rebase", False)),
        })

    df = pd.DataFrame(filas)

    # ── Estadísticas globales ─────────────────────────────────────────────
    hfl_vals    = df["HFL"].tolist()
    hfl_pos     = [v for v in hfl_vals if v > 0]
    hfl_rebase  = df[df["rebase"]]["HFL"].tolist()

    total_muestras   = len(df)
    muestras_positivas = len(df[df["HFL"] > 0])
    muestras_rebase  = len(df[df["rebase"]])

    hfl_max   = max(hfl_vals) if hfl_vals else 0
    hfl_media = round(statistics.mean(hfl_pos), 2) if hfl_pos else 0
    hfl_mediana = round(statistics.median(hfl_pos), 2) if hfl_pos else 0

    # Muestra con concentración máxima
    idx_max     = df["HFL"].idxmax() if not df.empty else 0
    muestra_max = df.loc[idx_max] if not df.empty else None

    # ── Geometría de la pluma ─────────────────────────────────────────────
    df_pos = df[df["HFL"] > 0]
    if not df_pos.empty and df_pos["x"].any():
        x_min = df_pos["x"].min()
        x_max = df_pos["x"].max()
        y_min = df_pos["y"].min()
        y_max = df_pos["y"].max()
        ancho_m  = round(x_max - x_min, 1)
        largo_m  = round(y_max - y_min, 1)
        centroide_x = round(df_pos["x"].mean(), 2)
        centroide_y = round(df_pos["y"].mean(), 2)
    else:
        x_min = x_max = y_min = y_max = 0
        ancho_m = largo_m = 0
        centroide_x = centroide_y = 0

    # ── Análisis por profundidad ──────────────────────────────────────────
    prof_grupos = df[df["HFL"] > 0].groupby("profundidad")["HFL"].agg(["mean","max","count"])
    prof_grupos = prof_grupos.reset_index().sort_values("profundidad")
    prof_max_hfl = (
        float(prof_grupos.loc[prof_grupos["max"].idxmax(), "profundidad"])
        if not prof_grupos.empty else 0.0
    )
    prof_max_detectado = df[df["HFL"] > 0]["profundidad"].max() if not df_pos.empty else 0.0

    # ── Análisis por zona ─────────────────────────────────────────────────
    zona_stats = {}
    for zona in df["zona"].unique():
        df_z     = df[df["zona"] == zona]
        hfl_z    = df_z["HFL"].tolist()
        hfl_pos_z = [v for v in hfl_z if v > 0]
        zona_stats[zona] = {
            "n_muestras":   len(df_z),
            "n_positivas":  len(df_z[df_z["HFL"] > 0]),
            "n_rebase":     len(df_z[df_z["rebase"]]),
            "hfl_max":      max(hfl_z) if hfl_z else 0,
            "hfl_media":    round(statistics.mean(hfl_pos_z), 2) if hfl_pos_z else 0,
        }

    # ── pH y humedad promedio ─────────────────────────────────────────────
    ph_vals  = [v for v in df["pH"].tolist()  if v > 0]
    hum_vals = [v for v in df["Humedad"].tolist() if v > 0]
    ph_prom  = round(statistics.mean(ph_vals),  2) if ph_vals  else 0
    hum_prom = round(statistics.mean(hum_vals), 2) if hum_vals else 0

    # ── Tabla resumen por muestra (para el prompt) ────────────────────────
    tabla_resumen = []
    for _, row in df.iterrows():
        tabla_resumen.append({
            "muestra":    row["id_muestra"],
            "zona":       row["zona"],
            "prof_m":     row["profundidad"],
            "x":          row["x"],
            "y":          row["y"],
            "HFL_mgkg":   row["HFL"],
            "pH":         row["pH"],
            "Humedad_pct":row["Humedad"],
            "excede_LMP": row["rebase"],
        })

    return {
        # Conteos
        "total_muestras":      total_muestras,
        "muestras_positivas":  muestras_positivas,
        "muestras_rebase":     muestras_rebase,
        "porcentaje_positivas": round(muestras_positivas / total_muestras * 100, 1)
                                if total_muestras else 0,

        # HFL estadísticas
        "hfl_maximo":          round(hfl_max, 2),
        "hfl_media_positivos": hfl_media,
        "hfl_mediana_positivos": hfl_mediana,
        "muestra_max_id":      str(muestra_max["id_muestra"]) if muestra_max is not None else "",
        "muestra_max_zona":    str(muestra_max["zona"])       if muestra_max is not None else "",
        "muestra_max_prof":    float(muestra_max["profundidad"]) if muestra_max is not None else 0,
        "muestra_max_x":       float(muestra_max["x"])        if muestra_max is not None else 0,
        "muestra_max_y":       float(muestra_max["y"])        if muestra_max is not None else 0,

        # Geometría
        "pluma_ancho_m":       ancho_m,
        "pluma_largo_m":       largo_m,
        "centroide_x":         centroide_x,
        "centroide_y":         centroide_y,

        # Profundidades
        "prof_max_hfl_m":      prof_max_hfl,
        "prof_max_detectado_m":float(prof_max_detectado),

        # Por zona
        "zona_stats":          zona_stats,

        # Fisicoquímicos
        "ph_promedio":         ph_prom,
        "humedad_promedio_pct":hum_prom,

        # Tabla detallada para el prompt
        "tabla_muestras":      tabla_resumen,
    }


# ---------------------------------------------------------------------------
# Llamadas a Claude
# ---------------------------------------------------------------------------

def analizar_dispersion_claude(
    client:    anthropic.Anthropic,
    model_id:  str,
    stats:     dict,
    contexto:  dict,
) -> dict:
    """
    Envía los datos reales del proyecto a Claude y retorna el análisis
    de dispersión estructurado en JSON.

    Args:
        client:   Cliente Anthropic.
        model_id: Nombre del modelo (MODEL_ID de app.py).
        stats:    Salida de calcular_estadisticas_pluma().
        contexto: Dict con datos del proyecto (municipio, autopista, etc.).
    """
    # Construir el payload de datos para el prompt
    tabla_txt = "\n".join([
        f"  {r['muestra']:15s} | {r['zona']:10s} | {r['prof_m']} m | "
        f"HFL={r['HFL_mgkg']:>10.2f} | pH={r['pH']:.2f} | "
        f"Hum={r['Humedad_pct']:.1f}% | {'🔴 EXCEDE' if r['excede_LMP'] else '✅ OK'}"
        for r in stats.get("tabla_muestras", [])
    ])

    zona_txt = "\n".join([
        f"  {zona:12s}: {v['n_muestras']} muestras, "
        f"{v['n_rebase']} exceden LMP, HFL max={v['hfl_max']:.2f} mg/kg"
        for zona, v in stats.get("zona_stats", {}).items()
    ])

    payload = f"""
DATOS DEL PROYECTO:
  Siniestro:        {contexto.get('nombre_siniestro', 'N/A')}
  Municipio/Estado: {contexto.get('municipio', 'N/A')}, {contexto.get('estado', 'N/A')}
  Autopista/Km:     {contexto.get('nombre_autopista', 'N/A')} — {contexto.get('km_autopista', 'N/A')}
  Contaminante:     {contexto.get('contaminante', 'Gasolina (HFL)')}
  Volumen derramado:{contexto.get('volumen_litros', 'N/A')} litros
  Área afectada:    {contexto.get('area_m2', 'N/A')} m²
  Uso de suelo:     {contexto.get('uso_suelo', 'Agrícola/Forestal')}

ESTADÍSTICAS DE LA PLUMA:
  Total muestras:    {stats.get('total_muestras', 0)}
  Con HFL detectable:{stats.get('muestras_positivas', 0)} ({stats.get('porcentaje_positivas', 0)}%)
  Exceden LMP NOM-138:{stats.get('muestras_rebase', 0)}

  HFL máximo:        {stats.get('hfl_maximo', 0):.2f} mg/kg
  HFL media (pos):   {stats.get('hfl_media_positivos', 0):.2f} mg/kg
  Muestra epicentro: {stats.get('muestra_max_id', '')} ({stats.get('muestra_max_zona', '')})
    Coordenadas:     X={stats.get('muestra_max_x', 0):.2f}, Y={stats.get('muestra_max_y', 0):.2f}
    Profundidad:     {stats.get('muestra_max_prof', 0)} m

  Extensión lateral estimada:
    Ancho: {stats.get('pluma_ancho_m', 0):.1f} m (E-O)
    Largo: {stats.get('pluma_largo_m', 0):.1f} m (N-S)
    Centroide: X={stats.get('centroide_x', 0):.2f}, Y={stats.get('centroide_y', 0):.2f}

  Profundidad con HFL máximo: {stats.get('prof_max_hfl_m', 0)} m
  Profundidad máxima detectable: {stats.get('prof_max_detectado_m', 0)} m

  pH promedio: {stats.get('ph_promedio', 0):.2f}
  Humedad promedio: {stats.get('humedad_promedio_pct', 0):.1f}%

RESUMEN POR ZONA:
{zona_txt}

TABLA DETALLADA DE MUESTRAS:
{tabla_txt}
"""

    try:
        msg = client.messages.create(
            model=model_id,
            max_tokens=8192,
            extra_headers={"anthropic-beta": "max-tokens-2024-07-17"},
            system=SYSTEM_PROMPT_DISPERSION,
            messages=[{
                "role": "user",
                "content": (
                    "Con los datos reales del proyecto a continuación, "
                    "genera el análisis técnico de dispersión en el formato JSON solicitado.\n\n"
                    f"{payload}"
                ),
            }],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`")
        raw = re.sub(r',\s*([\]}])', r'\1', raw)
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        st.error(f"Error al parsear análisis de dispersión: {exc}")
        return {}
    except anthropic.APIStatusError as exc:
        st.error(f"Error de API (dispersión): {exc.status_code} — {exc.message}")
        return {}
    except Exception as exc:
        st.error(f"Error inesperado (dispersión): {exc}")
        return {}


def generar_capitulo5_claude(
    client:    anthropic.Anthropic,
    model_id:  str,
    stats:     dict,
    contexto:  dict,
    analisis:  dict,
) -> str:
    """
    Genera el texto completo del Capítulo 5 usando los datos reales
    del proyecto y el análisis de dispersión ya elaborado.

    Returns:
        String con el texto del capítulo formateado en Markdown.
    """
    # Serializar el análisis de dispersión para el prompt
    analisis_txt = json.dumps(analisis, ensure_ascii=False, indent=2)[:3000]

    payload = f"""
DATOS DEL PROYECTO (usar en el capítulo):
  Siniestro:         {contexto.get('nombre_siniestro', 'N/A')}
  Municipio:         {contexto.get('municipio', 'N/A')}
  Estado:            {contexto.get('estado', 'N/A')}
  Autopista:         {contexto.get('nombre_autopista', 'N/A')}
  Kilómetro:         {contexto.get('km_autopista', 'N/A')}
  Coordenadas:       {contexto.get('coordenadas', 'N/A')}
  Fecha siniestro:   {contexto.get('fecha', 'N/A')}
  Contaminante:      {contexto.get('contaminante', 'Gasolina (HC fracción ligera)')}
  Volumen derramado: {contexto.get('volumen_litros', 'N/A')} litros
  Área afectada:     {contexto.get('area_m2', 'N/A')} m²
  Uso de suelo:      {contexto.get('uso_suelo', 'Agrícola/Forestal')}

DATOS ANALÍTICOS CLAVE (integrar en secciones pertinentes):
  HFL máximo detectado:     {stats.get('hfl_maximo', 0):.2f} mg/kg ({stats.get('muestra_max_id', '')})
  Muestras que exceden LMP: {stats.get('muestras_rebase', 0)} de {stats.get('total_muestras', 0)}
  Profundidad máxima HC:    {stats.get('prof_max_detectado_m', 0)} m
  pH promedio del suelo:    {stats.get('ph_promedio', 0):.2f}
  Humedad promedio:         {stats.get('humedad_promedio_pct', 0):.1f}%
  Extensión lateral pluma:  {stats.get('pluma_ancho_m', 0):.1f} m (E-O) × {stats.get('pluma_largo_m', 0):.1f} m (N-S)

ANÁLISIS DE DISPERSIÓN PREVIO (usar en secciones de Hidrografía y Edafología):
{analisis_txt}
"""

    try:
        msg = client.messages.create(
            model=model_id,
            max_tokens=16000,
            extra_headers={"anthropic-beta": "max-tokens-2024-07-17"},
            system=SYSTEM_PROMPT_CAPITULO5,
            messages=[{
                "role": "user",
                "content": (
                    "Con los datos reales a continuación, redacta el "
                    "Capítulo 5 completo del informe de caracterización.\n\n"
                    f"{payload}"
                ),
            }],
        )
        return msg.content[0].text.strip()
    except anthropic.APIStatusError as exc:
        st.error(f"Error de API (Cap. 5): {exc.status_code} — {exc.message}")
        return ""
    except Exception as exc:
        st.error(f"Error inesperado (Cap. 5): {exc}")
        return ""


# ---------------------------------------------------------------------------
# Renderizado de resultados
# ---------------------------------------------------------------------------

def _render_analisis_dispersion(analisis: dict, stats: dict) -> None:
    """Renderiza el análisis de dispersión con métricas y cards visuales."""

    resumen = analisis.get("resumen_pluma", {})
    factores = analisis.get("factores_dispersion", [])
    rutas    = analisis.get("rutas_migracion", [])
    escenarios = analisis.get("escenarios", [])

    # ── Métricas clave de la pluma ─────────────────────────────────────────
    st.subheader("📐 Geometría de la Pluma")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("HFL Máximo",
              f"{stats.get('hfl_maximo',0):,.2f} mg/kg",
              f"Muestra {stats.get('muestra_max_id','')}")
    c2.metric("Muestras > LMP",
              f"{stats.get('muestras_rebase',0)} / {stats.get('total_muestras',0)}",
              f"{stats.get('porcentaje_positivas',0):.0f}% con HC detectable")
    c3.metric("Extensión lateral",
              f"{stats.get('pluma_ancho_m',0):.0f} × {stats.get('pluma_largo_m',0):.0f} m",
              "Este-Oeste × Norte-Sur")
    c4.metric("Prof. máx. afectada",
              f"{stats.get('prof_max_detectado_m',0):.2f} m",
              f"Mayor HFL a {stats.get('prof_max_hfl_m',0):.2f} m")

    # ── Descripción general ────────────────────────────────────────────────
    if resumen.get("descripcion_general"):
        st.info(f"📋 **Descripción de la pluma:**\n\n{resumen['descripcion_general']}")

    col_a, col_b = st.columns(2)
    with col_a:
        if resumen.get("tendencia_migracion"):
            st.warning(f"🧭 **Tendencia de migración:**\n\n{resumen['tendencia_migracion']}")
    with col_b:
        patron = resumen.get("patron_distribucion", "—")
        colores = {"VERTICAL": "🔴", "LATERAL": "🟡", "MIXTO": "🟠"}
        st.info(f"{colores.get(patron,'⚪')} **Patrón:** {patron}\n\n"
                f"**Epicentro:** {resumen.get('zona_epicentro','—')}")

    # ── Factores de dispersión ────────────────────────────────────────────
    if factores:
        st.subheader("⚗️ Factores de Dispersión")
        cols = st.columns(min(len(factores), 3))
        riesgo_color = {"ALTO": "🔴", "MEDIO": "🟡", "BAJO": "🟢"}
        for col, f in zip(cols, factores):
            nivel = f.get("nivel_riesgo", "MEDIO")
            with col:
                st.markdown(f"""
                <div style="border:1px solid #ddd;border-radius:8px;
                            padding:12px;height:100%;">
                  <b>{riesgo_color.get(nivel,'⚪')} {f.get('factor','—')}</b><br>
                  <small style="color:#555">{f.get('condicion_sitio','—')}</small><br><br>
                  <i>{f.get('efecto_sobre_pluma','—')}</i>
                </div>
                """, unsafe_allow_html=True)

    # ── Rutas de migración ────────────────────────────────────────────────
    if rutas:
        st.subheader("🗺️ Rutas de Migración Potencial")
        prob_color = {"ALTA": "#ffcccc", "MEDIA": "#fff0b3", "BAJA": "#d6f0d6"}
        for ruta in rutas:
            prob = ruta.get("probabilidad", "MEDIA")
            bg   = prob_color.get(prob, "#f0f0f0")
            dist = ruta.get("distancia_estimada_m")
            dist_txt = f"{dist} m" if dist else "Indeterminada"
            st.markdown(f"""
            <div style="background:{bg};border-radius:8px;
                        padding:12px 16px;margin-bottom:8px">
              <b>{ruta.get('ruta','—')}</b>
              &nbsp;·&nbsp; Probabilidad: <b>{prob}</b>
              &nbsp;·&nbsp; Distancia est.: <b>{dist_txt}</b><br>
              {ruta.get('descripcion','—')}<br>
              <small>⚠️ Receptor: {ruta.get('receptor_potencial','—')}</small>
            </div>
            """, unsafe_allow_html=True)

    # ── Escenarios ────────────────────────────────────────────────────────
    if escenarios:
        st.subheader("🎭 Escenarios de Dispersión")
        for esc in escenarios:
            with st.expander(f"📌 {esc.get('nombre','Escenario')}"):
                st.markdown(f"**Descripción:** {esc.get('descripcion','—')}")
                st.markdown(f"**Condición desencadenante:** {esc.get('condicion_desencadenante','—')}")
                st.warning(f"**Impacto potencial:** {esc.get('impacto_potencial','—')}")

    # ── Conclusiones ──────────────────────────────────────────────────────
    conclusiones = analisis.get("conclusiones_dispersion","")
    if conclusiones:
        st.subheader("📝 Conclusiones Técnicas")
        st.success(conclusiones)

    # ── Recomendaciones urgentes ──────────────────────────────────────────
    recs = analisis.get("recomendaciones_urgentes", [])
    if recs:
        st.subheader("🚨 Recomendaciones Urgentes")
        for i, rec in enumerate(recs, 1):
            st.error(f"**{i}.** {rec}")


def _render_capitulo5(texto: str, proyecto_id: str) -> None:
    """Renderiza el Capítulo 5 y ofrece descarga en TXT."""
    if not texto:
        st.error("No se pudo generar el Capítulo 5.")
        return

    # Contar palabras
    palabras = len(texto.split())
    st.caption(f"📄 {palabras:,} palabras generadas")

    # Renderizar el markdown del capítulo
    st.markdown(texto)

    # ── Descargas ─────────────────────────────────────────────────────────
    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        st.download_button(
            "⬇️ Descargar Capítulo 5 (.txt)",
            data=texto.encode("utf-8"),
            file_name=f"Cap5_{proyecto_id}.txt",
            mime="text/plain",
            use_container_width=True,
        )

    with col2:
        # Versión con encabezado de documento para copiar en Word
        word_ready = (
            f"INFORME DE CARACTERIZACIÓN DE SITIO CONTAMINADO\n"
            f"Proyecto: {proyecto_id}\n"
            f"{'=' * 60}\n\n"
            f"{texto}\n\n"
            f"{'=' * 60}\n"
            f"Generado por Hub de Automatización Ambiental v2.6.0\n"
        )
        st.download_button(
            "⬇️ Descargar para Word (.txt)",
            data=word_ready.encode("utf-8"),
            file_name=f"Cap5_{proyecto_id}_Word.txt",
            mime="text/plain",
            use_container_width=True,
            help="Abre en Word → Archivo → Abrir → selecciona este .txt",
        )


# ---------------------------------------------------------------------------
# Función principal de renderizado (llamada desde app.py)
# ---------------------------------------------------------------------------

def render_herramienta_dispersion(
    client:          anthropic.Anthropic,
    model_id:        str,
    proyecto_actual: str | None,
    historial_lab:   list[dict],
    detalles_proyecto: dict | None,
    entidades_auditor: dict | None = None,
) -> None:
    """
    Renderiza la Herramienta 4 — Análisis de Dispersión + Capítulo 5.

    Args:
        client:             Cliente Anthropic.
        model_id:           MODEL_ID de app.py.
        proyecto_actual:    ID del proyecto activo.
        historial_lab:      Datos de cargar_laboratorio_proyecto().
        detalles_proyecto:  Dict con nombre, uso_de_suelo del proyecto.
        entidades_auditor:  Dict de entidades extraídas por el auditor (opcional).
    """
    st.header("🌊 Herramienta 4 — Dispersión de Hidrocarburos + Capítulo 5")
    st.caption(
        "Analiza la pluma de contaminación con los datos reales de laboratorio "
        "y genera el Capítulo 5 del informe con información específica del sitio."
    )

    if not proyecto_actual:
        st.warning("⚠️ Selecciona o crea un proyecto en la barra lateral para continuar.")
        return

    if not historial_lab:
        st.warning(
            "⚠️ Este proyecto no tiene datos de laboratorio.\n\n"
            "Ve primero a **🧪 Vaciado de Laboratorio**, procesa el PDF de "
            "NOVALABSA y luego regresa aquí."
        )
        return

    # ── Construir contexto enriquecido del proyecto ────────────────────────
    # Combina datos de BD con lo que el auditor extrajo (si está disponible)
    ent = entidades_auditor or {}
    contexto = {
        "nombre_siniestro": detalles_proyecto.get("nombre", "") if detalles_proyecto else "",
        "uso_suelo":        detalles_proyecto.get("uso_de_suelo", "Agrícola/Forestal")
                            if detalles_proyecto else "Agrícola/Forestal",
        "municipio":        ent.get("municipio")      or "Villa de Arriaga",
        "estado":           ent.get("estado")         or "San Luis Potosí",
        "km_autopista":     ent.get("km_autopista")   or "Km 75+550",
        "nombre_autopista": ent.get("nombre_autopista") or "Autopista Lagos de Moreno – SLP",
        "contaminante":     ent.get("contaminante")   or "Gasolina (HC fracción ligera)",
        "volumen_litros":   ent.get("volumen_derramado_litros") or "N/D",
        "area_m2":          ent.get("area_afectada_m2") or "N/D",
        "coordenadas":      ent.get("coordenadas_siniestro") or "N/D",
        "fecha":            ent.get("fecha_siniestro") or "N/D",
    }

    # ── Panel de contexto del proyecto ────────────────────────────────────
    with st.expander("📋 Contexto del proyecto (editar si es necesario)", expanded=False):
        st.caption(
            "Estos datos se usan para generar el análisis y el Capítulo 5. "
            "Si el Auditor Técnico (H2) ya procesó el informe, se rellenan automáticamente."
        )
        col1, col2 = st.columns(2)
        with col1:
            contexto["municipio"]       = st.text_input("Municipio", value=contexto["municipio"])
            contexto["estado"]          = st.text_input("Estado",    value=contexto["estado"])
            contexto["km_autopista"]    = st.text_input("Km autopista", value=contexto["km_autopista"])
            contexto["nombre_autopista"]= st.text_input("Autopista", value=contexto["nombre_autopista"])
        with col2:
            contexto["contaminante"]    = st.text_input("Contaminante", value=contexto["contaminante"])
            contexto["volumen_litros"]  = st.text_input("Volumen derramado (L)", value=str(contexto["volumen_litros"]))
            contexto["area_m2"]         = st.text_input("Área afectada (m²)", value=str(contexto["area_m2"]))
            contexto["fecha"]           = st.text_input("Fecha del siniestro", value=str(contexto["fecha"]))

    # ── Métricas del expediente ────────────────────────────────────────────
    n_total  = len(historial_lab)
    n_rebase = sum(1 for h in historial_lab if h.get("rebase"))
    st.info(
        f"📊 Expediente analítico: **{n_total} muestras** cargadas · "
        f"**{n_rebase}** exceden el LMP NOM-138"
    )

    # ── Botón de análisis ─────────────────────────────────────────────────
    if st.button(
        "🔬 Generar análisis de dispersión + Capítulo 5",
        type="primary",
        key="btn_dispersion",
    ):
        # Paso 1: Calcular estadísticas (sin API)
        with st.spinner("Calculando estadísticas de la pluma…"):
            stats = calcular_estadisticas_pluma(historial_lab)

        if not stats:
            st.error("No se pudieron calcular estadísticas. Verifica los datos de laboratorio.")
            return

        # Paso 2: Análisis de dispersión con Claude
        with st.spinner("Claude analizando la dispersión del contaminante… (30-60 seg)"):
            analisis = analizar_dispersion_claude(client, model_id, stats, contexto)

        if not analisis:
            st.error("No se pudo generar el análisis de dispersión.")
            return

        # Paso 3: Generar Capítulo 5
        with st.spinner("Redactando Capítulo 5 con datos reales del proyecto… (30-60 seg)"):
            cap5 = generar_capitulo5_claude(client, model_id, stats, contexto, analisis)

        # Guardar en session_state para persistir entre reruns
        st.session_state["_dispersion_stats"]    = stats
        st.session_state["_dispersion_analisis"] = analisis
        st.session_state["_dispersion_cap5"]     = cap5
        st.session_state["_dispersion_proyecto"] = proyecto_actual

        # Registrar evento en historial del proyecto (Fase 5)
        try:
            from gestor_proyectos import registrar_evento as _reg_ev
            patron = analisis.get("resumen_pluma", {}).get("patron_distribucion", "—")
            _reg_ev(
                proyecto_actual,
                "DISPERSION",
                f"Análisis de dispersión generado — patrón {patron} · "
                f"HFL máx {stats.get('hfl_maximo', 0):.2f} mg/kg · "
                f"{stats.get('muestras_rebase', 0)} muestras fuera de norma",
                st.session_state.get("usuario_actual", "Ingeniero"),
                {
                    "hfl_maximo":      stats.get("hfl_maximo", 0),
                    "muestras_rebase": stats.get("muestras_rebase", 0),
                    "patron":          patron,
                    "municipio":       contexto.get("municipio", ""),
                },
            )
        except Exception:
            pass   # El historial nunca rompe el flujo principal

        st.success("✅ Análisis completado. Revisa los resultados en los tabs.")
        st.rerun()

    # ── Mostrar resultados guardados ───────────────────────────────────────
    if (
        st.session_state.get("_dispersion_proyecto") == proyecto_actual
        and st.session_state.get("_dispersion_analisis")
    ):
        stats   = st.session_state["_dispersion_stats"]
        analisis= st.session_state["_dispersion_analisis"]
        cap5    = st.session_state.get("_dispersion_cap5", "")

        tab_disp, tab_cap5, tab_raw = st.tabs([
            "🌊 Análisis de Dispersión",
            "📝 Capítulo 5",
            "🔩 Datos crudos (JSON)",
        ])

        with tab_disp:
            _render_analisis_dispersion(analisis, stats)

        with tab_cap5:
            _render_capitulo5(cap5, proyecto_actual)

        with tab_raw:
            st.caption("Datos de estadísticas calculadas y análisis JSON completo.")
            with st.expander("📊 Estadísticas de la pluma"):
                stats_display = {k: v for k, v in stats.items() if k != "tabla_muestras"}
                st.json(stats_display)
            with st.expander("🔬 Análisis de dispersión (JSON completo)"):
                st.json(analisis)
