"""
Módulo: tabla_nom138.py
Generador de la tabla NOM-138 estilo NOVALABSA para el Hub Ambiental.

Responsabilidad única: tomar los datos del expediente analítico de la BD
y producir la tabla HTML idéntica a la imagen de referencia de NOVALABSA,
con celdas de zona fusionadas, resaltado cyan en valores que superan el LMP,
y fila de LMP al pie con fondo amarillo.

NO modifica el app.py principal — se importa como módulo.
"""

from __future__ import annotations
from typing import Any
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# ── Constantes de visualización ──────────────────────────────────────────────
_COLOR_EXCEDE   = "#00B0F0"   # Azul/cyan exacto de la imagen NOVALABSA
_COLOR_EXCEDE_T = "#000000"   # Texto negro sobre cyan
_COLOR_LMP_BG   = "#FFFF99"   # Fondo amarillo para la fila de LMP
_COLOR_LMP_T    = "#000000"
_COLOR_HEADER   = "#1F3864"   # Azul oscuro del encabezado NOVALABSA
_COLOR_ZONA_BG  = "#D9E1F2"   # Azul claro para celdas de zona
_COLOR_NOTA_BG  = "#F2F2F2"

# Límites de cuantificación exactos del laboratorio NOVALABSA
_LC = {
    "HFL":         4.68,
    "Benceno":     0.030,
    "Tolueno":     0.10,
    "Etilbenceno": 0.20,
    "Xilenos":     0.30,   # m,p-Xilenos (valor más alto)
}

_PARAMS = ["HFL", "Benceno", "Tolueno", "Etilbenceno", "Xilenos"]

_NOTA_PIE = (
    "&lt; L.C.: LÍMITE CUANTIFICABLE &nbsp;&nbsp;"
    "HFL = 4.68 mg/kg &nbsp;·&nbsp; BENCENO = 0.030 mg/kg &nbsp;·&nbsp; "
    "TOLUENO = 0.10 mg/kg &nbsp;·&nbsp; ETILBENCENO = 0.20 mg/kg &nbsp;·&nbsp; "
    "m,p-XILENOS = 0.30 mg/kg &nbsp;·&nbsp; o-XILENO = 0.20 mg/kg"
)

_LEYENDA_LMP = (
    "VALORES POR ARRIBA DE LOS LÍMITES MÁXIMOS PERMISIBLES "
    "MARCADOS POR LA NOM-138-SEMARNAT/SSA1-2012"
)


def _fmt_valor(val: Any, param: str, lc: dict) -> tuple[str, bool]:
    """
    Convierte un valor numérico de la BD a su representación en celda:
    - Si val == 0.0 y el parámetro tiene LC → muestra "< L.C."
    - Si val > 0  → muestra el número con 2-3 decimales
    Retorna (texto_celda, supera_lmp_bool).
    Nota: 'supera_lmp' se evalúa en la capa superior con los LMP del proyecto.
    """
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "—", False

    if v <= 0.0:
        return "&lt; L.C.", False

    # Formateo numérico: sin ceros innecesarios pero con precisión suficiente
    if v >= 100:
        texto = f"{v:,.2f}"
    elif v >= 10:
        texto = f"{v:.2f}"
    else:
        texto = f"{v:.3f}"

    return texto, True


def _celda(
    contenido: str,
    supera: bool = False,
    es_lmp: bool = False,
    es_zona: bool = False,
    rowspan: int = 1,
    center: bool = True,
    bold: bool = False,
    extra_style: str = "",
) -> str:
    """Genera un <td> HTML con el estilo correcto."""
    styles = ["padding:4px 6px", "border:1px solid #BFBFBF", "font-size:11px"]

    if es_zona:
        styles += [
            f"background:{_COLOR_ZONA_BG}",
            "font-weight:bold",
            "vertical-align:middle",
            "text-align:center",
        ]
    elif es_lmp:
        styles += [
            f"background:{_COLOR_LMP_BG}",
            f"color:{_COLOR_LMP_T}",
            "font-weight:bold",
            "text-align:center",
        ]
    elif supera:
        styles += [
            f"background:{_COLOR_EXCEDE}",
            f"color:{_COLOR_EXCEDE_T}",
            "font-weight:bold",
            "text-align:center",
        ]
    else:
        if center:
            styles.append("text-align:center")

    if bold and not es_zona and not es_lmp:
        styles.append("font-weight:bold")

    if extra_style:
        styles.append(extra_style)

    rs = f' rowspan="{rowspan}"' if rowspan > 1 else ""
    style_str = ";".join(styles)
    return f'<td{rs} style="{style_str}">{contenido}</td>'


