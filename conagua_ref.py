"""
Módulo: conagua_ref.py
Tabla de referencia local de acuíferos y cuencas hidrológicas de México.

LIMITACIÓN HONESTA: CONAGUA no expone una API REST pública. Lo que existe
es el SINA (visores web, descargas Excel/Shapefile, servicios WMS de
mapas como imagen). Por eso este módulo NO consulta CONAGUA en vivo —
mantiene una tabla local de acuíferos con datos extraídos de fuentes
oficiales públicas (DOF, estudios técnicos citados) que se amplía caso
por caso según los proyectos reales del sistema.

Acuíferos sembrados inicialmente (verificados, no inventados):
  - Valle de San Luis Potosí (clave 2412) — confirmado por fuentes DOF/
    UASLP para el caso real del PDF NOVALABSA OT-126040089.

Para agregar más acuíferos: usar agregar_acuifero() con datos verificados
de la fuente oficial (DOF: "Acuerdos de disponibilidad de aguas
subterráneas" o el Atlas del Agua en México de CONAGUA).

Compatible con: Python 3.10+, psycopg2>=2.9, streamlit>=1.35
"""

from __future__ import annotations

import math
from typing import Any

import psycopg2
import streamlit as st


# ---------------------------------------------------------------------------
# Conexión BD
# ---------------------------------------------------------------------------

def _conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(st.secrets["DATABASE_URL"])


# ---------------------------------------------------------------------------
# Migración + semilla de datos verificados
# ---------------------------------------------------------------------------

def migrar_bd_conagua() -> bool:
    """Crea la tabla de acuíferos y siembra los datos verificados iniciales."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS acuiferos_mx (
                id_acuifero       SERIAL PRIMARY KEY,
                clave             TEXT UNIQUE NOT NULL,
                nombre            TEXT NOT NULL,
                estado            TEXT NOT NULL,
                region_hidrologica TEXT,
                cuenca            TEXT,
                subcuenca         TEXT,
                centroide_lat     DOUBLE PRECISION,
                centroide_lon     DOUBLE PRECISION,
                precipitacion_media_mm DOUBLE PRECISION,
                condicion         TEXT,
                fuente            TEXT DEFAULT 'DOF / CONAGUA',
                fecha_registro    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        # ── Semilla de datos VERIFICADOS (no inventados) ──────────────────
        # Fuente: DOF (Acuerdo de disponibilidad), UASLP, ResearchGate —
        # confirmado para el caso real del proyecto OT-126040089.
        semilla = [
            (
                "2412", "Valle de San Luis Potosí", "San Luis Potosí",
                "RH-26 Pánuco / RH-37 El Salado",  # zona de transición
                "Río Santiago", "Subcuenca Valle de San Luis",
                22.1565, -100.9855,    # centroide aprox. ciudad SLP
                402.6,                  # mm/año — fuente DOF 2015, confirmado
                "SOBREEXPLOTADO — Veda desde 1961. Extracción ~2x la recarga "
                "natural (CNA et al., 2005). Concentración de arsénico y "
                "fluoruro reportada en el acuífero profundo.",
            ),
        ]
        for fila in semilla:
            c.execute(
                """INSERT INTO acuiferos_mx
                   (clave, nombre, estado, region_hidrologica, cuenca,
                    subcuenca, centroide_lat, centroide_lon,
                    precipitacion_media_mm, condicion)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (clave) DO NOTHING""",
                fila,
            )
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception as exc:
        st.session_state["_conagua_error"] = str(exc)
        return False


# ---------------------------------------------------------------------------
# Administración — agregar nuevos acuíferos verificados
# ---------------------------------------------------------------------------

def agregar_acuifero(
    clave: str, nombre: str, estado: str,
    region_hidrologica: str = "", cuenca: str = "", subcuenca: str = "",
    lat: float | None = None, lon: float | None = None,
    precipitacion_mm: float | None = None, condicion: str = "",
) -> tuple[bool, str]:
    """
    Agrega un acuífero verificado a la tabla de referencia.
    Usar SOLO con datos confirmados de fuente oficial (DOF/CONAGUA),
    nunca con valores estimados o inventados.
    """
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """INSERT INTO acuiferos_mx
               (clave, nombre, estado, region_hidrologica, cuenca,
                subcuenca, centroide_lat, centroide_lon,
                precipitacion_media_mm, condicion)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (clave, nombre, estado, region_hidrologica, cuenca,
             subcuenca, lat, lon, precipitacion_mm, condicion),
        )
        conn.commit()
        c.close()
        conn.close()
        return True, f"Acuífero '{nombre}' agregado."
    except psycopg2.errors.UniqueViolation:
        return False, f"Ya existe un acuífero con clave '{clave}'."
    except Exception as exc:
        return False, f"Error: {exc}"


