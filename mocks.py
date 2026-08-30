"""
mocks.py — Modo de Prueba (Mock / Dummy)
==========================================
Reemplazos de CERO costo de todas las funciones que llaman a la API de Anthropic.

CÓMO ACTIVAR EL MODO DE PRUEBA:
  En app.py, cambia las líneas de importación:

  MODO PRODUCCIÓN (gasta tokens):
    from app import analizar_fotografia, _llamar_claude_lab_texto, ...

  MODO PRUEBA (costo $0.00):
    from mocks import (
        analizar_fotografia,
        _llamar_claude_lab_texto,
        _llamar_claude_lab_vision,
        analizar_reporte_laboratorio,
        auditar_informe,
    )

  O más simple: al final de app.py, agrega:
    if os.getenv("MODO_PRUEBA") == "1":
        from mocks import *

  Luego corre con:
    MODO_PRUEBA=1 streamlit run app.py

Funciones mockeadas (5):
  1. analizar_fotografia          → H1 Fotografías
  2. _llamar_claude_lab_texto     → H3 Lab (modo texto interno)
  3. _llamar_claude_lab_vision    → H3 Lab (modo vision interno)
  4. analizar_reporte_laboratorio → H3 Lab (función principal)
  5. auditar_informe              → H2 Auditor Técnico
"""

from __future__ import annotations

import random
import time
from typing import Any

import anthropic
import streamlit as st

# ---------------------------------------------------------------------------
# Constantes de mock
# ---------------------------------------------------------------------------
_AVISO = "🟢 **MODO DE PRUEBA ACTIVO** — Generando datos simulados (Costo: $0.00)"
_SLEEP = 2   # segundos de latencia simulada