def generar_tabla_nom138_html(
    historial: list[dict],
    lim_vigentes: dict[str, float],
    titulo_proyecto: str,
    uso_suelo: str,
    nombre_siniestro: str = "",
) -> str:
    """
    Genera el HTML completo de la tabla NOM-138 estilo NOVALABSA.

    Args:
        historial:        Lista de dicts de cargar_laboratorio_proyecto()
        lim_vigentes:     Dict con LMP por parámetro según uso de suelo
        titulo_proyecto:  ID del proyecto (ej. OT-2026-001)
        uso_suelo:        Uso de suelo activo del proyecto
        nombre_siniestro: Nombre descriptivo del siniestro

    Returns:
        String HTML completo de la tabla, listo para st.components.v1.html()
    """
    if not historial:
        return "<p style='font-family:sans-serif;color:#666'>Sin datos en el expediente.</p>"

    # ── 1. Ordenar y agrupar por zona ──────────────────────────────────────
    # Orden canónico de zonas para que aparezcan en el orden correcto
    orden_zona = {"ZONA 1": 0, "ZONA 2": 1, "ZONA 3": 2,
                  "PERIFERIA": 3, "CAMPO": 4}

    def sort_key(h: dict) -> tuple:
        zona  = str(h.get("zona", "CAMPO")).upper().strip()
        muest = str(h.get("id_muestra", "")).strip()
        # Extraer número de pozo y profundidad para ordenar numéricamente
        import re
        nums = re.findall(r"[\d.]+", muest)
        n1 = float(nums[0]) if len(nums) > 0 else 0
        n2 = float(nums[1]) if len(nums) > 1 else 0
        dup = 1 if "dup" in muest.lower() else 0
        return (orden_zona.get(zona, 99), n1, n2, dup)

    historial_sorted = sorted(historial, key=sort_key)

    # Agrupar por zona
    from itertools import groupby
    grupos: list[tuple[str, list[dict]]] = []
    for zona, items in groupby(
        historial_sorted,
        key=lambda h: str(h.get("zona", "CAMPO")).upper().strip()
    ):
        grupos.append((zona, list(items)))

    # ── 2. Título del documento ────────────────────────────────────────────
    titulo_tabla = (
        nombre_siniestro.upper()
        if nombre_siniestro
        else f"MUESTREO — PROYECTO {titulo_proyecto.upper()}"
    )

    # ── 3. Construir HTML ──────────────────────────────────────────────────
    rows = []

    # Cabecera del documento
    rows.append(f"""
    <tr>
      <td colspan="12" style="
        background:{_COLOR_HEADER};color:white;font-weight:bold;
        font-size:12px;text-align:center;padding:8px 6px;
        border:1px solid #BFBFBF;
      ">{titulo_tabla}</td>
    </tr>
    """)

    # Fila de uso de suelo
    rows.append(f"""
    <tr>
      <td colspan="12" style="
        background:#D6E4F7;font-size:10px;text-align:center;
        padding:3px 6px;border:1px solid #BFBFBF;color:#1F3864;
      ">
        Evaluación conforme a <strong>NOM-138-SEMARNAT/SSA1-2012</strong>
        &nbsp;|&nbsp; Uso de suelo: <strong>{uso_suelo}</strong>
        &nbsp;|&nbsp; Proyecto: <strong>{titulo_proyecto}</strong>
      </td>
    </tr>
    """)

    # Encabezados de columna — fila 1 (grupos)
    rows.append(f"""
    <tr style="background:{_COLOR_HEADER};color:white;font-size:10px;font-weight:bold;text-align:center">
      <td rowspan="2" style="border:1px solid #BFBFBF;padding:5px 4px;vertical-align:middle">ZONA<br>AFECTADA</td>
      <td rowspan="2" style="border:1px solid #BFBFBF;padding:5px 4px;vertical-align:middle">IDENTIFICACIÓN<br>DE LA MUESTRA</td>
      <td rowspan="2" style="border:1px solid #BFBFBF;padding:5px 4px;vertical-align:middle">PROFUNDIDAD<br>(m)</td>
      <td colspan="2" style="border:1px solid #BFBFBF;padding:5px 4px">COORDENADAS 15Q</td>
      <td colspan="7" style="border:1px solid #BFBFBF;padding:5px 4px">PARÁMETROS A ANALIZAR (mg/Kg)</td>
    </tr>
    """)

    # Encabezados de columna — fila 2 (detalle)
    rows.append(f"""
    <tr style="background:{_COLOR_HEADER};color:white;font-size:10px;font-weight:bold;text-align:center">
      <td style="border:1px solid #BFBFBF;padding:4px 3px">X<br><span style='font-weight:normal;font-size:9px'>(Metros Este)</span></td>
      <td style="border:1px solid #BFBFBF;padding:4px 3px">Y<br><span style='font-weight:normal;font-size:9px'>(Metros Norte)</span></td>
      <td style="border:1px solid #BFBFBF;padding:4px 3px">HFL</td>
      <td style="border:1px solid #BFBFBF;padding:4px 3px">BENCENO</td>
      <td style="border:1px solid #BFBFBF;padding:4px 3px">TOLUENO</td>
      <td style="border:1px solid #BFBFBF;padding:4px 3px">ETILBENCENO</td>
      <td style="border:1px solid #BFBFBF;padding:4px 3px">XILENOS</td>
      <td style="border:1px solid #BFBFBF;padding:4px 3px">pH</td>
      <td style="border:1px solid #BFBFBF;padding:4px 3px">Humedad<br>(%)</td>
    </tr>
    """)

    # Filas de datos por zona
    for zona, muestras in grupos:
        n = len(muestras)
        for i, h in enumerate(muestras):
            res   = h.get("resultados", {})
            muest = str(h.get("id_muestra", "")).strip()
            prof  = str(h.get("profundidad", "")).strip()
            cx    = str(h.get("x", "")).strip()
            cy    = str(h.get("y", "")).strip()

            # Formatear coordenadas con comas de miles si son numéricas
            try:
                cx_fmt = f"{float(cx.replace(',','')):.2f}"
                cy_fmt = f"{float(cy.replace(',','')):.2f}"
            except ValueError:
                cx_fmt, cy_fmt = cx, cy

            # Evaluar cada parámetro
            param_celdas = []
            for p in _PARAMS:
                v_raw  = res.get(p, 0.0)
                v_float = float(v_raw) if v_raw is not None else 0.0
                lmp    = lim_vigentes.get(p, 999999.0)

                if v_float <= 0.0:
                    param_celdas.append(_celda("&lt; L.C.", supera=False))
                else:
                    supera = v_float > lmp
                    if v_float >= 100:
                        txt = f"{v_float:,.2f}"
                    elif v_float >= 10:
                        txt = f"{v_float:.2f}"
                    else:
                        txt = f"{v_float:.3f}"
                    param_celdas.append(_celda(txt, supera=supera))

            # pH y Humedad (sin comparación con LMP)
            ph_v = res.get("pH", 0.0)
            hm_v = res.get("Humedad", 0.0)
            try:
                ph_txt = f"{float(ph_v):.2f}" if float(ph_v) > 0 else "—"
                hm_txt = f"{float(hm_v):.3f}" if float(hm_v) > 0 else "—"
            except (TypeError, ValueError):
                ph_txt, hm_txt = "—", "—"

            # Construir fila
            row = "<tr>"

            # Celda de zona fusionada — solo en la primera fila del grupo
            if i == 0:
                row += (
                    f'<td rowspan="{n}" style="'
                    f"background:{_COLOR_ZONA_BG};font-weight:bold;"
                    "font-size:11px;text-align:center;vertical-align:middle;"
                    f'border:1px solid #BFBFBF;padding:4px 6px">{zona}</td>'
                )

            row += _celda(muest)
            row += _celda(prof)
            row += _celda(cx_fmt)
            row += _celda(cy_fmt)
            row += "".join(param_celdas)
            row += _celda(ph_txt)
            row += _celda(hm_txt)
            row += "</tr>"
            rows.append(row)

    # Fila de LMP
    lmp_celdas = "".join(
        _celda(str(lim_vigentes.get(p, "—")), es_lmp=True)
        for p in _PARAMS
    )
    rows.append(f"""
    <tr>
      <td colspan="3" style="
        background:{_COLOR_LMP_BG};font-size:10px;font-weight:bold;
        text-align:center;padding:5px 6px;border:1px solid #BFBFBF;
        vertical-align:middle;
      ">
        LÍMITE MÁXIMO PERMISIBLE DE ACUERDO A LA NOM-138-SEMARNAT/SSA1-2012<br>
        <span style='font-weight:normal'>PARA USO DE SUELO {uso_suelo.upper()}</span>
      </td>
      <td colspan="2" style="background:{_COLOR_LMP_BG};border:1px solid #BFBFBF"></td>
      {lmp_celdas}
      <td style="background:{_COLOR_LMP_BG};border:1px solid #BFBFBF"></td>
      <td style="background:{_COLOR_LMP_BG};border:1px solid #BFBFBF"></td>
    </tr>
    """)

    # Fila de leyenda cyan
    rows.append(f"""
    <tr>
      <td colspan="12" style="
        background:{_COLOR_EXCEDE};color:{_COLOR_EXCEDE_T};
        font-size:10px;font-weight:bold;text-align:center;
        padding:4px 6px;border:1px solid #BFBFBF;
      ">{_LEYENDA_LMP}</td>
    </tr>
    """)

    # Nota al pie
    rows.append(f"""
    <tr>
      <td colspan="12" style="
        background:{_COLOR_NOTA_BG};font-size:9px;
        text-align:left;padding:4px 8px;border:1px solid #BFBFBF;
      ">{_NOTA_PIE}</td>
    </tr>
    """)

    tabla_html = f"""
    <div style="overflow-x:auto;font-family:Arial,Helvetica,sans-serif">
      <table style="
        border-collapse:collapse;
        width:100%;
        min-width:900px;
        table-layout:auto;
      ">
        {''.join(rows)}
      </table>
    </div>
    """
    return tabla_html


def render_tabla_nom138(
    historial: list[dict],
    lim_vigentes: dict[str, float],
    titulo_proyecto: str,
    uso_suelo: str,
    nombre_siniestro: str = "",
    altura_px: int = 600,
) -> None:
    """
    Renderiza la tabla NOM-138 completa dentro de Streamlit.
    Usa st.components.v1.html() para preservar el HTML exacto con estilos inline.

    Args:
        historial:        Datos de cargar_laboratorio_proyecto()
        lim_vigentes:     LMP del proyecto según uso de suelo
        titulo_proyecto:  ID del proyecto activo
        uso_suelo:        Uso de suelo seleccionado
        nombre_siniestro: Nombre completo del siniestro para el encabezado
        altura_px:        Altura del iframe en píxeles
    """
    html = generar_tabla_nom138_html(
        historial=historial,
        lim_vigentes=lim_vigentes,
        titulo_proyecto=titulo_proyecto,
        uso_suelo=uso_suelo,
        nombre_siniestro=nombre_siniestro,
    )
    components.html(html, height=altura_px, scrolling=True)


