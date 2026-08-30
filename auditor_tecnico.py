"""
Módulo: auditor_tecnico.py
Herramienta 2 — Revisión Técnica Ambiental (Fase 3 — orquestador)

Reescritura completa sobre la arquitectura de Fases 1-2:
  - Pass 1 (auditor_extraccion.py): extrae hechos de TODO el documento sin
    truncar, en paralelo, cada uno con su página exacta.
  - Reduce (Python puro): discrepancias de consistencia y coherencia hídrica
    interna se determinan comparando la tabla de hechos — sin llamar a Claude.
  - Pass 2 (este módulo): un checklist regulatorio/técnico fijo, evaluado por
    Claude solo contra los fragmentos relevantes, con IDs controlados —
    nunca una lista abierta de "encuentra lo que quieras".
  - ICTI (auditor_icti.py): puntaje 100% determinista sobre los hallazgos.

Contrato de integración que este módulo NUNCA rompe (verificado contra
app.py y gestor_proyectos.py antes de escribir esta versión):
  - render_herramienta_auditor(client, model_id, proyecto_actual) — misma firma.
  - auditar_informe(client, texto, model_id) -> dict — sigue existiendo como
    función de módulo, porque app.py la reemplaza en caliente por el mock de
    mocks.py cuando se activa Modo Prueba (_aplicar_modo() en app.py).
  - st.session_state["_auditoria_resultado"]["entidades"] conserva las mismas
    17 claves que antes (dispersor_hc.py y app.py las leen directamente).
  - st.session_state["_auditoria_resultado"]["icti"]["puntaje_total"/"nivel"]
    siguen en el nivel superior del dict (app.py los lee así en el Dashboard).
  - Las llamadas a gestor_proyectos.registrar_evento()/actualizar_proyecto()
    no cambian de firma.

No depende de app.py — se importa como módulo.
Compatible con: Python 3.10+, anthropic>=0.28, streamlit>=1.35, pdfplumber
"""

from __future__ import annotations

import io
import json
import re
from typing import Any

import anthropic
import pdfplumber
import streamlit as st

import auditor_extraccion as _ax
import auditor_icti as _icti_calc
import nom138_referencias as _nom

# ---------------------------------------------------------------------------
# Constantes de visualización
# ---------------------------------------------------------------------------
_ICTI_COLORES = {
    "APROBADO":      ("#1a7a1a", "#d4edda", "🟢"),
    "OBSERVACIONES": ("#856404", "#fff3cd", "🟡"),
    "DEFICIENTE":    ("#7d3c00", "#fde8d0", "🟠"),
    "RECHAZABLE":    ("#7b0000", "#f8d7da", "🔴"),
}

_CRITICIDAD_COLORES = {
    "CRITICO":      ("#7b0000", "#f8d7da", "🔴"),
    "ALTO":         ("#cc0000", "#ffcccc", "🟠"),
    "MEDIO":        ("#7d5a00", "#fff0b3", "🟡"),
    "BAJO":         ("#1a5c1a", "#d6f0d6", "🟢"),
    "INFORMATIVO":  ("#31708f", "#d9edf7", "ℹ️"),
}

_ESTADO_VERIFICACION_ICONO = {
    "CONFIRMADO":          "✅",
    "PROBABLE":            "🟡",
    "REQUIERE_VALIDACION": "🔎",
    "NO_VERIFICABLE":      "⚪",
}

_CATEGORIA_LABEL = {
    "consistencia_datos":      "Consistencia de datos",
    "completitud_regulatoria": "Completitud regulatoria",
    "solidez_tecnica":         "Solidez técnica",
    "validacion_geografica":   "Validación geográfica (INEGI)",
    "validacion_hidrica":      "Validación hídrica (CONAGUA)",
}

# Desempate del Top 5 por impacto regulatorio real, no solo criticidad
# nominal: un vacío regulatorio va antes que una discrepancia de un dato
# secundario aunque ambos sean CRÍTICO/ALTO.
_ORDEN_CRITICIDAD = {"CRITICO": 0, "ALTO": 1, "MEDIO": 2, "BAJO": 3, "INFORMATIVO": 4}
_ORDEN_CATEGORIA_DESEMPATE = {
    "completitud_regulatoria": 0,
    "consistencia_datos":      1,
    "solidez_tecnica":         2,
    "validacion_geografica":   3,
    "validacion_hidrica":      3,
}

_MINUTOS_POR_CRITICIDAD = {"CRITICO": 45, "ALTO": 20, "MEDIO": 10, "BAJO": 5, "INFORMATIVO": 0}


# ---------------------------------------------------------------------------
# Extracción de texto del PDF del informe (sin cambios de Fase 0)
# ---------------------------------------------------------------------------

