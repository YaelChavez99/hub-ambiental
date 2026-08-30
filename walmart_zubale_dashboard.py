"""
Dashboard Walmart × Zubale — Órdenes y Cumplimiento On-Time
=============================================================
Stack: Python + Streamlit + Plotly.
Se eligió este stack (y no React/Tailwind/Recharts) porque el repo ya corre
sobre Streamlit (ver app.py) y permite desplegar un dashboard interactivo
con datos reales de BigQuery en minutos, sin build de frontend separado.

Ejecutar en local:
    streamlit run walmart_zubale_dashboard.py

-------------------------------------------------------------------------
CÓMO CONECTAR TUS DATOS REALES (BigQuery o CSV exportado)
-------------------------------------------------------------------------
Todo el dashboard consume un único DataFrame con estas columnas:

    order_id            str/int   — ID único de la orden
    created_at          datetime  — fecha/hora de creación de la orden
    brand               str       — marca / sucursal
    status              str       — estado de la orden (delivered, cancelled, ...)
    is_ontime           bool      — True si se entregó a tiempo, False si con retraso

Busca el bloque "PUNTO DE CONEXIÓN DE DATOS" más abajo (función load_data)
para:
  (a) subir un CSV exportado de BigQuery / Google Sheets, o
  (b) descomentar la consulta directa a BigQuery con google-cloud-bigquery.
No hace falta tocar nada más del archivo: filtros, KPIs y gráficos leen
siempre del DataFrame que devuelve load_data().
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA — debe ser la primera llamada Streamlit
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Walmart × Zubale — Órdenes",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# PALETA DE MARCA ZUBALE
# ---------------------------------------------------------------------------
# Tomados directamente del logo oficial de Zubale (azul vibrante sobre navy/blanco).
# Ajusta estos hex si tu manual de marca define tonos adicionales; todo el
# dashboard (CSS + gráficos) se alimenta únicamente de estas constantes.
BLUE = "#0043FC"        # azul Zubale — acento primario / positivo (on-time)
NAVY = "#071633"        # navy profundo — contraste / negativo (retraso)
BLUE_DARK = "#0A2A7A"   # paso intermedio navy→azul, usado en el degradado del header
BG = "#F4F5F9"
CARD = "#FFFFFF"
TEXT = "#101828"
MUTED = "#6B7280"
BORDER = "#E7E9F0"

CSS = f"""
<style>
    .stApp {{ background-color: {BG}; }}
    #MainMenu, footer {{ visibility: hidden; }}

    /* Encabezado */
    .zb-header {{
        background: linear-gradient(120deg, {NAVY} 0%, {BLUE_DARK} 100%);
        border-radius: 16px;
        padding: 28px 32px;
        margin-bottom: 24px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        box-shadow: 0 8px 24px rgba(11, 31, 58, 0.18);
    }}
    .zb-header h1 {{
        color: #FFFFFF; font-size: 1.6rem; font-weight: 700; margin: 0;
    }}
    .zb-header p {{
        color: #C9D2E3; font-size: 0.9rem; margin: 4px 0 0 0;
    }}
    .zb-badge {{
        background: {BLUE};
        color: white;
        padding: 6px 16px;
        border-radius: 999px;
        font-size: 0.8rem;
        font-weight: 600;
        white-space: nowrap;
    }}

    /* Tarjetas KPI */
    .zb-kpi {{
        background: {CARD};
        border: 1px solid {BORDER};
        border-radius: 14px;
        padding: 20px 22px;
        box-shadow: 0 2px 10px rgba(16, 24, 40, 0.04);
        height: 100%;
    }}
    .zb-kpi-label {{
        color: {MUTED}; font-size: 0.8rem; font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 8px;
    }}
    .zb-kpi-value {{
        color: {TEXT}; font-size: 2rem; font-weight: 800; line-height: 1.1;
    }}
    .zb-kpi-delta {{
        font-size: 0.82rem; font-weight: 600; margin-top: 6px; display: inline-block;
    }}
    .zb-kpi-accent {{ border-top: 4px solid {BLUE}; }}
    .zb-kpi-accent-navy {{ border-top: 4px solid {NAVY}; }}

    /* Contenedores de gráficos */
    .zb-panel {{
        background: {CARD};
        border: 1px solid {BORDER};
        border-radius: 14px;
        padding: 18px 20px 6px 20px;
        box-shadow: 0 2px 10px rgba(16, 24, 40, 0.04);
        margin-bottom: 20px;
    }}
    .zb-panel h3 {{
        color: {TEXT}; font-size: 1.02rem; font-weight: 700; margin: 0 0 2px 0;
    }}
    .zb-panel span.zb-sub {{ color: {MUTED}; font-size: 0.82rem; }}

    section[data-testid="stSidebar"] {{
        background: {CARD}; border-right: 1px solid {BORDER};
    }}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# PUNTO DE CONEXIÓN DE DATOS
