"""
Módulo: gestor_proyectos.py
Fase 5 — Arquitectura Multi-Proyecto

Responsabilidades:
  1. migrar_bd_fase5()           — Crear/extender tablas en caliente (idempotente)
  2. registrar_evento()          — Historial automático de actividades
  3. guardar_documento_db()      — Gestión documental por proyecto
  4. render_dashboard_proyecto() — Vista ejecutiva del proyecto activo
  5. render_gestor_proyectos()   — Tabla maestra de todos los proyectos

Se integra en app.py como una herramienta más del sidebar
y sus funciones de BD son llamadas por H1, H2, H3 y H4
para registrar eventos automáticamente.

Compatible con: Python 3.10+, psycopg2>=2.9, streamlit>=1.35, pandas>=2.2
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Any

import pandas as pd
import psycopg2
import streamlit as st

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
_TIPOS_DOC = ["PDF_LAB", "PDF_INFORME", "REPORTE_GENERADO", "MAPA", "OTRO"]
_TIPOS_NOTA = ["TÉCNICA", "REGULATORIA", "OPERATIVA"]
_ACCIONES_VALIDAS = {
    "CREACION":    "🆕 Proyecto creado",
    "LABORATORIO": "🧪 Laboratorio procesado",
    "AUDITORIA":   "🔎 Auditoría técnica realizada",
    "DISPERSION":  "🌊 Análisis de dispersión generado",
    "FOTO":        "📷 Fotografías procesadas",
    "DOCUMENTO":   "📄 Documento adjuntado",
    "NOTA":        "📝 Nota registrada",
    "CIERRE":      "✅ Proyecto cerrado",
}
_ESTADO_COLOR = {
    "ACTIVO":     ("#155724", "#d4edda"),
    "EN_PROCESO": ("#856404", "#fff3cd"),
    "CERRADO":    ("#6c757d", "#f8f9fa"),
}

# ---------------------------------------------------------------------------
# Conexión BD (reutiliza el patrón de app.py)
# ---------------------------------------------------------------------------

def _conn() -> psycopg2.extensions.connection:
    """Abre conexión a PostgreSQL desde st.secrets."""
    try:
        return psycopg2.connect(st.secrets["DATABASE_URL"])
    except (KeyError, FileNotFoundError) as exc:
        raise RuntimeError(
            "DATABASE_URL no encontrada en secrets.toml"
        ) from exc


# ---------------------------------------------------------------------------
# 1. MIGRACIÓN BD FASE 5 — idempotente, segura en cada arranque
# ---------------------------------------------------------------------------

def migrar_bd_fase5() -> bool:
    """
    Crea las nuevas tablas y columnas necesarias para Fase 5.
    Usa IF NOT EXISTS y ADD COLUMN IF NOT EXISTS → seguro ejecutar siempre.
    Retorna True si todo ok, False si hay error.
    """
    try:
        conn = _conn()
        c    = conn.cursor()

        # ── Tabla: proyecto_historial ────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS proyecto_historial (
                id_evento    SERIAL PRIMARY KEY,
                id_proyecto  TEXT NOT NULL REFERENCES proyectos(id_proyecto) ON DELETE CASCADE,
                usuario      TEXT NOT NULL DEFAULT 'Sistema',
                accion       TEXT NOT NULL,
                descripcion  TEXT,
                metadata_json TEXT,
                fecha_evento TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Tabla: proyecto_notas ────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS proyecto_notas (
                id_nota       SERIAL PRIMARY KEY,
                id_proyecto   TEXT NOT NULL REFERENCES proyectos(id_proyecto) ON DELETE CASCADE,
                usuario       TEXT NOT NULL DEFAULT 'Ingeniero',
                texto         TEXT NOT NULL,
                tipo          TEXT NOT NULL DEFAULT 'TÉCNICA',
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Tabla: proyecto_documentos ───────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS proyecto_documentos (
                id_doc        SERIAL PRIMARY KEY,
                id_proyecto   TEXT NOT NULL REFERENCES proyectos(id_proyecto) ON DELETE CASCADE,
                tipo_doc      TEXT NOT NULL DEFAULT 'OTRO',
                nombre_archivo TEXT NOT NULL,
                contenido_b64  TEXT,
                version       INTEGER NOT NULL DEFAULT 1,
                notas         TEXT,
                usuario_subida TEXT DEFAULT 'Ingeniero',
                fecha_subida  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Nuevas columnas en tabla proyectos ───────────────────────────
        nuevas_cols = [
            ("responsable",          "TEXT DEFAULT ''"),
            ("fecha_siniestro",      "TEXT DEFAULT ''"),
            ("km_autopista",         "TEXT DEFAULT ''"),
            ("nombre_autopista",     "TEXT DEFAULT ''"),
            ("contaminante",         "TEXT DEFAULT 'Gasolina (HFL)'"),
            ("volumen_litros",       "TEXT DEFAULT ''"),
            ("coordenadas_siniestro","TEXT DEFAULT ''"),
            ("area_afectada_m2",     "TEXT DEFAULT ''"),
            ("estado_proyecto",      "TEXT DEFAULT 'ACTIVO'"),
            ("icti_ultimo",          "INTEGER DEFAULT 0"),
            ("notas_generales",      "TEXT DEFAULT ''"),
        ]
        for col, tipo in nuevas_cols:
            try:
                c.execute(
                    f"ALTER TABLE proyectos ADD COLUMN IF NOT EXISTS {col} {tipo}"
                )
            except Exception:
                conn.rollback()

        conn.commit()
        c.close()
        conn.close()
        return True

    except Exception as exc:
        st.session_state["_fase5_error"] = str(exc)
        return False


# ---------------------------------------------------------------------------
# 2. HISTORIAL — registro automático de eventos
# ---------------------------------------------------------------------------

def registrar_evento(
    id_proyecto: str,
    accion:      str,
    descripcion: str = "",
    usuario:     str = "",
    metadata:    dict | None = None,
) -> None:
    """
    Registra un evento en el historial del proyecto.
    Llamado automáticamente desde H1, H2, H3, H4.

    Args:
        id_proyecto: ID del proyecto activo.
        accion:      Clave de _ACCIONES_VALIDAS (ej. 'LABORATORIO').
        descripcion: Texto libre del evento.
        usuario:     Usuario que ejecutó la acción.
        metadata:    Dict con datos adicionales del evento (serializable a JSON).
    """
    if not id_proyecto:
        return
    if not usuario:
        usuario = st.session_state.get("usuario_actual", "Sistema")
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """INSERT INTO proyecto_historial
               (id_proyecto, usuario, accion, descripcion, metadata_json)
               VALUES (%s, %s, %s, %s, %s)""",
            (
                id_proyecto,
                usuario,
                accion,
                descripcion,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        c.close()
        conn.close()
    except Exception:
        pass   # El historial nunca debe romper el flujo principal


def cargar_historial(id_proyecto: str, limite: int = 50) -> list[dict]:
    """Carga los últimos N eventos del historial de un proyecto."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """SELECT id_evento, usuario, accion, descripcion,
                      metadata_json, fecha_evento
               FROM proyecto_historial
               WHERE id_proyecto = %s
               ORDER BY fecha_evento DESC
               LIMIT %s""",
            (id_proyecto, limite),
        )
        rows = c.fetchall()
        c.close()
        conn.close()
        return [
            {
                "id":          r[0],
                "usuario":     r[1],
                "accion":      r[2],
                "descripcion": r[3],
                "metadata":    json.loads(r[4]) if r[4] else {},
                "fecha":       r[5],
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 3. NOTAS DEL EQUIPO
# ---------------------------------------------------------------------------

def guardar_nota(
    id_proyecto: str,
    texto:       str,
    tipo:        str = "TÉCNICA",
    usuario:     str = "",
) -> bool:
    """Guarda una nota técnica asociada al proyecto."""
    if not usuario:
        usuario = st.session_state.get("usuario_actual", "Ingeniero")
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """INSERT INTO proyecto_notas (id_proyecto, usuario, texto, tipo)
               VALUES (%s, %s, %s, %s)""",
            (id_proyecto, usuario, texto.strip(), tipo),
        )
        conn.commit()
        c.close()
        conn.close()
        registrar_evento(id_proyecto, "NOTA",
                         f"Nota {tipo.lower()} registrada por {usuario}")
        return True
    except Exception as exc:
        st.error(f"Error al guardar nota: {exc}")
        return False


def cargar_notas(id_proyecto: str) -> list[dict]:
    """Carga todas las notas de un proyecto, más recientes primero."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """SELECT id_nota, usuario, texto, tipo, fecha_creacion
               FROM proyecto_notas
               WHERE id_proyecto = %s
               ORDER BY fecha_creacion DESC""",
            (id_proyecto,),
        )
        rows = c.fetchall()
        c.close()
        conn.close()
        return [
            {"id": r[0], "usuario": r[1], "texto": r[2],
             "tipo": r[3], "fecha": r[4]}
            for r in rows
        ]
    except Exception:
        return []


def eliminar_nota(id_nota: int) -> bool:
    """Elimina una nota por ID."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute("DELETE FROM proyecto_notas WHERE id_nota = %s", (id_nota,))
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 4. GESTIÓN DOCUMENTAL
# ---------------------------------------------------------------------------