def listar_acuiferos() -> list[dict]:
    """Lista todos los acuíferos registrados en la tabla de referencia."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute("""
            SELECT clave, nombre, estado, region_hidrologica, cuenca,
                   subcuenca, centroide_lat, centroide_lon,
                   precipitacion_media_mm, condicion, fuente
            FROM acuiferos_mx ORDER BY estado, nombre
        """)
        rows = c.fetchall()
        c.close()
        conn.close()
        return [
            {
                "clave": r[0], "nombre": r[1], "estado": r[2],
                "region": r[3], "cuenca": r[4], "subcuenca": r[5],
                "lat": r[6], "lon": r[7], "precip_mm": r[8],
                "condicion": r[9], "fuente": r[10],
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Búsqueda por proximidad geográfica
# ---------------------------------------------------------------------------

def _distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia aproximada en km entre dos puntos (fórmula haversine simplificada)."""
    R = 6371.0  # radio de la Tierra en km
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def buscar_acuifero_cercano(
    lat: float, lon: float, estado_filtro: str = ""
) -> dict | None:
    """
    Busca el acuífero más cercano a una coordenada dada.
    Si se proporciona estado_filtro, prioriza acuíferos de ese estado.

    Returns:
        Dict del acuífero más cercano con distancia_km calculada,
        o None si la tabla de referencia está vacía para esa zona.
    """
    acuiferos = listar_acuiferos()
    if not acuiferos:
        return None

    candidatos = acuiferos
    if estado_filtro:
        filtrados = [a for a in acuiferos
                    if estado_filtro.upper() in (a["estado"] or "").upper()]
        if filtrados:
            candidatos = filtrados

    mejor = None
    mejor_dist = float("inf")
    for ac in candidatos:
        if ac["lat"] is None or ac["lon"] is None:
            continue
        d = _distancia_km(lat, lon, ac["lat"], ac["lon"])
        if d < mejor_dist:
            mejor_dist = d
            mejor = ac

    if mejor:
        mejor = dict(mejor)
        mejor["distancia_km"] = round(mejor_dist, 1)
        mejor["origen"] = "tabla_referencia"
        return mejor

    return None


def buscar_acuifero_por_estado(estado: str) -> dict | None:
    """
    Fallback simple: si no hay coordenadas precisas, busca el primer
    acuífero registrado para el estado dado. Menos preciso que la
    búsqueda por proximidad pero útil cuando solo se tiene el nombre
    del estado/municipio.
    """
    acuiferos = listar_acuiferos()
    for ac in acuiferos:
        if estado.upper() in (ac["estado"] or "").upper():
            ac = dict(ac)
            ac["origen"] = "tabla_referencia_por_estado"
            return ac
    return None


# ---------------------------------------------------------------------------
# Renderizado — Estado de la integración + panel de administración
# ---------------------------------------------------------------------------

def render_estado_conagua(datos: dict | None) -> None:
    """Muestra el resultado de la búsqueda de acuífero con su origen."""
    if not datos:
        st.warning(
            "⚠️ **CONAGUA — sin dato de referencia.** No hay acuíferos "
            "registrados para esta zona en la tabla local. CONAGUA no "
            "expone API pública; agrega el acuífero manualmente en el "
            "panel de administración si lo conoces."
        )
        return

    dist = datos.get("distancia_km")
    dist_txt = f" · {dist} km del epicentro" if dist is not None else ""
    st.success(
        f"✅ **{datos['nombre']}** (clave {datos['clave']}){dist_txt}\n\n"
        f"Cuenca: {datos.get('cuenca','—')} · "
        f"Precipitación media: {datos.get('precip_mm','—')} mm/año\n\n"
        f"⚠️ {datos.get('condicion','—')}"
    )
    st.caption(f"Fuente: {datos.get('fuente','—')} (tabla de referencia local — CONAGUA sin API)")


def render_panel_conagua(usuario_rol: str = "INGENIERO") -> None:
    """Panel de administración de la tabla de acuíferos. Visible para todos
    los roles, ya que es información técnica de consulta y enriquecimiento."""
    st.header("💧 Referencia de Acuíferos (CONAGUA)")
    st.warning(
        "ℹ️ CONAGUA no tiene API REST pública. Esta tabla se construye "
        "manualmente con datos verificados de fuentes oficiales (DOF, "
        "Atlas del Agua en México). Agrega acuíferos según aparezcan "
        "nuevos proyectos en otros estados."
    )

    acuiferos = listar_acuiferos()
    if acuiferos:
        st.subheader(f"Acuíferos registrados ({len(acuiferos)})")
        for ac in acuiferos:
            with st.expander(f"💧 {ac['nombre']} ({ac['estado']}) — clave {ac['clave']}"):
                st.markdown(f"**Cuenca:** {ac.get('cuenca','—')}")
                st.markdown(f"**Subcuenca:** {ac.get('subcuenca','—')}")
                st.markdown(f"**Precipitación media:** {ac.get('precip_mm','—')} mm/año")
                st.markdown(f"**Condición:** {ac.get('condicion','—')}")
                st.caption(f"Fuente: {ac.get('fuente','—')}")
    else:
        st.info("No hay acuíferos registrados aún.")

    with st.expander("➕ Agregar acuífero verificado", expanded=False):
        st.caption(
            "⚠️ Usa solo datos confirmados de fuente oficial (DOF/CONAGUA). "
            "No inventes valores de disponibilidad ni coordenadas."
        )
        with st.form("form_acuifero"):
            col1, col2 = st.columns(2)
            with col1:
                clave   = st.text_input("Clave CONAGUA")
                nombre  = st.text_input("Nombre del acuífero")
                estado  = st.text_input("Estado")
                cuenca  = st.text_input("Cuenca")
            with col2:
                lat     = st.number_input("Latitud centroide", format="%.5f")
                lon     = st.number_input("Longitud centroide", format="%.5f")
                precip  = st.number_input("Precipitación media (mm/año)", min_value=0.0)
                condicion = st.text_area("Condición (sobreexplotado/equilibrio/etc.)")

            if st.form_submit_button("Guardar acuífero", type="primary"):
                ok, msg = agregar_acuifero(
                    clave, nombre, estado, cuenca=cuenca,
                    lat=lat or None, lon=lon or None,
                    precipitacion_mm=precip or None, condicion=condicion,
                )
                if ok:
                    st.success(f"✅ {msg}")
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")