# ---------------------------------------------------------------------------
def _generar_datos_mock(n: int = 6000, seed: int = 42) -> pd.DataFrame:
    """Genera órdenes de prueba realistas para visualizar el dashboard de inmediato.
    Reemplázalo por datos reales usando load_data() más abajo."""
    rng = np.random.default_rng(seed)

    brands = [
        "Walmart Supercenter",
        "Bodega Aurrerá",
        "Walmart Express",
        "Sam's Club",
        "Mi Bodega Aurrerá",
    ]
    brand_weights = [0.30, 0.26, 0.20, 0.14, 0.10]
    # probabilidad de on-time distinta por marca, para que el desglose sea interesante
    brand_ontime_rate = {
        "Walmart Supercenter": 0.93,
        "Bodega Aurrerá": 0.87,
        "Walmart Express": 0.81,
        "Sam's Club": 0.95,
        "Mi Bodega Aurrerá": 0.84,
    }

    end_date = pd.Timestamp.now().normalize()
    start_date = end_date - pd.Timedelta(days=89)
    date_range_seconds = int((end_date - start_date).total_seconds())

    brand_col = rng.choice(brands, size=n, p=brand_weights)
    offset_seconds = rng.integers(0, date_range_seconds, size=n)
    created_at = start_date + pd.to_timedelta(offset_seconds, unit="s")

    # ligera tendencia: fines de semana con más volumen y algo más de retrasos
    is_weekend = pd.Series(created_at).dt.dayofweek.isin([5, 6]).to_numpy()

    ontime_prob = np.array([brand_ontime_rate[b] for b in brand_col])
    ontime_prob = np.where(is_weekend, ontime_prob - 0.08, ontime_prob)
    is_ontime = rng.random(n) < ontime_prob

    status = np.where(
        is_ontime,
        "delivered",
        rng.choice(["delivered_late", "cancelled", "returned"], size=n, p=[0.75, 0.15, 0.10]),
    )

    df = pd.DataFrame(
        {
            "order_id": [f"WM-{100000 + i}" for i in range(n)],
            "created_at": created_at,
            "brand": brand_col,
            "status": status,
            "is_ontime": is_ontime,
        }
    ).sort_values("created_at").reset_index(drop=True)
    return df