def _extraer_texto_informe(pdf_bytes: bytes) -> str:
    """
    Extrae el texto completo del PDF del informe, página por página, con
    marcadores '--- PÁGINA N/total ---' que auditor_extraccion.py usa para
    segmentar sin truncar. A diferencia del módulo de laboratorio, NO filtra
    páginas — el informe puede tener datos relevantes en cualquier sección.
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
# Compatibilidad de "entidades" — mismo esquema de 17 claves que la versión
# anterior, para no romper dispersor_hc.py ni el render de app.py.
# ---------------------------------------------------------------------------

_ENTIDADES_LABELS = {
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


def _primer_valor(tabla_hechos: dict[str, list[dict]], entidad: str) -> str | None:
    ocurrencias = tabla_hechos.get(entidad, [])
    return str(ocurrencias[0]["valor"]) if ocurrencias else None


def _construir_entidades_compatibles(tabla_hechos: dict[str, list[dict]]) -> dict:
    """
    Mapea la tabla de hechos de Pass 1 al esquema de 17 claves que
    dispersor_hc.py y el render de entidades ya esperan. Cada valor viene
    de un hecho extraído textualmente — si no se encontró, queda None
    (el render ya lo muestra como "no encontrado").
    """
    coord_x = _primer_valor(tabla_hechos, "coordenadas_x")
    coord_y = _primer_valor(tabla_hechos, "coordenadas_y")
    coordenadas_siniestro = f"X: {coord_x} / Y: {coord_y}" if coord_x and coord_y else None

    return {
        "numero_informe":           _primer_valor(tabla_hechos, "numero_informe"),
        "fecha_siniestro":          _primer_valor(tabla_hechos, "fecha_siniestro"),
        "fecha_muestreo":           _primer_valor(tabla_hechos, "fecha_muestreo"),
        "municipio":                _primer_valor(tabla_hechos, "municipio"),
        "estado":                   _primer_valor(tabla_hechos, "estado"),
        "km_autopista":             _primer_valor(tabla_hechos, "km_autopista"),
        "nombre_autopista":         _primer_valor(tabla_hechos, "nombre_autopista"),
        "volumen_derramado_litros": _primer_valor(tabla_hechos, "volumen_derramado_litros"),
        "contaminante":             _primer_valor(tabla_hechos, "contaminante"),
        "coordenadas_siniestro":    coordenadas_siniestro,
        "area_afectada_m2":         _primer_valor(tabla_hechos, "area_afectada_m2"),
        "volumen_suelo_m3":         _primer_valor(tabla_hechos, "volumen_suelo_m3"),
        "numero_pozos_muestreo":    _primer_valor(tabla_hechos, "numero_pozos_muestreo"),
        "empresa_vehiculo":         _primer_valor(tabla_hechos, "empresa_vehiculo"),
        "responsable_tecnico":      _primer_valor(tabla_hechos, "responsable_tecnico"),
        "uso_de_suelo":             _primer_valor(tabla_hechos, "uso_de_suelo"),
        "tipo_muestreo":            _primer_valor(tabla_hechos, "tipo_muestreo"),
    }


# ---------------------------------------------------------------------------
# Consistencia de datos -> hallazgos (Python puro, ya viene de Fase 1)
# ---------------------------------------------------------------------------

def _discrepancias_a_hallazgos(discrepancias: list[dict]) -> list[dict]:
    """
    Convierte las discrepancias detectadas por auditor_extraccion.py
    (comparación estructurada, sin Claude) al formato común de hallazgo.
    Criticidad fija: toda discrepancia confirmada en un dato de valor único
    esperado (volumen, área, coordenadas...) es ALTO — es exactamente el
    tipo de error "copy-paste" que hace que la autoridad rechace un informe.
    """
    hallazgos = []
    for d in discrepancias:
        hallazgos.append({
            "categoria":            "consistencia_datos",
            "criticidad":           "ALTO",
            "estado_verificacion":  "CONFIRMADO",
            "descripcion": (
                f"El dato '{d['entidad']}' aparece con valores distintos en el documento: "
                f"'{d['valor_referencia']}' (pág. {d['pagina_referencia']}) "
                f"vs. '{d['valor_discrepante']}' (pág. {d['pagina_discrepante']})."
            ),
            "pagina":            d["pagina_discrepante"],
            "pagina_referencia": d["pagina_referencia"],
            "cita_textual":      d["cita_discrepante"],
            "cita_referencia":   d["cita_referencia"],
            "cita_normativa":    None,
            "fuente_externa":    None,
        })
    return hallazgos


# ---------------------------------------------------------------------------
# Coherencia hídrica interna (Python puro, sobre la tabla de hechos)
# ---------------------------------------------------------------------------
# Reglas de criticidad fijadas explícitamente por el cliente — no se
# infieren de un prompt, viven en código:
#   No evalúa riesgo de migración hacia el acuífero -> CRÍTICO (NOM-138 Apartado 8)
#   No reporta profundidad del nivel freático        -> ALTO
#   No menciona distancia a cuerpos de agua sup.      -> ALTO
#   Menciona el acuífero sin nombre específico        -> MEDIO
#
# Estas comprobaciones NO usan Claude: se derivan de la tabla de hechos de
# Pass 1, que ya cubrió el documento completo. Es más confiable que un
# "vistazo" a un fragmento — evita el falso positivo de que un chunk diga
# "no se menciona" cuando en realidad aparece 40 páginas más adelante.

def _hallazgos_coherencia_hidrica(tabla_hechos: dict[str, list[dict]]) -> list[dict]:
    hallazgos: list[dict] = []

    riesgo_evaluado = any(
        str(h.get("valor", "")).strip().upper() == "SI"
        for h in tabla_hechos.get("evaluacion_riesgo_migracion_acuifero", [])
    )
    if not riesgo_evaluado:
        hallazgos.append({
            "categoria":           "solidez_tecnica",
            "criticidad":          "CRITICO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": (
                "El informe no desarrolla una evaluación de riesgo de migración "
                "vertical/lateral del contaminante hacia el acuífero."
            ),
            "pagina": None, "cita_textual": "",
            "cita_normativa": _nom.resolver_cita("evaluacion_riesgos"),
            "fuente_externa": None,
        })

    prof_freatico = tabla_hechos.get("profundidad_nivel_freatico_m", [])
    if not prof_freatico:
        hallazgos.append({
            "categoria":           "solidez_tecnica",
            "criticidad":          "ALTO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "El informe no reporta la profundidad del nivel freático.",
            "pagina": None, "cita_textual": "",
            "cita_normativa": _nom.CITA_GENERICA,
            "fuente_externa": None,
        })

    distancia_agua = tabla_hechos.get("distancia_cuerpo_agua_superficial_m", [])
    if not distancia_agua:
        hallazgos.append({
            "categoria":           "solidez_tecnica",
            "criticidad":          "ALTO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "El informe no menciona distancia a cuerpos de agua superficiales.",
            "pagina": None, "cita_textual": "",
            "cita_normativa": _nom.CITA_GENERICA,
            "fuente_externa": None,
        })

    nombre_acuifero = tabla_hechos.get("nombre_acuifero", [])
    menciona_temas_hidricos = bool(prof_freatico or distancia_agua)
    if not nombre_acuifero and menciona_temas_hidricos:
        hallazgos.append({
            "categoria":           "solidez_tecnica",
            "criticidad":          "MEDIO",
            "estado_verificacion": "CONFIRMADO",
            "descripcion": "El informe trata temas hídricos pero no nombra el acuífero específico.",
            "pagina": (prof_freatico or distancia_agua)[0].get("pagina_inicio"),
            "cita_textual": "", "cita_normativa": _nom.CITA_GENERICA,
            "fuente_externa": None,
        })

    return hallazgos


# ---------------------------------------------------------------------------
# Pass 2 — Checklist regulatorio/técnico (Claude, solo sobre chunks enrutados)
# ---------------------------------------------------------------------------
# Catálogo CONTROLADO de preguntas — no una lista abierta de "encuentra lo
# que quieras". Cada ítem trae su criticidad y su cita NOM-138 fijas: Claude
# solo responde SÍ/NO con evidencia, la criticidad la decide este código.

CHECKLIST_REGULATORIO: list[dict] = [
    {
        "id": "metodologia_preservacion_muestras",
        "pregunta": "¿El informe especifica el método de preservación de las "
                    "muestras durante el transporte (temperatura, tipo de contenedor)?",
        "seccion_hint": ["metodolog", "muestreo", "cadena de custodia"],
        "categoria": "completitud_regulatoria", "criticidad_si_falta": "ALTO",
        "cita_clave": "metodologia_muestreo", "autoridad": "ASEA",
    },
    {
        "id": "evaluacion_riesgo_desarrollada",
        "pregunta": "¿El informe desarrolla (no solo menciona) la evaluación de "
                    "riesgo a receptores humanos y ecosistémicos?",
        "seccion_hint": ["riesgo"],
        "categoria": "completitud_regulatoria", "criticidad_si_falta": "CRITICO",
        "cita_clave": "evaluacion_riesgos", "autoridad": "SEMARNAT",
    },
    {
        "id": "certificado_acreditacion_laboratorio",
        "pregunta": "¿Se menciona el certificado de acreditación EMA del laboratorio?",
        "seccion_hint": ["anexo", "laborator", "acreditaci"],
        "categoria": "completitud_regulatoria", "criticidad_si_falta": "MEDIO",
        "cita_clave": None, "autoridad": "PROFEPA",
    },
    {
        "id": "datum_coordenadas",
        "pregunta": "¿Las coordenadas del sitio especifican el datum geodésico (ej. WGS84)?",
        "seccion_hint": ["coordenada", "caracterizaci", "localiza"],
        "categoria": "completitud_regulatoria", "criticidad_si_falta": "MEDIO",
        "cita_clave": None, "autoridad": "SEMARNAT",
    },
    {
        "id": "tabla_cumplimiento_nom138",
        "pregunta": "¿Las conclusiones incluyen una tabla o referencia explícita de "
                    "cumplimiento/incumplimiento por parámetro contra la NOM-138?",
        "seccion_hint": ["conclusi"],
        "categoria": "completitud_regulatoria", "criticidad_si_falta": "BAJO",
        "cita_clave": "limites_maximos_permisibles", "autoridad": "ASEA",
    },
    {
        "id": "litologia_especifica_por_pozo",
        "pregunta": "¿La descripción de litología/estratigrafía es específica por pozo "
                    "(textura, color, presencia de HC) y no una descripción genérica de una línea?",
        "seccion_hint": ["caracterizaci", "edafolog", "litolog", "geolog"],
        "categoria": "solidez_tecnica", "criticidad_si_falta": "MEDIO",
        "cita_clave": None, "autoridad": None,
    },
    {
        "id": "interpretacion_resultados_especifica",
        "pregunta": "¿La interpretación de resultados identifica específicamente qué "
                    "muestras/parámetros superaron el LMP, en vez de una afirmación genérica?",
        "seccion_hint": ["resultado", "interpretaci"],
        "categoria": "solidez_tecnica", "criticidad_si_falta": "ALTO",
        "cita_clave": None, "autoridad": None,
    },
    {
        "id": "recomendaciones_especificas",
        "pregunta": "¿Las recomendaciones especifican tecnología de remediación propuesta, "
                    "volumen estimado y cronograma tentativo?",
        "seccion_hint": ["recomendaci"],
        "categoria": "solidez_tecnica", "criticidad_si_falta": "MEDIO",
        "cita_clave": None, "autoridad": None,
    },
]

SYSTEM_PROMPT_CHECKLIST = """
Eres un auditor técnico ambiental. Se te darán fragmentos de un informe de
caracterización de sitio contaminado y una lista de preguntas de verificación
con un ID cada una.

