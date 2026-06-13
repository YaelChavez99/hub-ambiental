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
    "puntaje_total":              0,
    "consistencia_datos":         0,
    "completitud_regulatoria":    0,
    "solidez_tecnica":            0,
    "formato_presentacion":       0,
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
    raw = re.sub(r"
http://googleusercontent.com/immersive_entry_chip/0
*(Si lo prefieres, también puedes usar `claude-4-5-sonnet` si tu cuenta de Anthropic ya tiene acceso habilitado a la nueva generación).*

### 3. Reinicia tu app

Finalmente, ve a la consola de Streamlit Community Cloud (donde dice Manage App) y presiona el botón de **Reboot**. Con esto borrarás la memoria caché que se quedó pegada con el JSON cortado, el sistema cargará tus nuevos límites de 8K tokens y tu auditoría técnica funcionará de principio a fin sin atorarse.