@st.cache_data(show_spinner=False)
def _cargar_csv(file) -> pd.DataFrame:
    df = pd.read_csv(file)
    df.columns = [c.strip().lower() for c in df.columns]

    # Mapeo flexible de nombres de columna comunes al exportar de BigQuery/Sheets
    rename_map = {
        "local_updated_at": "created_at",
        "order_date": "created_at",
        "marca": "brand",
        "sucursal": "brand",
        "estado": "status",
        "on_time": "is_ontime",
        "ontime": "is_ontime",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    required = {"order_id", "created_at", "brand", "status", "is_ontime"}
    faltantes = required - set(df.columns)
    if faltantes:
        raise ValueError(f"El CSV no tiene las columnas requeridas: {sorted(faltantes)}")

    df["created_at"] = pd.to_datetime(df["created_at"])
    if df["is_ontime"].dtype != bool:
        df["is_ontime"] = (
            df["is_ontime"].astype(str).str.strip().str.lower().isin(["true", "1", "on_time", "on-time", "sí", "si", "yes"])
        )
    return df


def load_data(fuente: str, archivo_subido) -> pd.DataFrame:
    """Punto único de carga de datos. Todo lo demás en el dashboard depende
    solo del DataFrame que esta función retorna."""

    if fuente == "CSV / Google Sheets exportado":
        if archivo_subido is None:
            st.info("Sube un archivo CSV en el panel lateral para continuar.")
            st.stop()
        return _cargar_csv(archivo_subido)

    if fuente == "BigQuery (en vivo)":
        # ------------------------------------------------------------------
        # CONEXIÓN A BIGQUERY — descomenta y ajusta cuando tengas credenciales
        # ------------------------------------------------------------------
        # from google.cloud import bigquery
        # client = bigquery.Client()  # usa credenciales de st.secrets o ADC
        # query = """
        #     SELECT
        #         order_id,
        #         created_at,
        #         brand,
        #         status,
        #         is_ontime
        #     FROM `tu-proyecto.tu_dataset.walmart_orders`
        #     WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
        # """
        # return client.query(query).to_dataframe()
        st.warning(
            "Conexión a BigQuery no configurada todavía: descomenta el bloque "
            "en `load_data()` dentro de walmart_zubale_dashboard.py y agrega tus credenciales. "
            "Mostrando datos de muestra mientras tanto."
        )
        return _generar_datos_mock()

    # Datos de muestra (default) — así se ve el dashboard sin conectar nada aún
    return _generar_datos_mock()


# ---------------------------------------------------------------------------
# ENCABEZADO
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="zb-header">
        <div>
            <h1>📦 Dashboard de Órdenes — Walmart</h1>
            <p>Cumplimiento de entregas on-time por marca · Powered by Zubale</p>
        </div>
        <div class="zb-badge">Actualizado: {dt.datetime.now().strftime('%d %b %Y, %H:%M')}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# SIDEBAR — FUENTE DE DATOS + FILTROS
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ⚙️ Fuente de datos")
    fuente = st.radio(
        "Origen",
        ["Datos de muestra (demo)", "CSV / Google Sheets exportado", "BigQuery (en vivo)"],
        label_visibility="collapsed",
    )
    archivo_subido = None
    if fuente == "CSV / Google Sheets exportado":
        archivo_subido = st.file_uploader(
            "Sube tu CSV",
            type=["csv"],
            help="Columnas esperadas: order_id, created_at, brand, status, is_ontime",
        )

    st.markdown("---")
    st.markdown("### 🔍 Filtros")

df_raw = load_data(fuente, archivo_subido)

with st.sidebar:
    fecha_min = df_raw["created_at"].min().date()
    fecha_max = df_raw["created_at"].max().date()
    rango_fechas = st.date_input(
        "Rango de fechas",
        value=(fecha_min, fecha_max),
        min_value=fecha_min,
        max_value=fecha_max,
    )

    marcas_disponibles = sorted(df_raw["brand"].dropna().unique().tolist())
    marcas_sel = st.multiselect("Marca / Sucursal", marcas_disponibles, default=marcas_disponibles)

    granularidad = st.radio("Agrupar tendencia por", ["Día", "Semana"], horizontal=True)

# Normaliza el rango de fechas (st.date_input puede devolver 1 o 2 valores)
if isinstance(rango_fechas, tuple) and len(rango_fechas) == 2:
    fecha_ini, fecha_fin = rango_fechas
else:
    fecha_ini, fecha_fin = fecha_min, fecha_max

mask = (
    (df_raw["created_at"].dt.date >= fecha_ini)
    & (df_raw["created_at"].dt.date <= fecha_fin)
    & (df_raw["brand"].isin(marcas_sel))
)
df = df_raw.loc[mask].copy()

if df.empty:
    st.warning("No hay órdenes para los filtros seleccionados.")
    st.stop()

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
total_ordenes = len(df)
ontime_pct = df["is_ontime"].mean() * 100
ordenes_retrasadas = int((~df["is_ontime"]).sum())
marcas_activas = df["brand"].nunique()

k1, k2, k3, k4 = st.columns(4)
kpi_defs = [
    (k1, "Órdenes totales", f"{total_ordenes:,}", "accent-navy"),
    (k2, "On-time general", f"{ontime_pct:.1f}%", "accent"),
    (k3, "Órdenes con retraso", f"{ordenes_retrasadas:,}", "accent-navy"),
    (k4, "Marcas activas", f"{marcas_activas}", "accent"),
]
for col, label, value, accent in kpi_defs:
    with col:
        st.markdown(
            f"""
            <div class="zb-kpi zb-kpi-{accent}">
                <div class="zb-kpi-label">{label}</div>
                <div class="zb-kpi-value">{value}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# GRÁFICOS PRINCIPALES
# ---------------------------------------------------------------------------
col_izq, col_der = st.columns([1, 1.4])

# --- Donut: distribución general on-time vs retraso ------------------------
with col_izq:
    st.markdown(
        """<div class="zb-panel"><h3>Distribución general</h3>
        <span class="zb-sub">On-time vs. con retraso</span></div>""",
        unsafe_allow_html=True,
    )
    fig_donut = go.Figure(
        data=[
            go.Pie(
                labels=["On-time", "Con retraso"],
                values=[df["is_ontime"].sum(), (~df["is_ontime"]).sum()],
                hole=0.62,
                marker=dict(colors=[BLUE, NAVY]),
                textinfo="percent",
                textfont=dict(color="white", size=14),
                sort=False,
            )
        ]
    )
    fig_donut.update_layout(
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15, x=0.15),
        margin=dict(t=10, b=10, l=10, r=10),
        height=320,
        annotations=[dict(text=f"{ontime_pct:.0f}%", x=0.5, y=0.5, font_size=26, font_color=TEXT, showarrow=False)],
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_donut, use_container_width=True, config={"displayModeBar": False})

# --- Barras: on-time % por marca -------------------------------------------
with col_der:
    st.markdown(
        """<div class="zb-panel"><h3>On-time % por marca</h3>
        <span class="zb-sub">Desglose de cumplimiento de entrega</span></div>""",
        unsafe_allow_html=True,
    )
    resumen_marca = (
        df.groupby("brand")
        .agg(ordenes=("order_id", "count"), ontime_pct=("is_ontime", "mean"))
        .assign(ontime_pct=lambda d: d["ontime_pct"] * 100)
        .sort_values("ontime_pct")
        .reset_index()
    )
    fig_bar = go.Figure(
        go.Bar(
            x=resumen_marca["ontime_pct"],
            y=resumen_marca["brand"],
            orientation="h",
            marker=dict(color=BLUE),
            text=[f"{v:.1f}%" for v in resumen_marca["ontime_pct"]],
            textposition="outside",
            customdata=resumen_marca["ordenes"],
            hovertemplate="<b>%{y}</b><br>On-time: %{x:.1f}%<br>Órdenes: %{customdata}<extra></extra>",
        )
    )
    fig_bar.update_layout(
        margin=dict(t=10, b=10, l=10, r=30),
        height=320,
        xaxis=dict(title="On-time %", range=[0, 105], gridcolor=BORDER),
        yaxis=dict(title=""),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT),
    )
    st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})

# --- Línea: tendencia temporal de volumen + on-time % -----------------------
st.markdown(
    """<div class="zb-panel"><h3>Tendencia temporal</h3>
    <span class="zb-sub">Volumen de órdenes y % on-time en el periodo seleccionado</span></div>""",
    unsafe_allow_html=True,
)
freq = "D" if granularidad == "Día" else "W-MON"
tendencia = (
    df.set_index("created_at")
    .resample(freq)
    .agg(volumen=("order_id", "count"), ontime_pct=("is_ontime", "mean"))
    .assign(ontime_pct=lambda d: d["ontime_pct"] * 100)
    .reset_index()
)

fig_trend = go.Figure()
fig_trend.add_trace(
    go.Bar(
        x=tendencia["created_at"],
        y=tendencia["volumen"],
        name="Volumen de órdenes",
        marker=dict(color=NAVY, opacity=0.85),
        yaxis="y1",
        hovertemplate="Órdenes: %{y}<extra></extra>",
    )
)
fig_trend.add_trace(
    go.Scatter(
        x=tendencia["created_at"],
        y=tendencia["ontime_pct"],
        name="% On-time",
        mode="lines+markers",
        line=dict(color=BLUE, width=3),
        marker=dict(size=6),
        yaxis="y2",
        hovertemplate="On-time: %{y:.1f}%<extra></extra>",
    )
)
fig_trend.update_layout(
    height=380,
    margin=dict(t=10, b=10, l=10, r=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    xaxis=dict(title="", gridcolor=BORDER),
    yaxis=dict(title="Órdenes", gridcolor=BORDER),
    yaxis2=dict(title="% On-time", overlaying="y", side="right", range=[0, 105], showgrid=False),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=TEXT),
    hovermode="x unified",
)
st.plotly_chart(fig_trend, use_container_width=True, config={"displayModeBar": False})

st.caption(
    "Datos de muestra generados localmente mientras se conecta la fuente real "
    "(BigQuery o CSV). Ver `load_data()` en walmart_zubale_dashboard.py."
)