Para CADA pregunta, responde ÚNICAMENTE con base en lo que esté
explícitamente en los fragmentos proporcionados. No infieras, no asumas.

Devuelve un arreglo JSON, un objeto por pregunta:
[{"id": "<id_de_la_pregunta>", "cumple": true/false, "pagina": <número o null>, "cita_textual": "<máx 200 caracteres o vacío>"}]

Si los fragmentos no contienen evidencia suficiente para responder con
certeza, responde cumple=false, pagina=null, cita_textual="".
Responde TODAS las preguntas de la lista, en el mismo orden.
Devuelve ÚNICAMENTE el arreglo JSON. Sin markdown. Sin texto adicional.
"""

_MAX_CHARS_CHECKLIST = 40_000   # tope agregado por llamada al checklist


def _enrutar_chunks_por_palabras(chunks: list[dict], palabras: set[str]) -> list[dict]:
    palabras_norm = {p.lower() for p in palabras}
    return [
        c for c in chunks
        if any(p in c["seccion_titulo"].lower() for p in palabras_norm)
    ]


def _limpiar_json_checklist(raw: str) -> list[dict]:
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`")
    raw = re.sub(r',\s*([\]}])', r'\1', raw)
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        raw = match.group(0)
    try:
        datos = json.loads(raw)
        return datos if isinstance(datos, list) else []
    except json.JSONDecodeError:
        return []


def _llamar_checklist(
    client: anthropic.Anthropic, model_id: str, chunks: list[dict], items: list[dict]
) -> dict[str, dict]:
    """Evalúa un lote de chunks contra un subconjunto del checklist. Puede
    llamarse varias veces si el contenido enrutado excede el tope de tamaño;
    los resultados se combinan con OR (si algún lote confirma que cumple,
    se toma como cumplido)."""
    if not chunks or not items:
        return {}

    lotes: list[list[dict]] = []
    lote_actual: list[dict] = []
    chars_actuales = 0
    for c in chunks:
        if chars_actuales + len(c["texto"]) > _MAX_CHARS_CHECKLIST and lote_actual:
            lotes.append(lote_actual)
            lote_actual, chars_actuales = [], 0
        lote_actual.append(c)
        chars_actuales += len(c["texto"])
    if lote_actual:
        lotes.append(lote_actual)

    preguntas_txt = "\n".join(f"{it['id']}: {it['pregunta']}" for it in items)
    resultados: dict[str, dict] = {}

    for lote in lotes:
        payload = "\n\n".join(
            f"--- PÁGINAS {c['pagina_inicio']}-{c['pagina_fin']} ---\n{c['texto']}"
            for c in lote
        )
        try:
            msg = client.messages.create(
                model=model_id,
                max_tokens=2_000,
                system=SYSTEM_PROMPT_CHECKLIST,
                messages=[{
                    "role": "user",
                    "content": f"PREGUNTAS:\n{preguntas_txt}\n\nFRAGMENTOS:\n{payload}",
                }],
            )
            respuestas = _limpiar_json_checklist(msg.content[0].text.strip())
        except Exception:
            respuestas = []

        for r in respuestas:
            rid = r.get("id")
            if not rid:
                continue
            if rid not in resultados or (r.get("cumple") and not resultados[rid].get("cumple")):
                resultados[rid] = r

    return resultados


def evaluar_checklist_regulatorio(
    client: anthropic.Anthropic, model_id: str, chunks: list[dict]
) -> list[dict]:
    """
    Evalúa el catálogo fijo CHECKLIST_REGULATORIO contra el documento.
    Enruta por palabras clave de sección; si ninguna sección coincide con
    ningún ítem (detección de encabezados imperfecta en este documento en
    particular), corre el checklist contra TODOS los chunks en vez de
    asumir ausencia — es más caro pero evita un falso "no cumple" por un
    fallo de enrutamiento, no del contenido real del informe.
    """
    todas_palabras = {p for it in CHECKLIST_REGULATORIO for p in it["seccion_hint"]}
    enrutados = _enrutar_chunks_por_palabras(chunks, todas_palabras)
    chunks_a_usar = enrutados if enrutados else chunks

    respuestas = _llamar_checklist(client, model_id, chunks_a_usar, CHECKLIST_REGULATORIO)

    hallazgos: list[dict] = []
    for item in CHECKLIST_REGULATORIO:
        r = respuestas.get(item["id"])
        cumple = bool(r and r.get("cumple"))
        if cumple:
            continue
        hallazgos.append({
            "categoria":           item["categoria"],
            "criticidad":          item["criticidad_si_falta"],
            "estado_verificacion": "CONFIRMADO" if r else "REQUIERE_VALIDACION",
            "descripcion":         f"{item['pregunta']} — No se encontró evidencia de esto en el informe.",
            "pagina":              (r or {}).get("pagina"),
            "cita_textual":        (r or {}).get("cita_textual", ""),
            "cita_normativa":      _nom.resolver_cita(item.get("cita_clave")),
            "autoridad":           item.get("autoridad"),
            "fuente_externa":      None,
        })
    return hallazgos


# ---------------------------------------------------------------------------
# Resumen ejecutivo — Top 5 con desempate por impacto regulatorio real,
# conteo por criticidad, y estimación de tiempo de corrección.
# ---------------------------------------------------------------------------

def _construir_top5(hallazgos: list[dict]) -> list[dict]:
    accionables = [h for h in hallazgos if h.get("criticidad") != "INFORMATIVO"]
    ordenados = sorted(
        accionables,
        key=lambda h: (
            _ORDEN_CRITICIDAD.get(h.get("criticidad"), 99),
            _ORDEN_CATEGORIA_DESEMPATE.get(h.get("categoria"), 99),
        ),
    )
    return [
        {
            "descripcion": h.get("descripcion"),
            "pagina":      h.get("pagina"),
            "criticidad":  h.get("criticidad"),
            "categoria":   _CATEGORIA_LABEL.get(h.get("categoria"), h.get("categoria")),
        }
        for h in ordenados[:5]
    ]


def _estimar_tiempo_correccion(hallazgos: list[dict]) -> str:
    minutos = sum(_MINUTOS_POR_CRITICIDAD.get(h.get("criticidad"), 0) for h in hallazgos)
    horas, mins = divmod(minutos, 60)
    if horas and mins:
        return f"~{horas} h {mins} min"
    if horas:
        return f"~{horas} h"
    return f"~{mins} min"


def _construir_bloque_cobertura(cobertura_externa: dict, entidades: dict) -> dict:
    """
    Bloque de estado de verificación — visible en el resumen ejecutivo, no
    una nota al pie. Se calcula en Python a partir del estado real de cada
    módulo externo, nunca se redacta por Claude.
    """
    if cobertura_externa.get("geografica_no_verificable"):
        geo = {
            "verificado": False,
            "texto": "Validación geográfica: NO VERIFICABLE — módulo INEGI aún no conectado (Fase 4).",
        }
    else:
        geo = {"verificado": True, "texto": "Validación geográfica: INEGI verificado."}

    if cobertura_externa.get("hidrica_no_verificable"):
        estado_txt = entidades.get("estado") or "el estado declarado"
        hid = {
            "verificado": False,
            "texto": (
                f"Validación hídrica: NO VERIFICABLE — sin dato de referencia CONAGUA "
                f"para {estado_txt} (módulo aún no conectado, Fase 5)."
            ),
        }
    else:
        hid = {"verificado": True, "texto": "Validación hídrica: CONAGUA verificado."}

    return {"geografica": geo, "hidrica": hid}


def _construir_resumen_ejecutivo(
    resultado_icti: dict, hallazgos: list[dict], cobertura_externa: dict, entidades: dict
) -> dict:
    conteo_criticidad = {c: 0 for c in _ORDEN_CRITICIDAD}
    for h in hallazgos:
        crit = h.get("criticidad")
        if crit in conteo_criticidad:
            conteo_criticidad[crit] += 1

    return {
        "icti": resultado_icti["puntaje_total"],
        "nivel": resultado_icti["nivel"],
        "conteo_por_criticidad": conteo_criticidad,
        "top5_prioridades": _construir_top5(hallazgos),
        "tiempo_estimado_correccion": _estimar_tiempo_correccion(hallazgos),
        "bloque_cobertura": _construir_bloque_cobertura(cobertura_externa, entidades),
    }


# ---------------------------------------------------------------------------
# Orquestador principal — ÚNICO punto de entrada que app.py reemplaza en
# caliente por el mock cuando se activa Modo Prueba. Mantener esta firma.
# ---------------------------------------------------------------------------

def auditar_informe(client: anthropic.Anthropic, texto: str, model_id: str) -> dict:
    """
    Orquesta la auditoría completa: Pass 1 (extracción sin truncar) ->
    Reduce (consistencia y coherencia hídrica en Python) -> Pass 2
    (checklist regulatorio/técnico enrutado) -> ICTI determinista ->
    resumen ejecutivo.
    """
    chunks = _ax.segmentar_documento(texto)
    if not chunks:
        return {
            "_error": "El documento no produjo fragmentos analizables.",
            "entidades": {}, "hallazgos": [], "icti": {"puntaje_total": 0, "nivel": "RECHAZABLE"},
        }

    pass1_stats: dict = {}
    hechos = _ax.extraer_hechos_documento(client, model_id, chunks, stats_out=pass1_stats)
    tabla_hechos = _ax.construir_tabla_hechos(hechos)
    entidades = _construir_entidades_compatibles(tabla_hechos)

    hallazgos: list[dict] = []
    hallazgos += _discrepancias_a_hallazgos(_ax.detectar_discrepancias(hechos))
    hallazgos += _hallazgos_coherencia_hidrica(tabla_hechos)
    hallazgos += evaluar_checklist_regulatorio(client, model_id, chunks)

    # INEGI (Fase 4) y CONAGUA (Fase 5) todavía no están conectados aquí —
    # se deja el campo listo y honesto: NO_VERIFICABLE, nunca "verificado" falso.
    cobertura_externa = {
        "geografica_no_verificable": True,
        "hidrica_no_verificable":    True,
    }

    resultado_icti = _icti_calc.calcular_icti(hallazgos, cobertura_externa)
    resumen_ejecutivo = _construir_resumen_ejecutivo(
        resultado_icti, hallazgos, cobertura_externa, entidades
    )

    return {
        "entidades":            entidades,
        "hallazgos":            hallazgos,
        "icti":                 resultado_icti,
        "resumen_ejecutivo":    resumen_ejecutivo,
        "cobertura_externa":    cobertura_externa,
        "total_paginas":        chunks[-1]["pagina_fin"] if chunks else 0,
        "total_chunks":         len(chunks),
        "pass1_stats":          pass1_stats,
    }


# ---------------------------------------------------------------------------
# Renderizado — ICTI con las 5 categorías nuevas
# ---------------------------------------------------------------------------

def _render_icti(resultado_icti: dict) -> None:
    nivel   = resultado_icti.get("nivel", "RECHAZABLE")
    puntaje = resultado_icti.get("puntaje_total", 0)
    color_t, color_bg, emoji = _ICTI_COLORES.get(nivel, _ICTI_COLORES["RECHAZABLE"])

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

    categorias = resultado_icti.get("categorias", {})
    cols = st.columns(len(categorias) or 1)
    for col, (cat, datos) in zip(cols, categorias.items()):
        label = _CATEGORIA_LABEL.get(cat, cat)
        if datos.get("no_verificable_externamente"):
            col.metric(label=label, value=f"{datos['puntaje']}/{datos['maximo']}", delta="NO VERIFICABLE — otorgado")
        else:
            pct = int((datos["puntaje"] / datos["maximo"]) * 100) if datos["maximo"] else 0
            col.metric(label=label, value=f"{datos['puntaje']}/{datos['maximo']}",
                       delta=f"{pct}%", delta_color="normal" if pct >= 70 else "inverse")


def _render_bloque_cobertura(bloque: dict) -> None:
    """Bloque visible de estado de verificación — no una nota al pie."""
    st.markdown("#### 🛰️ Estado de verificación externa")
    col_geo, col_hid = st.columns(2)
    for col, (clave, datos) in zip((col_geo, col_hid), bloque.items()):
        icono = "✅" if datos["verificado"] else "⚪"
        bg = "#d4edda" if datos["verificado"] else "#f0f0f0"
        color = "#155724" if datos["verificado"] else "#555"
        col.markdown(f"""
        <div style="background:{bg};border-radius:8px;padding:12px 16px;
                    font-size:13px;color:{color};font-weight:600">
          {icono} {datos['texto']}
        </div>
        """, unsafe_allow_html=True)


def _render_resumen_ejecutivo(resumen: dict) -> None:
    st.markdown("#### 📋 Resumen ejecutivo — léelo en 60 segundos")
    _render_bloque_cobertura(resumen["bloque_cobertura"])
    st.markdown("---")

    conteo = resumen["conteo_por_criticidad"]
    cols = st.columns(5)
    for col, crit in zip(cols, ("CRITICO", "ALTO", "MEDIO", "BAJO", "INFORMATIVO")):
        _, _, emoji = _CRITICIDAD_COLORES[crit]
        col.metric(f"{emoji} {crit.title()}", conteo.get(crit, 0))

    st.info(f"⏱️ **Tiempo estimado de corrección: {resumen['tiempo_estimado_correccion']}**")

    st.markdown("##### 🎯 Top 5 — corrige esto primero")
    if not resumen["top5_prioridades"]:
        st.success("✅ No hay hallazgos accionables pendientes.")
    else:
        for i, item in enumerate(resumen["top5_prioridades"], 1):
            _, bg, emoji = _CRITICIDAD_COLORES.get(item["criticidad"], ("#333", "#eee", "⚪"))
            pagina_txt = f"pág. {item['pagina']}" if item["pagina"] is not None else "página no determinada"
            st.markdown(f"""
            <div style="background:{bg};border-radius:8px;padding:10px 14px;margin-bottom:6px">
              <b>{i}. {emoji} [{item['criticidad']}] {item['categoria']}</b> — {pagina_txt}<br>
              {item['descripcion']}
            </div>
            """, unsafe_allow_html=True)


def _render_entidades(entidades: dict) -> None:
    if not entidades:
        st.caption("No se pudieron extraer entidades del documento.")
        return
    import pandas as pd
    filas = []
    for key, label in _ENTIDADES_LABELS.items():
        val = entidades.get(key)
        filas.append({
            "Campo":  label,
            "Valor":  val if val else "— no encontrado —",
            "Estado": "✅" if val else "⚠️",
        })
    df = pd.DataFrame(filas)
    st.dataframe(
        df.style.map(
            lambda v: "color:#cc6600;font-style:italic" if v == "— no encontrado —" else "",
            subset=["Valor"],
        ),
        use_container_width=True, hide_index=True,
    )


def _render_hallazgos(hallazgos: list[dict]) -> None:
    if not hallazgos:
        st.success("✅ No se detectaron hallazgos. El informe pasó todas las verificaciones.")
        return

    criticidades_presentes = sorted(
        {h.get("criticidad", "INFORMATIVO") for h in hallazgos},
        key=lambda c: _ORDEN_CRITICIDAD.get(c, 99),
    )
    filtro = st.multiselect(
        "Filtrar por criticidad:", criticidades_presentes,
        default=criticidades_presentes, key="filtro_criticidad_auditor",
    )
    filtrados = [h for h in hallazgos if h.get("criticidad") in filtro]
    filtrados.sort(key=lambda h: (
        _ORDEN_CRITICIDAD.get(h.get("criticidad"), 99),
        _ORDEN_CATEGORIA_DESEMPATE.get(h.get("categoria"), 99),
    ))

    for h in filtrados:
        crit = h.get("criticidad", "INFORMATIVO")
        _, bg, emoji = _CRITICIDAD_COLORES.get(crit, ("#333", "#eee", "⚪"))
        estado_icono = _ESTADO_VERIFICACION_ICONO.get(h.get("estado_verificacion", ""), "")
        categoria_label = _CATEGORIA_LABEL.get(h.get("categoria"), h.get("categoria"))
        pagina_txt = f"pág. {h['pagina']}" if h.get("pagina") is not None else "página no determinada"

        with st.expander(f"{emoji} [{crit}] {categoria_label} — {pagina_txt}"):
            st.markdown(h.get("descripcion", ""))
            if h.get("cita_textual"):
                st.markdown(f"**Cita textual:** _{h['cita_textual']}_")
            if h.get("cita_normativa"):
                st.caption(f"📖 {h['cita_normativa']}")
            if h.get("autoridad"):
                st.caption(f"🏛️ Autoridad: {h['autoridad']}")
            st.caption(f"{estado_icono} Estado de verificación: {h.get('estado_verificacion', '—')}")


# ---------------------------------------------------------------------------
# Función principal de renderizado (llamada desde app.py) — MISMA FIRMA
# ---------------------------------------------------------------------------

def render_herramienta_auditor(
    client: anthropic.Anthropic,
    model_id: str,
    proyecto_actual: str | None,
) -> None:
    st.header("🔎 Herramienta 2 — Revisión Técnica Ambiental")
    st.caption(
        "Sube el PDF del informe preliminar. El documento se analiza completo, "
        "sin límite de páginas ni de hallazgos. El ICTI se calcula de forma "
        "determinista a partir de lo que se detecta."
    )

    if not proyecto_actual:
        st.warning("⚠️ Selecciona o crea un proyecto en la barra lateral para continuar.")
        return

    uploaded_pdf = st.file_uploader(
        "Sube el PDF del informe preliminar",
        type=["pdf"], key="uploader_auditor",
        help="El informe debe tener texto extraíble (no escaneado puro).",
    )

    if not uploaded_pdf:
        st.info(
            "👆 Sube el PDF del informe para comenzar la auditoría técnica.\n\n"
            "**¿Qué se analiza?** El documento completo, sin truncar — "
            "consistencia de datos, vacíos regulatorios, solidez técnica, "
            "y coherencia hídrica interna, con ICTI determinista."
        )
        return

    if st.button("🔍 Iniciar auditoría técnica", type="primary", key="btn_auditor"):
        pdf_bytes = uploaded_pdf.read()

        with st.spinner("Extrayendo texto del informe…"):
            texto = _extraer_texto_informe(pdf_bytes)

        chars = len(texto.replace(" ", "").replace("\n", ""))
        st.caption(f"📄 Caracteres útiles extraídos: {chars:,}")

        if not _tiene_texto_suficiente(texto):
            st.error(
                "❌ No se pudo extraer suficiente texto del PDF. "
                "El informe parece ser un PDF escaneado sin capa de texto."
            )
            return

        with st.spinner("Auditando el documento completo…"):
            resultado = auditar_informe(client, texto, model_id)

        if resultado.get("_error"):
            st.error(f"❌ {resultado['_error']}")
            return

        st.session_state["_auditoria_resultado"]  = resultado
        st.session_state["_auditoria_proyecto"]   = proyecto_actual
        st.session_state["_auditoria_nombre_pdf"] = uploaded_pdf.name

        # Registrar evento e ICTI en gestor_proyectos.py — misma integración de siempre.
        try:
            from gestor_proyectos import registrar_evento as _reg_ev
            from gestor_proyectos import actualizar_proyecto as _act_proy
            icti_val = resultado["icti"]["puntaje_total"]
            nivel = resultado["icti"]["nivel"]
            hallazgos = resultado["hallazgos"]
            n_consistencia = sum(1 for h in hallazgos if h["categoria"] == "consistencia_datos")
            n_regulatorio  = sum(1 for h in hallazgos if h["categoria"] == "completitud_regulatoria")
            _reg_ev(
                proyecto_actual, "AUDITORIA",
                f"Auditoría técnica completada — ICTI {icti_val}/100 ({nivel}) · "
                f"{len(hallazgos)} hallazgo(s) totales",
                st.session_state.get("usuario_actual", "Ingeniero"),
                {
                    "icti": icti_val, "nivel": nivel,
                    "discrepancias": n_consistencia, "vacios": n_regulatorio,
                    "total_hallazgos": len(hallazgos),
                    "total_paginas": resultado.get("total_paginas", 0),
                    "pdf": uploaded_pdf.name,
                },
            )
            _act_proy(proyecto_actual, {"icti_ultimo": icti_val})
        except Exception:
            pass   # El historial nunca rompe el flujo principal

    # ── Mostrar resultados (de la sesión actual o de ejecución previa) ──────
    resultado = st.session_state.get("_auditoria_resultado")
    if resultado and st.session_state.get("_auditoria_proyecto") == proyecto_actual:
        nombre_pdf = st.session_state.get("_auditoria_nombre_pdf", "informe.pdf")
        st.markdown(f"**Informe analizado:** `{nombre_pdf}` · "
                     f"{resultado.get('total_paginas', 0)} páginas · "
                     f"{resultado.get('total_chunks', 0)} fragmentos procesados")
        st.markdown("---")

        _render_icti(resultado["icti"])
        st.markdown("---")
        _render_resumen_ejecutivo(resultado["resumen_ejecutivo"])
        st.markdown("---")

        tab_ent, tab_hall = st.tabs([
            "📋 Entidades extraídas",
            f"⚠️ Hallazgos ({len(resultado['hallazgos'])})",
        ])
        with tab_ent:
            _render_entidades(resultado["entidades"])
        with tab_hall:
            _render_hallazgos(resultado["hallazgos"])

        st.markdown("---")
        st.download_button(
            "⬇️ Descargar reporte completo (JSON)",
            data=json.dumps(resultado, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=f"auditoria_{proyecto_actual}.json",
            mime="application/json",
        )