# ---------------------------------------------------------------------------
# MOCK 1 — analizar_fotografia
# Firma original: (client, image_bytes, media_type) -> dict
# Usada en: render_herramienta_fotos() → H1
# ---------------------------------------------------------------------------
def analizar_fotografia(
    client: anthropic.Anthropic,
    image_bytes: bytes,
    media_type: str,
) -> dict:
    """
    MOCK: Clasifica una fotografía sin llamar a la API.
    Rota entre las 5 categorías reales del sistema para que
    puedas ver cómo se llenan todas las carpetas.
    """
    st.info(_AVISO)
    time.sleep(_SLEEP)

    # Rota entre categorías para poblar todas las carpetas
    categorias = [
        ("Evidencia del Siniestro",    "Derrame de gasolina sobre suelo natural en zona afectada A."),
        ("Excavaciones",               "Excavación mecánica en Punto P3 a profundidad 1.80 m."),
        ("Sondeos y Muestreo",         "Toma de muestra en pozo PM-05 durante muestreo inicial."),
        ("Evidencias de Remediación",  "Retiro de suelo contaminado con retroexcavadora. Zona 2."),
        ("INSERVIBLE",                 None),
    ]

    # Usar tamaño de los bytes como semilla para rotación determinista
    idx = (len(image_bytes) // 1000) % len(categorias)
    clasificacion, pie = categorias[idx]

    return {
        "clasificacion": clasificacion,
        "pie_de_foto":   pie if pie else "Fotografía sin contexto técnico útil.",
    }


# ---------------------------------------------------------------------------
# MOCK 2 — _llamar_claude_lab_texto  (función interna)
# Firma original: (client, texto) -> list[dict]
# Usada en: analizar_reporte_laboratorio()
# ---------------------------------------------------------------------------
def _llamar_claude_lab_texto(
    client: anthropic.Anthropic,
    texto: str,
) -> list[dict]:
    """
    MOCK: Devuelve muestras simuladas sin procesar el texto.
    Retorna un subconjunto de las 38 muestras reales del PDF NOVALABSA.
    """
    st.info(_AVISO)
    time.sleep(_SLEEP)
    return _muestras_novalabsa_completas()


# ---------------------------------------------------------------------------
# MOCK 3 — _llamar_claude_lab_vision  (función interna)
# Firma original: (client, imagenes_b64) -> list[dict]
# Usada en: analizar_reporte_laboratorio()
# ---------------------------------------------------------------------------
def _llamar_claude_lab_vision(
    client: anthropic.Anthropic,
    imagenes_b64: list[str],
) -> list[dict]:
    """
    MOCK: Devuelve muestras simuladas sin procesar imágenes.
    Simula el resultado del modo Vision para PDFs escaneados.
    """
    st.info(_AVISO)
    time.sleep(_SLEEP)
    return _muestras_novalabsa_completas()


# ---------------------------------------------------------------------------
# MOCK 4 — analizar_reporte_laboratorio  (función principal H3)
# Firma original: (client, pdf_bytes) -> list[dict]
# Usada en: render_herramienta_lab()
# ---------------------------------------------------------------------------
def analizar_reporte_laboratorio(
    client: anthropic.Anthropic,
    pdf_bytes: bytes,
) -> list[dict]:
    """
    MOCK: Devuelve las 38 muestras reales del PDF NOVALABSA OT-126040089.
    Incluye todas las zonas, duplicados y la muestra de periferia.
    Simula ambos modos (texto y visión) con un solo bloque de datos.
    """
    st.info(_AVISO)
    st.caption(f"📊 Caracteres útiles en el PDF: 48,293 (simulado)")
    st.info("📄 Modo texto: capa de texto con datos analíticos detectada. (simulado)")
    time.sleep(_SLEEP)

    muestras = _muestras_novalabsa_completas()
    st.caption(f"   ↳ {len(muestras)} muestra(s) nueva(s). (simulado)")
    return muestras


# ---------------------------------------------------------------------------
# MOCK 5 — auditar_informe  (función principal H2)
# Firma original: (client, texto, model_id) -> dict
# Usada en: render_herramienta_auditor() en auditor_tecnico.py
# ---------------------------------------------------------------------------
def auditar_informe(
    client: anthropic.Anthropic,
    texto: str,
    model_id: str,
) -> dict:
    """
    MOCK: Devuelve un reporte de auditoría completo simulado, en el mismo
    esquema que produce el orquestador real (auditor_tecnico.auditar_informe,
    Fase 3): entidades, hallazgos con categoría/criticidad/página/estado de
    verificación, e ICTI. El ICTI y el resumen ejecutivo se calculan con las
    MISMAS funciones deterministas que usa el sistema real (auditor_icti.py),
    no con valores fijos — así el mock nunca se desincroniza del cálculo real.
    """
    st.info(_AVISO)
    time.sleep(_SLEEP)

    import auditor_icti as _icti
    import auditor_tecnico as _at
    import nom138_referencias as _nom

    entidades = {
        "numero_informe":           "OT-126040089",
        "fecha_siniestro":          "21 de abril de 2026",
        "fecha_muestreo":           "21 de abril de 2026",
        "municipio":                "Villa de Arriaga",
        "estado":                   "San Luis Potosí",
        "km_autopista":             "Km 75+550",
        "nombre_autopista":         "Autopista Lagos de Moreno – San Luis Potosí",
        "volumen_derramado_litros": "32,077 litros",
        "contaminante":             "Gasolina (hidrocarburo fracción ligera)",
        "coordenadas_siniestro":    "X: 250,037 / Y: 2,420,516 (UTM 15Q)",
        "area_afectada_m2":         "310 m²",
        "volumen_suelo_m3":         "46.5 m³",
        "numero_pozos_muestreo":    "17 pozos (38 muestras + 4 duplicados)",
        "empresa_vehiculo":         "Transportes del Norte S.A. de C.V.",
        "responsable_tecnico":      "Biol. Juan Carlos Vargas Mellado",
        "uso_de_suelo":             "Agrícola, forestal, pecuario y de conservación",
        "tipo_muestreo":            "Inicial / Comprobatorio",
    }

    hallazgos = [
        # ── Consistencia de datos (antes "discrepancias") ────────────────────
        {
            "categoria": "consistencia_datos", "criticidad": "ALTO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": (
                "El dato 'volumen_derramado_litros' aparece con valores distintos: "
                "'32,077 litros' (pág. 3, Antecedentes) vs. '32,000 litros' (pág. 47, Conclusiones)."
            ),
            "pagina": 47, "pagina_referencia": 3,
            "cita_textual": "32,000 litros", "cita_referencia": "32,077 litros",
            "cita_normativa": None, "autoridad": None,
        },
        {
            "categoria": "consistencia_datos", "criticidad": "ALTO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": (
                "El dato 'area_afectada_m2' aparece con valores distintos: "
                "'310 m²' (pág. 12, Caracterización) vs. '350 m²' (pág. 55, Plan de Saneamiento)."
            ),
            "pagina": 55, "pagina_referencia": 12,
            "cita_textual": "350 m²", "cita_referencia": "310 m²",
            "cita_normativa": None, "autoridad": None,
        },
        {
            "categoria": "consistencia_datos", "criticidad": "ALTO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": (
                "El dato 'numero_pozos_muestreo' aparece con valores distintos: "
                "'17 pozos' (pág. 8, Plan de Muestreo) vs. '15 pozos' (pág. 2, Resumen Ejecutivo)."
            ),
            "pagina": 2, "pagina_referencia": 8,
            "cita_textual": "15 pozos", "cita_referencia": "17 pozos",
            "cita_normativa": None, "autoridad": None,
        },

        # ── Completitud regulatoria (antes "vacíos regulatorios") ────────────
        {
            "categoria": "completitud_regulatoria", "criticidad": "ALTO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": (
                "No se especifica el método de preservación de muestras durante "
                "el transporte (temperatura, tipo de contenedor)."
            ),
            "pagina": 34, "cita_textual": "",
            "cita_normativa": _nom.resolver_cita("metodologia_muestreo"), "autoridad": "ASEA",
        },
        {
            "categoria": "completitud_regulatoria", "criticidad": "CRITICO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": (
                "Falta evaluación de riesgo a receptores humanos y ecosistémicos "
                "desarrollada conforme a la NOM-138 — solo se menciona, no se desarrolla."
            ),
            "pagina": 88, "cita_textual": "",
            "cita_normativa": _nom.resolver_cita("evaluacion_riesgos"), "autoridad": "SEMARNAT",
        },
        {
            "categoria": "completitud_regulatoria", "criticidad": "MEDIO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "No se incluye el certificado de acreditación vigente del laboratorio ante la EMA.",
            "pagina": None, "cita_textual": "",
            "cita_normativa": _nom.CITA_GENERICA, "autoridad": "PROFEPA",
        },
        {
            "categoria": "completitud_regulatoria", "criticidad": "MEDIO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "Las coordenadas del polígono no están referenciadas a un datum geodésico explícito.",
            "pagina": 12, "cita_textual": "",
            "cita_normativa": _nom.CITA_GENERICA, "autoridad": "SEMARNAT",
        },
        {
            "categoria": "completitud_regulatoria", "criticidad": "BAJO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "Las conclusiones no incluyen tabla de cumplimiento NOM-138 por parámetro.",
            "pagina": 95, "cita_textual": "",
            "cita_normativa": _nom.resolver_cita("limites_maximos_permisibles"), "autoridad": "ASEA",
        },

        # ── Solidez técnica (antes "debilidades técnicas") ───────────────────
        {
            "categoria": "solidez_tecnica", "criticidad": "MEDIO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "La descripción de litología es genérica, sin detalle por pozo.",
            "pagina": 40, "cita_textual": "", "cita_normativa": None, "autoridad": None,
        },
        {
            "categoria": "solidez_tecnica", "criticidad": "ALTO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "La interpretación de resultados no identifica qué muestras/parámetros superaron el LMP.",
            "pagina": 42, "cita_textual": "", "cita_normativa": None, "autoridad": None,
        },
        {
            "categoria": "solidez_tecnica", "criticidad": "CRITICO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "La evaluación de riesgos no desarrolla los 4 pasos del proceso (identificación, exposición, toxicológica, riesgo).",
            "pagina": 88, "cita_textual": "", "cita_normativa": None, "autoridad": None,
        },
        {
            "categoria": "solidez_tecnica", "criticidad": "MEDIO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "Las recomendaciones no especifican tecnología, volumen ni cronograma de remediación.",
            "pagina": 97, "cita_textual": "", "cita_normativa": None, "autoridad": None,
        },

        # ── Coherencia hídrica interna (simulada; en el sistema real se
        #    deriva de la tabla de hechos en Python, no de un mock) ──────────
        {
            "categoria": "solidez_tecnica", "criticidad": "ALTO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "El informe no reporta la profundidad del nivel freático.",
            "pagina": None, "cita_textual": "", "cita_normativa": _nom.CITA_GENERICA, "autoridad": None,
        },
        {
            "categoria": "solidez_tecnica", "criticidad": "MEDIO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "El informe trata temas hídricos pero no nombra el acuífero específico.",
            "pagina": 68, "cita_textual": "", "cita_normativa": _nom.CITA_GENERICA, "autoridad": None,
        },
    ]

    # Módulos externos (Fase 4/5) — simulados como no verificables, igual
    # que en producción hasta que INEGI/CONAGUA se conecten al auditor.
    cobertura_externa = {
        "geografica_no_verificable": True,
        "hidrica_no_verificable":    True,
    }

    resultado_icti = _icti.calcular_icti(hallazgos, cobertura_externa)
    resumen_ejecutivo = _at._construir_resumen_ejecutivo(
        resultado_icti, hallazgos, cobertura_externa, entidades
    )

    return {
        "entidades":         entidades,
        "hallazgos":         hallazgos,
        "icti":              resultado_icti,
        "resumen_ejecutivo": resumen_ejecutivo,
        "cobertura_externa": cobertura_externa,
        "total_paginas":     98,
        "total_chunks":      25,
    }


# ---------------------------------------------------------------------------
# DATOS BASE — 38 muestras reales del PDF NOVALABSA OT-126040089
# Reutilizadas por los mocks 2, 3 y 4
# ---------------------------------------------------------------------------
def _muestras_novalabsa_completas() -> list[dict]:
    """
    Devuelve las 38 muestras del reporte NOVALABSA con valores reales
    extraídos manualmente del PDF de referencia.
    Estructura idéntica a la que produce analizar_reporte_laboratorio() real.
    """
    return [
        # ── ZONA 1 ──────────────────────────────────────────────────────────
        {"id_muestra":"P1 0.6",  "zona":"ZONA 1","profundidad":"0.60",
         "coordenada_x":"250037.32","coordenada_y":"2420516.68",
         "HFL":1876.25,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.87,"Humedad":16.797},
        {"id_muestra":"P1 0.75", "zona":"ZONA 1","profundidad":"0.75",
         "coordenada_x":"250037.32","coordenada_y":"2420516.68",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.35,"Humedad":22.95},
        {"id_muestra":"P2 0.6",  "zona":"ZONA 1","profundidad":"0.60",
         "coordenada_x":"250042.44","coordenada_y":"2420525.42",
         "HFL":742.61,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.07,"Humedad":22.259},
        {"id_muestra":"P2 0.75", "zona":"ZONA 1","profundidad":"0.75",
         "coordenada_x":"250042.44","coordenada_y":"2420525.42",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.57,"Humedad":20.948},
        # ── ZONA 2 ──────────────────────────────────────────────────────────
        {"id_muestra":"P3 0.6",  "zona":"ZONA 2","profundidad":"0.60",
         "coordenada_x":"250034.00","coordenada_y":"2420515.73",
         "HFL":5237.48,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.09,"Humedad":21.48},
        {"id_muestra":"P3 1.2",  "zona":"ZONA 2","profundidad":"1.20",
         "coordenada_x":"250034.00","coordenada_y":"2420515.73",
         "HFL":3468.76,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.69,"Humedad":20.957},
        {"id_muestra":"P3 1.8",  "zona":"ZONA 2","profundidad":"1.80",
         "coordenada_x":"250034.00","coordenada_y":"2420515.73",
         "HFL":1100.25,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.12,"Humedad":19.191},
        {"id_muestra":"P3 2.0",  "zona":"ZONA 2","profundidad":"2.00",
         "coordenada_x":"250034.00","coordenada_y":"2420515.73",
         "HFL":50.36,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":8.00,"Humedad":17.762},
        {"id_muestra":"P4 0.6",  "zona":"ZONA 2","profundidad":"0.60",
         "coordenada_x":"250041.59","coordenada_y":"2420529.20",
         "HFL":1254.31,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.98,"Humedad":21.636},
        {"id_muestra":"P4 1.2",  "zona":"ZONA 2","profundidad":"1.20",
         "coordenada_x":"250041.59","coordenada_y":"2420529.20",
         "HFL":985.64,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.05,"Humedad":16.465},
        {"id_muestra":"P4 1.2 Dup","zona":"ZONA 2","profundidad":"1.20",
         "coordenada_x":"250041.59","coordenada_y":"2420529.20",
         "HFL":980.46,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.08,"Humedad":15.951},
        {"id_muestra":"P4 1.8",  "zona":"ZONA 2","profundidad":"1.80",
         "coordenada_x":"250041.59","coordenada_y":"2420529.20",
         "HFL":428.78,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.84,"Humedad":15.355},
        {"id_muestra":"P4 2.0",  "zona":"ZONA 2","profundidad":"2.00",
         "coordenada_x":"250041.59","coordenada_y":"2420529.20",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.00,"Humedad":15.355},
        {"id_muestra":"P5 0.6",  "zona":"ZONA 2","profundidad":"0.60",
         "coordenada_x":"250032.94","coordenada_y":"2420524.98",
         "HFL":1954.00,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.42,"Humedad":15.722},
        {"id_muestra":"P5 1.2",  "zona":"ZONA 2","profundidad":"1.20",
         "coordenada_x":"250032.94","coordenada_y":"2420524.98",
         "HFL":960.13,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.52,"Humedad":17.652},
        {"id_muestra":"P5 1.8",  "zona":"ZONA 2","profundidad":"1.80",
         "coordenada_x":"250032.94","coordenada_y":"2420524.98",
         "HFL":942.58,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.13,"Humedad":16.852},
        {"id_muestra":"P5 2.0",  "zona":"ZONA 2","profundidad":"2.00",
         "coordenada_x":"250032.94","coordenada_y":"2420524.98",
         "HFL":80.16,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.01,"Humedad":21.536},
        {"id_muestra":"P6 0.6",  "zona":"ZONA 2","profundidad":"0.60",
         "coordenada_x":"250024.58","coordenada_y":"2420520.54",
         "HFL":746.32,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.08,"Humedad":18.886},
        {"id_muestra":"P6 1.2",  "zona":"ZONA 2","profundidad":"1.20",
         "coordenada_x":"250024.58","coordenada_y":"2420520.54",
         "HFL":543.69,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.64,"Humedad":22.146},
        {"id_muestra":"P6 1.8",  "zona":"ZONA 2","profundidad":"1.80",
         "coordenada_x":"250024.58","coordenada_y":"2420520.54",
         "HFL":527.16,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.07,"Humedad":15.533},
        {"id_muestra":"P6 1.8 Dup","zona":"ZONA 2","profundidad":"1.80",
         "coordenada_x":"250024.58","coordenada_y":"2420520.54",
         "HFL":518.63,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.06,"Humedad":15.302},
        {"id_muestra":"P6 2.0",  "zona":"ZONA 2","profundidad":"2.00",
         "coordenada_x":"250024.58","coordenada_y":"2420520.54",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.25,"Humedad":19.616},
        {"id_muestra":"P7 0.6",  "zona":"ZONA 2","profundidad":"0.60",
         "coordenada_x":"250032.85","coordenada_y":"2420534.59",
         "HFL":428.65,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.89,"Humedad":18.974},
        {"id_muestra":"P7 1.2",  "zona":"ZONA 2","profundidad":"1.20",
         "coordenada_x":"250032.85","coordenada_y":"2420534.59",
         "HFL":316.82,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.05,"Humedad":17.554},
        {"id_muestra":"P7 1.8",  "zona":"ZONA 2","profundidad":"1.80",
         "coordenada_x":"250032.85","coordenada_y":"2420534.59",
         "HFL":296.13,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.22,"Humedad":16.355},
        {"id_muestra":"P7 2.0",  "zona":"ZONA 2","profundidad":"2.00",
         "coordenada_x":"250032.85","coordenada_y":"2420534.59",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.84,"Humedad":21.931},
        # ── ZONA 3 ──────────────────────────────────────────────────────────
        {"id_muestra":"P8 0.6",  "zona":"ZONA 3","profundidad":"0.60",
         "coordenada_x":"250024.83","coordenada_y":"2420525.35",
         "HFL":308.408,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.16,"Humedad":15.614},
        {"id_muestra":"P8 1.0",  "zona":"ZONA 3","profundidad":"1.00",
         "coordenada_x":"250024.83","coordenada_y":"2420525.35",
         "HFL":229.80,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.45,"Humedad":18.852},
        {"id_muestra":"P8 1.2",  "zona":"ZONA 3","profundidad":"1.20",
         "coordenada_x":"250024.83","coordenada_y":"2420525.35",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.32,"Humedad":18.948},
        {"id_muestra":"P9 0.6",  "zona":"ZONA 3","profundidad":"0.60",
         "coordenada_x":"250029.34","coordenada_y":"2420533.02",
         "HFL":257.22,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.85,"Humedad":18.4},
        {"id_muestra":"P9 0.6 Dup","zona":"ZONA 3","profundidad":"0.60",
         "coordenada_x":"250029.34","coordenada_y":"2420533.02",
         "HFL":258.69,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.72,"Humedad":17.697},
        {"id_muestra":"P9 1.0",  "zona":"ZONA 3","profundidad":"1.00",
         "coordenada_x":"250029.34","coordenada_y":"2420533.02",
         "HFL":210.69,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.77,"Humedad":16.519},
        {"id_muestra":"P9 1.2",  "zona":"ZONA 3","profundidad":"1.20",
         "coordenada_x":"250029.34","coordenada_y":"2420533.02",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.26,"Humedad":22.842},
        # ── PERIFERIA ────────────────────────────────────────────────────────
        {"id_muestra":"P10 0.5", "zona":"PERIFERIA","profundidad":"0.50",
         "coordenada_x":"250045.44","coordenada_y":"2420524.89",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.99,"Humedad":18.545},
        {"id_muestra":"P11 0.5", "zona":"PERIFERIA","profundidad":"0.50",
         "coordenada_x":"250040.14","coordenada_y":"2420515.47",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.70,"Humedad":20.43},
        {"id_muestra":"P12 0.5", "zona":"PERIFERIA","profundidad":"0.50",
         "coordenada_x":"250031.46","coordenada_y":"2420513.57",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.12,"Humedad":21.644},
        {"id_muestra":"P13 0.5", "zona":"PERIFERIA","profundidad":"0.50",
         "coordenada_x":"250023.22","coordenada_y":"2420518.09",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.05,"Humedad":22.861},
        {"id_muestra":"P14 0.5", "zona":"PERIFERIA","profundidad":"0.50",
         "coordenada_x":"250022.74","coordenada_y":"2420526.32",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.96,"Humedad":15.656},
        {"id_muestra":"P15 0.5", "zona":"PERIFERIA","profundidad":"0.50",
         "coordenada_x":"250027.58","coordenada_y":"2420535.01",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.49,"Humedad":16.664},
        {"id_muestra":"P16 0.5", "zona":"PERIFERIA","profundidad":"0.50",
         "coordenada_x":"250034.61","coordenada_y":"2420536.57",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.05,"Humedad":18.888},
        {"id_muestra":"P16 0.5 Dup","zona":"PERIFERIA","profundidad":"0.50",
         "coordenada_x":"250034.61","coordenada_y":"2420536.57",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.11,"Humedad":17.641},
        {"id_muestra":"P17 0.5", "zona":"PERIFERIA","profundidad":"0.50",
         "coordenada_x":"250042.42","coordenada_y":"2420531.98",
         "HFL":0.0,"Benceno":0.0,"Tolueno":0.0,"Etilbenceno":0.0,"Xilenos":0.0,"pH":7.33,"Humedad":19.85},
    ]


# ---------------------------------------------------------------------------
# MOCK 6 — analizar_dispersion_claude  (función interna H4)
# Firma original: (client, model_id, stats, contexto) -> dict
# ---------------------------------------------------------------------------
def analizar_dispersion_claude(
    client:   anthropic.Anthropic,
    model_id: str,
    stats:    dict,
    contexto: dict,
) -> dict:
    """MOCK: Devuelve análisis de dispersión simulado basado en datos reales NOVALABSA."""
    st.info(_AVISO)
    time.sleep(_SLEEP)
    return {
        "resumen_pluma": {
            "descripcion_general": (
                "La pluma de hidrocarburos fracción ligera (HFL) se distribuye "
                "principalmente en las Zonas 1 y 2, con concentraciones que alcanzan "
                "hasta 5,237.48 mg/kg en la muestra P3 0.6, superando en 26 veces el LMP. "
                "La distribución indica un patrón mixto con migración preferencial vertical "
                "en los primeros 1.8 m y lateral hacia el suroeste del sitio. "
                "La Zona 3 y la Periferia muestran concentraciones de contención y "
                "dilución progresiva, confirmando que el siniestro fue atendido oportunamente."
            ),
            "zona_epicentro":            "ZONA 2 — P3 0.6 con HFL = 5,237.48 mg/kg",
            "extension_estimada":        "Aproximadamente 22 m (E-O) × 22 m (N-S)",
            "profundidad_maxima_afectada":"2.00 m (detección límite cuantificable)",
            "patron_distribucion":       "MIXTO",
            "tendencia_migracion": (
                "La tendencia de migración es hacia el sureste, condicionada por la "
                "pendiente topográfica natural del Derecho de Vía y la textura "
                "arenoso-limosa del suelo que facilita la infiltración vertical "
                "antes que la dispersión lateral."
            ),
        },
        "factores_dispersion": [
            {
                "factor":          "Permeabilidad del suelo",
                "condicion_sitio": "Suelo arenoso-limoso con alta permeabilidad (pH 7.0-8.0)",
                "efecto_sobre_pluma": "Acelera la infiltración vertical del HC hacia capas más profundas",
                "nivel_riesgo":    "ALTO",
            },
            {
                "factor":          "Precipitación pluvial",
                "condicion_sitio": "Clima BS1kw con lluvias concentradas en jun-sep (400 mm/año)",
                "efecto_sobre_pluma": "Moviliza el HC disuelto lateralmente durante eventos de lluvia",
                "nivel_riesgo":    "MEDIO",
            },
            {
                "factor":          "Pendiente topográfica",
                "condicion_sitio": "Terreno con pendiente suave (<5%) hacia el sureste del DDV",
                "efecto_sobre_pluma": "Dirige el escurrimiento superficial y subsuperficial al sureste",
                "nivel_riesgo":    "MEDIO",
            },
        ],
        "rutas_migracion": [
            {
                "ruta":                "Infiltración vertical al subsuelo",
                "descripcion":         "Migración descendente por gravedad a través del perfil arenoso-limoso",
                "receptor_potencial":  "Acuífero Zona Metropolitana SLP (CONAGUA clave 2401)",
                "probabilidad":        "MEDIA",
                "distancia_estimada_m": None,
            },
            {
                "ruta":                "Escurrimiento superficial hacia zona sur",
                "descripcion":         "Transporte superficial hacia el sur del DDV por pendiente topográfica",
                "receptor_potencial":  "Área de pastizal y matorral xerófilo colindante",
                "probabilidad":        "BAJA",
                "distancia_estimada_m": 30,
            },
        ],
        "escenarios": [
            {
                "nombre":                   "Escenario sin intervención",
                "descripcion":              "Sin remediación activa, el HC residual continúa migrando por gravedad",
                "condicion_desencadenante": "Lluvia intensa > 30 mm en 24 h durante temporada húmeda",
                "impacto_potencial":        "Afectación de pastizal colindante y potencial contacto con suelos agrícolas en radio de 50 m",
            },
            {
                "nombre":                   "Escenario con remediación oportuna",
                "descripcion":              "Excavación de suelo contaminado y confinamiento controlado",
                "condicion_desencadenante": "Inicio de trabajos dentro de 30 días del siniestro",
                "impacto_potencial":        "Contención total de la pluma dentro del polígono actual; sin afectación adicional",
            },
        ],
        "conclusiones_dispersion": (
            "La pluma de hidrocarburos fracción ligera se encuentra actualmente "
            "contenida dentro del polígono muestreado de aproximadamente 310 m², "
            "con afectación principal en las Zonas 1 y 2 hasta una profundidad máxima "
            "de 2.00 m. Las concentraciones en Zona 3 y Periferia son cercanas al LMP "
            "o inferiores, indicando dilución natural hacia los bordes del sitio. "
            "El riesgo de migración al acuífero es MEDIO en el corto plazo, "
            "condicionado por la textura permeable del suelo y los eventos de lluvia. "
            "Se recomienda iniciar la remediación dentro de 30 días para evitar la "
            "dispersión lateral del contaminante fuera del polígono caracterizado."
        ),
        "recomendaciones_urgentes": [
            "Iniciar excavación de suelo contaminado en Zona 2 (P3, P5) dentro de 30 días — "
            "concentraciones > 1,000 mg/kg representan riesgo de migración activa.",
            "Instalar barreras físicas perimetrales en el límite sur del DDV para "
            "interceptar posible escurrimiento superficial durante temporada de lluvias.",
            "Realizar muestreo de confirmación a 2.50 m de profundidad en P3 y P5 "
            "para verificar que no hay HC detectable por debajo del perfil caracterizado.",
        ],
    }


# ---------------------------------------------------------------------------
# MOCK 7 — generar_capitulo5_claude  (función interna H4)
# Firma original: (client, model_id, stats, contexto, analisis) -> str
# ---------------------------------------------------------------------------
def generar_capitulo5_claude(
    client:   anthropic.Anthropic,
    model_id: str,
    stats:    dict,
    contexto: dict,
    analisis: dict,
    datos_inegi:   dict | None = None,
    datos_conagua: dict | None = None,
) -> str:
    """MOCK: Devuelve el Capítulo 5 simulado con datos reales + secciones INEGI/CONAGUA."""
    st.info(_AVISO)
    time.sleep(_SLEEP)

    municipio = contexto.get("municipio", "Villa de Arriaga")
    estado    = contexto.get("estado",    "San Luis Potosí")
    km        = contexto.get("km_autopista", "Km 75+550")
    autopista = contexto.get("nombre_autopista", "Autopista Lagos de Moreno – SLP")
    hfl_max   = stats.get("hfl_maximo", 5237.48)
    n_rebase  = stats.get("muestras_rebase", 22)
    n_total   = stats.get("total_muestras", 42)

    return f"""## 5. CARACTERÍSTICAS GENERALES DEL SITIO CONTAMINADO

### 5.1 Localización del Sitio

El sitio de estudio se ubica en el {km} de la {autopista}, en el municipio de {municipio}, {estado}. Las coordenadas UTM (Zona 14Q, Datum WGS84) del punto central del siniestro corresponden a X: 250,037 m Este y Y: 2,420,516 m Norte, situándose dentro del Derecho de Vía (DDV) federal de la autopista concesionada. El polígono afectado tiene una extensión aproximada de 310 m², comprendido en un área de 22 m en dirección Este-Oeste y 22 m en dirección Norte-Sur, según los resultados del muestreo inicial comprobatorio realizado el 21 de abril de 2026.

El acceso al sitio se realiza por la propia autopista desde la caseta de cobro de {municipio}, a través de un camino de servicio del Derecho de Vía. El sitio colinda al norte y sur con el DDV de la autopista, al oriente con zona de matorral xerófilo y al poniente con área de pastizal inducido, dentro de la zona semiárida característica del altiplano potosino.

### 5.2 Orografía y Geomorfología

El municipio de {municipio} se localiza en la provincia fisiográfica de la Mesa del Centro, subprovincia Llanuras y Sierras de Querétaro e Hidalgo, de acuerdo con la clasificación del INEGI. El relieve en la zona del sitio es predominantemente plano a ligeramente ondulado, con pendientes que no superan el 5% en dirección sureste, lo que favorece el escurrimiento superficial en esa dirección durante eventos de precipitación. La altitud media en el sitio es de aproximadamente 1,850 metros sobre el nivel del mar.

La geología superficial del área está conformada por depósitos aluviales del Cuaternario, constituidos por arenas, limos y gravas de origen fluvial, sobre una base de rocas sedimentarias del Terciario. Esta composición litológica, caracterizada por su textura arenoso-limosa documentada en los formatos de campo, determina una permeabilidad media-alta que influye directamente en la movilidad vertical del hidrocarburo derramado.

### 5.3 Hidrografía e Hidrología

El sitio se ubica dentro de la Región Hidrológica RH-12 (Lerma-Santiago), en la cuenca del Río Verde y la subcuenca del Río Salado, de acuerdo con la clasificación de la CONAGUA. No existen cuerpos de agua superficiales permanentes en un radio de 500 metros del sitio afectado; el escurrimiento superficial más cercano corresponde a un arroyo intermitente que fluye aproximadamente 280 m al sureste del polígono caracterizado, activo únicamente durante la temporada de lluvias.

En cuanto a la hidrología subterránea, el sitio se sitúa sobre el acuífero Zona Metropolitana de San Luis Potosí (clave CONAGUA 2401), clasificado como acuífero libre a semiconfinado. La profundidad al nivel estático en la zona varía entre 80 y 120 metros según datos históricos de CONAGUA, lo que representa una barrera natural significativa para la migración vertical del hidrocarburo en el corto plazo. Sin embargo, la textura permeable del suelo y la ausencia de horizontes arcillosos en los primeros 2.0 m explorados hace necesaria la remediación oportuna para evitar afectación al acuífero en el mediano plazo.

### 5.4 Clima

De acuerdo con la clasificación climática de Köppen modificada por García (1981) para México, el municipio de {municipio} presenta un clima BS1kw, correspondiente a semiárido templado con lluvias en verano. La temperatura media anual oscila entre 14°C y 18°C, con máximas que pueden superar los 30°C en los meses de abril y mayo, y mínimas por debajo de los 5°C en enero y febrero.

La precipitación media anual en la zona es de aproximadamente 380 a 420 mm, concentrándose el 75% en el período de junio a septiembre. Esta estacionalidad de las lluvias es relevante para la evaluación del riesgo, ya que los eventos de precipitación intensos durante la temporada húmeda pueden movilizar el hidrocarburo residual hacia zonas adyacentes al polígono caracterizado, tanto por escurrimiento superficial como por infiltración acelerada.

### 5.5 Flora y Vegetación

La vegetación natural de la zona corresponde a matorral xerófilo rosetófilo y pastizal inducido, típicos del altiplano potosino semiárido, con especies dominantes como lechuguilla (*Agave lechuguilla*), palma samandoca (*Yucca filifera*), gobernadora (*Larrea tridentata*) y nopal (*Opuntia* spp.). Dentro del Derecho de Vía, la vegetación original ha sido sustituida en su mayor parte por pastizal de temporal de baja densidad, con presencia de manchones de plantas ruderales.

En el área directamente afectada por el derrame de {stats.get('hfl_maximo', 5237):.0f} mg/kg de HFL en la muestra de mayor concentración, se observó durante el reconocimiento de campo la afectación visible a la cobertura vegetal, manifestada en la decoloración y marchitamiento de las plantas herbáceas expuestas al hidrocarburo. No se identificaron especies bajo protección especial de la NOM-059-SEMARNAT-2010 dentro del polígono afectado.

### 5.6 Fauna

La fauna silvestre reportada para el municipio de {municipio} y sus alrededores incluye mamíferos como el coyote (*Canis latrans*), el tlacuache (*Didelphis virginiana*), liebres (*Lepus californicus*) y diversas especies de roedores silvestres. En cuanto a la herpetofauna, se reportan diversas especies de lagartijas del género *Sceloporus* y serpientes como la víbora de cascabel (*Crotalus scutulatus*), listada en la NOM-059-SEMARNAT-2010 como especie Sujeta a Protección Especial.

Durante las actividades de muestreo realizadas el 21 de abril de 2026, no se observó fauna silvestre dentro del polígono afectado, posiblemente debido a la presencia del personal técnico y al olor característico del hidrocarburo. Sin embargo, el riesgo de exposición de fauna terrestre al contaminante es real durante el período nocturno, particularmente para roedores y reptiles que utilizan el DDV como corredor de desplazamiento. Se recomienda colocar señalización preventiva y concluir los trabajos de remediación de forma expedita.

### 5.7 Edafología y Tipo de Suelo

De acuerdo con la carta edafológica del INEGI escala 1:250,000, el tipo de suelo predominante en el área del sitio corresponde a Xerosol háplico (Xh) con fase lítica a profundidad mayor a 50 cm, característico de las zonas áridas y semiáridas del altiplano mexicano. Este tipo edafológico se caracteriza por presentar un horizonte cálcico o gípsico a profundidad variable, con textura dominante arenoso-limosa en los primeros horizontes superficiales.

Los resultados analíticos del muestreo confirman las características fisicoquímicas del suelo: el pH promedio de las {n_total} muestras analizadas fue de {stats.get('ph_promedio', 7.4):.2f} unidades, correspondiente a un suelo ligeramente alcalino, y la humedad gravimétrica promedio fue de {stats.get('humedad_promedio_pct', 18.9):.1f}%, valores consistentes con las condiciones de un xerosol en la temporada de estiaje. La textura arenoso-limosa con baja cohesión favorece la infiltración del hidrocarburo, explicando el patrón de distribución vertical documentado en la caracterización, con HFL detectable hasta los 2.0 m de profundidad.

### 5.8 Uso de Suelo y Vegetación (USV)

De acuerdo con la carta de Uso de Suelo y Vegetación serie VII del INEGI (2021), el área circundante al sitio afectado se clasifica como Matorral xerófilo (MK) con pastizal inducido (PI) en las márgenes del Derecho de Vía. El uso de suelo predominante en el municipio de {municipio} es {contexto.get('uso_suelo', 'agrícola, forestal, pecuario y de conservación')}, lo que determina la aplicación de los Límites Máximos Permisibles (LMP) correspondientes a ese uso según la Tabla 1 de la NOM-138-SEMARNAT/SSA1-2012.

Bajo esta clasificación, el LMP aplicable para Hidrocarburos Fracción Ligera (HFL) es de 200 mg/kg en base seca. Los resultados del muestreo inicial revelaron que {n_rebase} de las {n_total} muestras superan este límite, con un valor máximo de {hfl_max:,.2f} mg/kg, lo que confirma la necesidad de implementar acciones de remediación conforme a los criterios normativos vigentes.

### 5.9 Población

De acuerdo con el Censo de Población y Vivienda 2020 del INEGI, el municipio de {municipio}, {estado}, cuenta con una población total de 19,847 habitantes, distribuidos en la cabecera municipal y diversas localidades rurales. La localidad más cercana al sitio del siniestro es el ejido San Elías, ubicado aproximadamente 1.2 km al sur del {km}, con una población estimada de 180 a 250 personas.

La proximidad de esta localidad rural al sitio afectado implica que la actividad agropecuaria circundante puede verse comprometida si no se realizan las acciones de remediación oportunas, particularmente considerando que los habitantes utilizan el área para pastoreo de ganado bovino y caprino en las márgenes del DDV. No se identificaron pozos de abastecimiento de agua potable para uso humano en el radio de influencia inmediata del sitio (500 m).

### 5.10 Contexto Económico

La {autopista} es una vía de comunicación estratégica para el transporte de bienes y pasajeros entre la región de los Altos de Jalisco y el corredor industrial del altiplano potosino. El municipio de {municipio} tiene una economía basada principalmente en las actividades agropecuarias (ganadería extensiva y agricultura de temporal), el comercio regional y los servicios asociados al tránsito de la autopista.

El siniestro ocurrido en el {km} implicó la afectación temporal de la circulación vehicular y el potencial daño a la productividad agropecuaria de los predios colindantes al DDV. La atención oportuna del derrame y la realización del presente estudio de caracterización contribuyen a minimizar el impacto económico de largo plazo sobre los recursos naturales del municipio y a dar cumplimiento a las obligaciones normativas ante SEMARNAT y ASEA para la restauración del sitio afectado.
"""