def guardar_documento_db(
    id_proyecto:  str,
    tipo_doc:     str,
    nombre:       str,
    contenido:    bytes,
    notas:        str = "",
    usuario:      str = "",
) -> bool:
    """
    Guarda un documento en la BD asociado al proyecto.
    Incrementa la versión automáticamente si ya existe el mismo nombre.
    """
    if not usuario:
        usuario = st.session_state.get("usuario_actual", "Ingeniero")
    import base64
    try:
        conn = _conn()
        c    = conn.cursor()
        # Verificar si ya existe para versionar
        c.execute(
            """SELECT COALESCE(MAX(version), 0) FROM proyecto_documentos
               WHERE id_proyecto = %s AND nombre_archivo = %s""",
            (id_proyecto, nombre),
        )
        version_actual = c.fetchone()[0] + 1
        b64 = base64.b64encode(contenido).decode("utf-8")
        c.execute(
            """INSERT INTO proyecto_documentos
               (id_proyecto, tipo_doc, nombre_archivo, contenido_b64,
                version, notas, usuario_subida)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (id_proyecto, tipo_doc, nombre, b64, version_actual, notas, usuario),
        )
        conn.commit()
        c.close()
        conn.close()
        registrar_evento(
            id_proyecto, "DOCUMENTO",
            f"Documento '{nombre}' v{version_actual} adjuntado ({tipo_doc})",
            usuario, {"tipo": tipo_doc, "version": version_actual},
        )
        return True
    except Exception as exc:
        st.error(f"Error al guardar documento: {exc}")
        return False


def cargar_documentos(id_proyecto: str) -> list[dict]:
    """Carga la lista de documentos de un proyecto (sin contenido binario)."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """SELECT id_doc, tipo_doc, nombre_archivo, version,
                      notas, usuario_subida, fecha_subida
               FROM proyecto_documentos
               WHERE id_proyecto = %s
               ORDER BY fecha_subida DESC""",
            (id_proyecto,),
        )
        rows = c.fetchall()
        c.close()
        conn.close()
        return [
            {
                "id":       r[0], "tipo":    r[1], "nombre":  r[2],
                "version":  r[3], "notas":   r[4], "usuario": r[5],
                "fecha":    r[6],
            }
            for r in rows
        ]
    except Exception:
        return []


def descargar_documento(id_doc: int) -> tuple[bytes, str] | None:
    """Descarga el contenido binario de un documento por ID."""
    import base64
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            "SELECT contenido_b64, nombre_archivo FROM proyecto_documentos WHERE id_doc = %s",
            (id_doc,),
        )
        row = c.fetchone()
        c.close()
        conn.close()
        if row:
            return base64.b64decode(row[0]), row[1]
    except Exception:
        pass
    return None


def eliminar_documento(id_doc: int) -> bool:
    """Elimina un documento por ID."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute("DELETE FROM proyecto_documentos WHERE id_doc = %s", (id_doc,))
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 5. ACTUALIZAR DATOS DEL PROYECTO
# ---------------------------------------------------------------------------

def actualizar_proyecto(id_proyecto: str, campos: dict) -> bool:
    """
    Actualiza campos del proyecto en la tabla proyectos.
    Solo actualiza los campos presentes en el dict.
    """
    if not campos:
        return False
    try:
        conn    = _conn()
        c       = conn.cursor()
        sets    = ", ".join(f"{k} = %s" for k in campos)
        valores = list(campos.values()) + [id_proyecto]
        c.execute(f"UPDATE proyectos SET {sets} WHERE id_proyecto = %s", valores)
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception as exc:
        st.error(f"Error al actualizar proyecto: {exc}")
        return False


def cargar_todos_proyectos() -> list[dict]:
    """
    Carga todos los proyectos con métricas agregadas para la tabla maestra.

    Causa raíz del bug original: el SELECT con JOIN fallaba silenciosamente
    (except Exception: return []) cuando proyectos creados ANTES de la
    migración Fase 5 tenían columnas NULL o cuando el ALTER TABLE no se
    había ejecutado aún en esa sesión. El error quedaba oculto al usuario.

    Solución: 
      1. Intento principal con JOIN completo + COALESCE para columnas nuevas.
      2. Si falla, fallback a SELECT básico sin las columnas de Fase 5
         (degradación elegante — el gestor sigue funcionando).
      3. Si ambos fallan, se guarda el error real en session_state para
         mostrarlo en la UI en vez de devolver una lista vacía silenciosa.
    """
    # ── Intento principal: SELECT completo con JOIN y COALESCE ────────────
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute("""
            SELECT
                p.id_proyecto,
                p.nombre_siniestro,
                COALESCE(p.uso_de_suelo, 'Agrícola/Forestal'),
                COALESCE(p.estado_proyecto, 'ACTIVO'),
                COALESCE(p.responsable, ''),
                COALESCE(p.fecha_siniestro, ''),
                COALESCE(p.km_autopista, ''),
                COALESCE(p.contaminante, ''),
                COALESCE(p.icti_ultimo, 0),
                p.fecha_creacion,
                COUNT(DISTINCT dl.id_registro) AS n_muestras,
                COUNT(DISTINCT fs.id_foto)     AS n_fotos,
                COALESCE(SUM(CASE WHEN dl.rebase_nom THEN 1 ELSE 0 END), 0) AS n_rebase
            FROM proyectos p
            LEFT JOIN datos_laboratorio dl ON dl.id_proyecto = p.id_proyecto
            LEFT JOIN fotos_sistema     fs ON fs.id_proyecto = p.id_proyecto
            GROUP BY p.id_proyecto, p.nombre_siniestro, p.uso_de_suelo,
                     p.estado_proyecto, p.responsable, p.fecha_siniestro,
                     p.km_autopista, p.contaminante, p.icti_ultimo, p.fecha_creacion
            ORDER BY p.fecha_creacion DESC
        """)
        rows = c.fetchall()
        c.close()
        conn.close()
        st.session_state.pop("_gestor_error", None)   # limpiar error previo
        return [
            {
                "id":            r[0],  "nombre":     r[1],
                "uso_suelo":     r[2],  "estado":     r[3],
                "responsable":   r[4],  "fecha_sin":  r[5],
                "km":            r[6],  "contaminante":r[7],
                "icti":          r[8],
                "fecha_creacion":r[9],
                "n_muestras":    r[10] or 0,
                "n_fotos":       r[11] or 0,
                "n_rebase":      r[12] or 0,
            }
            for r in rows
        ]

    except Exception as exc_principal:
        # ── Fallback: SELECT básico sin columnas de Fase 5 ────────────────
        try:
            conn = _conn()
            c    = conn.cursor()
            c.execute("""
                SELECT
                    p.id_proyecto, p.nombre_siniestro, p.uso_de_suelo,
                    p.estado, p.fecha_creacion,
                    COUNT(DISTINCT dl.id_registro) AS n_muestras,
                    COUNT(DISTINCT fs.id_foto)     AS n_fotos
                FROM proyectos p
                LEFT JOIN datos_laboratorio dl ON dl.id_proyecto = p.id_proyecto
                LEFT JOIN fotos_sistema     fs ON fs.id_proyecto = p.id_proyecto
                GROUP BY p.id_proyecto, p.nombre_siniestro, p.uso_de_suelo,
                         p.estado, p.fecha_creacion
                ORDER BY p.fecha_creacion DESC
            """)
            rows = c.fetchall()
            c.close()
            conn.close()
            st.session_state["_gestor_error"] = (
                f"Modo degradado activo (columnas de Fase 5 no disponibles): "
                f"{exc_principal}"
            )
            return [
                {
                    "id": r[0], "nombre": r[1], "uso_suelo": r[2],
                    "estado": r[3] or "ACTIVO", "responsable": "",
                    "fecha_sin": "", "km": "", "contaminante": "",
                    "icti": 0, "fecha_creacion": r[4],
                    "n_muestras": r[5] or 0, "n_fotos": r[6] or 0,
                    "n_rebase": 0,
                }
                for r in rows
            ]
        except Exception as exc_fallback:
            # Ambos intentos fallaron — guardar error real para mostrarlo
            st.session_state["_gestor_error"] = (
                f"Error de conexión o consulta a la base de datos: {exc_fallback}"
            )
            return []


# ---------------------------------------------------------------------------
# RENDERIZADO — Dashboard del proyecto activo
# ---------------------------------------------------------------------------

def render_dashboard_proyecto(
    id_proyecto:       str,
    historial_lab:     list[dict],
    n_fotos:           int,
    icti:              int,
    detalles_proyecto: dict | None,
    usuario_actual:    str = "Ingeniero",
) -> None:
    """
    Dashboard ejecutivo del proyecto activo con:
    - Métricas clave
    - Datos del proyecto (editable)
    - Timeline de actividades
    - Notas del equipo
    - Gestión de documentos
    """
    st.header("🗂️ Dashboard del Proyecto")

    if not id_proyecto:
        st.warning("⚠️ Selecciona un proyecto en la barra lateral.")
        return

    det = detalles_proyecto or {}

    # ── Encabezado del proyecto ────────────────────────────────────────────
    estado_actual = det.get("estado_proyecto", "ACTIVO")
    col_fg, col_bg = _ESTADO_COLOR.get(estado_actual, ("#333", "#eee"))
    st.markdown(
        f'<span style="background:{col_bg};color:{col_fg};padding:4px 12px;'
        f'border-radius:6px;font-weight:bold;font-size:13px">'
        f'Estado: {estado_actual}</span>',
        unsafe_allow_html=True,
    )
    st.markdown(f"### {det.get('nombre', id_proyecto)}")
    st.caption(f"Proyecto: `{id_proyecto}` · "
               f"Uso de suelo: {det.get('uso_de_suelo','—')} · "
               f"Responsable: {det.get('responsable','—')}")
    st.markdown("---")

    # ── Métricas ejecutivas ────────────────────────────────────────────────
    n_muestras = len(historial_lab)
    n_rebase   = sum(1 for h in historial_lab if h.get("rebase"))

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("🧪 Muestras",       n_muestras)
    c2.metric("🚨 Exceden LMP",    n_rebase,
              delta=f"{round(n_rebase/n_muestras*100) if n_muestras else 0}%",
              delta_color="inverse")
    c3.metric("📷 Fotografías",    n_fotos)
    c4.metric("📋 ICTI",           f"{icti}/100",
              delta="Aprobado" if icti >= 80 else
                    "Observaciones" if icti >= 60 else
                    "Deficiente" if icti >= 40 else "Rechazable",
              delta_color="normal" if icti >= 60 else "inverse")
    c5.metric("📄 Documentos",
              len(cargar_documentos(id_proyecto)))

    st.markdown("---")

    # ── 4 tabs principales ─────────────────────────────────────────────────
    tab_info, tab_hist, tab_notas, tab_docs = st.tabs([
        "📋 Información del proyecto",
        "📅 Historial de actividades",
        "📝 Notas del equipo",
        "📁 Documentos adjuntos",
    ])

    # ── TAB 1: Información editable del proyecto ───────────────────────────
    with tab_info:
        st.subheader("Datos del siniestro")
        with st.form("form_editar_proyecto"):
            col1, col2 = st.columns(2)
            with col1:
                responsable    = st.text_input("Responsable técnico",
                                               value=det.get("responsable",""))
                fecha_sin      = st.text_input("Fecha del siniestro",
                                               value=det.get("fecha_siniestro",""))
                km_auto        = st.text_input("Km de la autopista",
                                               value=det.get("km_autopista",""))
                nombre_auto    = st.text_input("Nombre de la autopista",
                                               value=det.get("nombre_autopista",""))
                contaminante   = st.text_input("Contaminante",
                                               value=det.get("contaminante",
                                                             "Gasolina (HFL)"))
            with col2:
                volumen        = st.text_input("Volumen derramado (L)",
                                               value=det.get("volumen_litros",""))
                coords         = st.text_input("Coordenadas del siniestro",
                                               value=det.get("coordenadas_siniestro",""))
                area           = st.text_input("Área afectada (m²)",
                                               value=det.get("area_afectada_m2",""))
                estado_proy    = st.selectbox(
                    "Estado del proyecto",
                    ["ACTIVO", "EN_PROCESO", "CERRADO"],
                    index=["ACTIVO","EN_PROCESO","CERRADO"].index(
                        det.get("estado_proyecto","ACTIVO"))
                    if det.get("estado_proyecto") in ["ACTIVO","EN_PROCESO","CERRADO"]
                    else 0,
                )
                notas_gen      = st.text_area("Notas generales",
                                              value=det.get("notas_generales",""),
                                              height=80)

            if st.form_submit_button("💾 Guardar cambios", type="primary"):
                ok = actualizar_proyecto(id_proyecto, {
                    "responsable":           responsable,
                    "fecha_siniestro":       fecha_sin,
                    "km_autopista":          km_auto,
                    "nombre_autopista":      nombre_auto,
                    "contaminante":          contaminante,
                    "volumen_litros":        volumen,
                    "coordenadas_siniestro": coords,
                    "area_afectada_m2":      area,
                    "estado_proyecto":       estado_proy,
                    "notas_generales":       notas_gen,
                    "icti_ultimo":           icti,
                })
                if ok:
                    registrar_evento(id_proyecto, "NOTA",
                                     "Datos del proyecto actualizados",
                                     usuario_actual)
                    st.success("✅ Proyecto actualizado.")
                    st.rerun()

    # ── TAB 2: Historial de actividades ────────────────────────────────────
    with tab_hist:
        historial_ev = cargar_historial(id_proyecto)
        if not historial_ev:
            st.info("Sin actividades registradas aún.")
        else:
            st.caption(f"Últimas {len(historial_ev)} actividades del proyecto")
            for ev in historial_ev:
                accion  = ev["accion"]
                label   = _ACCIONES_VALIDAS.get(accion, accion)
                fecha   = ev["fecha"]
                fecha_s = fecha.strftime("%d/%m/%Y %H:%M") if fecha else "—"
                desc    = ev["descripcion"] or ""
                st.markdown(
                    f'<div style="border-left:3px solid #1F3864;'
                    f'padding:6px 12px;margin-bottom:6px;background:#f8f9fa;'
                    f'border-radius:0 6px 6px 0">'
                    f'<b>{label}</b> &nbsp;·&nbsp; '
                    f'<small style="color:#666">{fecha_s} &nbsp;·&nbsp; '
                    f'{ev["usuario"]}</small><br>'
                    f'<small>{desc}</small></div>',
                    unsafe_allow_html=True,
                )

    # ── TAB 3: Notas del equipo ────────────────────────────────────────────
    with tab_notas:
        # Formulario para agregar nota
        with st.expander("➕ Agregar nota", expanded=False):
            with st.form("form_nota"):
                tipo_nota  = st.selectbox("Tipo de nota", _TIPOS_NOTA)
                texto_nota = st.text_area("Texto de la nota", height=100)
                if st.form_submit_button("💾 Guardar nota", type="primary"):
                    if texto_nota.strip():
                        if guardar_nota(id_proyecto, texto_nota,
                                        tipo_nota, usuario_actual):
                            st.success("✅ Nota guardada.")
                            st.rerun()
                    else:
                        st.warning("Escribe el texto de la nota.")

        # Lista de notas existentes
        notas = cargar_notas(id_proyecto)
        if not notas:
            st.info("Sin notas registradas aún.")
        else:
            tipo_icon = {"TÉCNICA": "🔬", "REGULATORIA": "🏛️", "OPERATIVA": "⚙️"}
            for nota in notas:
                icono = tipo_icon.get(nota["tipo"], "📝")
                fecha_n = nota["fecha"]
                fecha_s = fecha_n.strftime("%d/%m/%Y %H:%M") if fecha_n else "—"
                col_t, col_btn = st.columns([10, 1])
                with col_t:
                    st.markdown(
                        f'{icono} **{nota["tipo"]}** — '
                        f'<small style="color:#666">{fecha_s} · {nota["usuario"]}</small><br>'
                        f'{nota["texto"]}',
                        unsafe_allow_html=True,
                    )
                with col_btn:
                    if st.button("🗑️", key=f"del_nota_{nota['id']}",
                                 help="Eliminar nota"):
                        if eliminar_nota(nota["id"]):
                            st.rerun()
                st.markdown('<hr style="margin:4px 0;border-color:#eee">',
                            unsafe_allow_html=True)

    # ── TAB 4: Documentos adjuntos ─────────────────────────────────────────
    with tab_docs:
        # Subida de documentos
        with st.expander("➕ Adjuntar documento", expanded=False):
            with st.form("form_doc"):
                tipo_doc   = st.selectbox("Tipo de documento", _TIPOS_DOC)
                notas_doc  = st.text_input("Notas del documento (opcional)")
                archivo    = st.file_uploader(
                    "Selecciona el archivo",
                    type=["pdf", "xlsx", "xls", "docx", "txt", "csv",
                          "jpg", "jpeg", "png"],
                )
                if st.form_submit_button("📎 Adjuntar", type="primary"):
                    if archivo:
                        ok = guardar_documento_db(
                            id_proyecto, tipo_doc,
                            archivo.name, archivo.read(),
                            notas_doc, usuario_actual,
                        )
                        if ok:
                            st.success(f"✅ '{archivo.name}' adjuntado.")
                            st.rerun()
                    else:
                        st.warning("Selecciona un archivo.")

        # Lista de documentos
        docs = cargar_documentos(id_proyecto)
        if not docs:
            st.info("Sin documentos adjuntos aún.")
        else:
            tipo_icon_doc = {
                "PDF_LAB":         "🧪",
                "PDF_INFORME":     "📄",
                "REPORTE_GENERADO":"🤖",
                "MAPA":            "🗺️",
                "OTRO":            "📎",
            }
            for doc in docs:
                icono = tipo_icon_doc.get(doc["tipo"], "📎")
                fecha_d = doc["fecha"]
                fecha_s = fecha_d.strftime("%d/%m/%Y %H:%M") if fecha_d else "—"
                col_inf, col_dl, col_del = st.columns([8, 1, 1])

                with col_inf:
                    st.markdown(
                        f'{icono} **{doc["nombre"]}** '
                        f'<span style="background:#e9ecef;padding:1px 6px;'
                        f'border-radius:4px;font-size:11px">v{doc["version"]}</span>'
                        f' — {doc["tipo"]}<br>'
                        f'<small style="color:#666">{fecha_s} · {doc["usuario"]}'
                        f'{" · " + doc["notas"] if doc["notas"] else ""}</small>',
                        unsafe_allow_html=True,
                    )

                with col_dl:
                    resultado = descargar_documento(doc["id"])
                    if resultado:
                        contenido_bytes, nombre_archivo = resultado
                        st.download_button(
                            "⬇️",
                            data=contenido_bytes,
                            file_name=nombre_archivo,
                            key=f"dl_doc_{doc['id']}",
                            help="Descargar",
                        )

                with col_del:
                    if st.button("🗑️", key=f"del_doc_{doc['id']}",
                                 help="Eliminar"):
                        if eliminar_documento(doc["id"]):
                            registrar_evento(
                                id_proyecto, "DOCUMENTO",
                                f"Documento '{doc['nombre']}' eliminado",
                                usuario_actual,
                            )
                            st.rerun()

                st.markdown('<hr style="margin:4px 0;border-color:#eee">',
                            unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# RENDERIZADO — Tabla maestra de todos los proyectos
# ---------------------------------------------------------------------------

def render_gestor_proyectos(usuario_actual: str = "Ingeniero") -> None:
    """
    Vista de todos los proyectos con métricas agregadas.
    Permite filtrar por estado y ver el resumen de cada uno.

    Nunca muestra pantalla vacía sin explicación:
    - Si hay error de BD/consulta → muestra el error real (no silencioso).
    - Si la BD responde pero no hay proyectos → mensaje claro de "sin proyectos".
    - Si hay proyectos → renderiza la tabla normalmente.
    """
    st.header("📊 Gestor de Proyectos")
    st.caption("Vista maestra de todos los proyectos del Hub Ambiental.")

    proyectos = cargar_todos_proyectos()

    # ── Caso 1: hubo un error real en la consulta — mostrarlo, no ocultarlo ──
    error_gestor = st.session_state.get("_gestor_error")
    if error_gestor:
        st.warning(f"⚠️ {error_gestor}")

    # ── Caso 2: la consulta funcionó pero no existen proyectos ──────────────
    if not proyectos:
        st.info(
            "📭 **No existen proyectos creados.**\n\n"
            "Crea tu primer proyecto desde el formulario "
            "**➕ Crear nuevo proyecto** en la barra lateral."
        )
        return

    # ── Caso 3: hay proyectos — continuar con la vista normal ───────────────
    st.success(f"✅ {len(proyectos)} proyecto(s) cargado(s) correctamente.")

    # ── Filtros ────────────────────────────────────────────────────────────
    col_f1, col_f2, _ = st.columns([2, 2, 4])
    with col_f1:
        filtro_estado = st.selectbox(
            "Filtrar por estado:",
            ["Todos", "ACTIVO", "EN_PROCESO", "CERRADO"],
        )
    with col_f2:
        filtro_texto = st.text_input("Buscar:", placeholder="ID o nombre")

    # Aplicar filtros
    proy_filtrados = proyectos
    if filtro_estado != "Todos":
        proy_filtrados = [p for p in proy_filtrados
                          if p["estado"] == filtro_estado]
    if filtro_texto:
        t = filtro_texto.lower()
        proy_filtrados = [
            p for p in proy_filtrados
            if t in p["id"].lower() or t in p["nombre"].lower()
        ]

    # ── Métricas globales ──────────────────────────────────────────────────
    total      = len(proyectos)
    activos    = sum(1 for p in proyectos if p["estado"] == "ACTIVO")
    en_proceso = sum(1 for p in proyectos if p["estado"] == "EN_PROCESO")
    cerrados   = sum(1 for p in proyectos if p["estado"] == "CERRADO")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total proyectos",  total)
    m2.metric("🟢 Activos",       activos)
    m3.metric("🟡 En proceso",    en_proceso)
    m4.metric("✅ Cerrados",      cerrados)
    st.markdown("---")

    # ── Tabla de proyectos ─────────────────────────────────────────────────
    st.caption(f"Mostrando {len(proy_filtrados)} de {total} proyectos")

    ICTI_NIVEL = {
        range(80, 101): "🟢",
        range(60,  80): "🟡",
        range(40,  60): "🟠",
        range(0,   40): "🔴",
    }

    def icti_emoji(v: int) -> str:
        for rng, em in ICTI_NIVEL.items():
            if v in rng:
                return em
        return "⚪"

    filas = []
    for p in proy_filtrados:
        fecha_c = p["fecha_creacion"]
        fecha_s = fecha_c.strftime("%d/%m/%Y") if fecha_c else "—"
        filas.append({
            "ID":          p["id"],
            "Siniestro":   p["nombre"],
            "Estado":      p["estado"],
            "Km / Autopista": p["km"] or "—",
            "Contaminante":p["contaminante"] or "—",
            "Muestras":    p["n_muestras"],
            "Rebase":      p["n_rebase"],
            "Fotos":       p["n_fotos"],
            "ICTI":        f"{icti_emoji(p['icti'])} {p['icti']}/100",
            "Responsable": p["responsable"] or "—",
            "Creado":      fecha_s,
        })

    df = pd.DataFrame(filas)

    def color_estado(v: str) -> str:
        mapa = {
            "ACTIVO":     "background:#d4edda;color:#155724",
            "EN_PROCESO": "background:#fff3cd;color:#856404",
            "CERRADO":    "background:#f8f9fa;color:#6c757d",
        }
        return mapa.get(v, "")

    st.dataframe(
        df.style.map(color_estado, subset=["Estado"]),
        use_container_width=True,
        hide_index=True,
    )

    # ── Descarga de la tabla maestra ───────────────────────────────────────
    st.download_button(
        "⬇️ Exportar tabla maestra (CSV)",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="proyectos_hub_ambiental.csv",
        mime="text/csv",
    )
