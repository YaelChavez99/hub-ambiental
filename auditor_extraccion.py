"""
Módulo: auditor_extraccion.py
Segmentación de documentos y extracción de hechos con trazabilidad (Fase 1).

Responsabilidad única:
  1. Segmentar el texto completo del informe (con marcadores de página) en
     fragmentos ("chunks") acotados por tamaño y por sección detectada, sin
     truncar nunca el documento — funciona igual con 50 que con 800 páginas.
  2. Pass 1 — Extracción de hechos: por cada chunk, una llamada barata y
     acotada a Claude que solo EXTRAE datos explícitos con su ubicación
     (página, sección, cita textual). No analiza, no opina, no infiere.
     Las llamadas son independientes entre sí → se ejecutan en paralelo.
  3. Reduce — Detección de discrepancias: comparación en Python puro de
     las apariciones de una misma entidad con valores distintos. Esto NO
     usa Claude — es 100% determinista y trazable a página exacta, y es la
     base de la categoría "consistencia de datos" del ICTI.

No depende de app.py — se importa como módulo.
Compatible con: Python 3.10+, anthropic>=0.28, streamlit>=1.35
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from typing import Any

import anthropic
import streamlit as st

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MAX_CHARS_POR_CHUNK = 10_000   # tope por fragmento enviado a Claude en Pass 1
MAX_WORKERS_EXTRACCION = 5     # llamadas concurrentes — evita saturar rate limits
MAX_TOKENS_EXTRACCION = 2_000  # salida acotada por chunk (solo datos, no prosa)

_PATRON_PAGINA = re.compile(r'--- PÁGINA (\d+)/(\d+) ---\n?')

# Detección de encabezados de sección — best-effort para enrutamiento en
# Pass 2, NO es crítico que sea perfecto: si falla, el chunk queda con
# seccion_titulo genérico y aun así conserva su página exacta.
_PATRON_SECCION = re.compile(
    r'^\s*(CAP[IÍ]TULO\s+\d+[.:]?[^\n]{0,80}'
    r'|ANEXO\s+[A-Z0-9]+[.:]?[^\n]{0,80}'
    r'|\d+\.\d+(?:\.\d+)?\s+[A-ZÁÉÍÓÚÑ][^\n]{0,80})',
    re.MULTILINE,
)

_SECCION_SIN_DETECTAR = "SIN SECCIÓN DETECTADA"

SYSTEM_PROMPT_EXTRACCION = """
Eres un extractor de datos técnicos de informes ambientales mexicanos.

TU ÚNICA TAREA es identificar datos EXPLÍCITAMENTE escritos en el fragmento
de texto proporcionado. NO analices. NO opines. NO evalúes calidad. NO infieras
ni completes valores que no estén escritos literalmente.

Para cada dato relevante que encuentres, devuelve un objeto:
{"entidad": "<nombre_normalizado>", "valor": "<valor_textual_o_numerico>", "cita_textual": "<fragmento_literal_max_200_caracteres>"}

Usa estos nombres de entidad cuando apliquen (usa otros solo si el dato no
encaja en ninguno de estos):
  numero_informe, volumen_derramado_litros, area_afectada_m2, volumen_suelo_m3,
  municipio, estado, coordenadas_x, coordenadas_y, km_autopista,
  nombre_autopista, contaminante, fecha_siniestro, fecha_muestreo,
  numero_pozos_muestreo, tipo_muestreo,
  responsable_tecnico, uso_de_suelo, empresa_vehiculo,
  nombre_acuifero, profundidad_nivel_freatico_m,
  distancia_cuerpo_agua_superficial_m,
  evaluacion_riesgo_migracion_acuifero (valor "SI" solo si el texto contiene
    una evaluación real de riesgo de migración vertical/lateral hacia el
    acuífero, no una simple mención del acuífero),
  laboratorio_acreditado, numero_acreditacion_ema, metodo_analitico,
  norma_citada.

REGLAS:
- Reporta SOLO lo explícito en el texto. Si no hay datos relevantes, devuelve [].
- No repitas el mismo dato dos veces si aparece igual dos veces en el mismo fragmento.
- Devuelve ÚNICAMENTE el arreglo JSON. Sin markdown. Sin texto adicional. Sin explicaciones.
"""


# ---------------------------------------------------------------------------
# Utilidades de normalización
# ---------------------------------------------------------------------------

def _normalizar_texto(texto: str) -> str:
    """Quita acentos, colapsa espacios y pasa a mayúsculas para comparar."""
    nfkd = unicodedata.normalize("NFKD", texto)
    sin_acentos = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_acentos).strip().upper()


def _normalizar_valor(valor: Any) -> tuple[str, float | None]:
    """
    Normaliza un valor para comparación de discrepancias.
    Devuelve (representación_string, valor_numérico_o_None).
    Trata "32,077 litros", "32077" y "32,077.0" como equivalentes.
    """
    texto = str(valor).strip()
    solo_numero = re.sub(r"[^\d.\-]", "", texto.replace(",", ""))
    try:
        if solo_numero and solo_numero not in ("-", "."):
            return (texto, float(solo_numero))
    except ValueError:
        pass
    return (_normalizar_texto(texto), None)


def calcular_hash_documento(pdf_bytes: bytes) -> str:
    """Huella única del PDF — permite reutilizar el resultado de Pass 1
    sin reprocesar el mismo documento (caché en BD, ver auditor_persistencia.py)."""
    return hashlib.sha256(pdf_bytes).hexdigest()


# ---------------------------------------------------------------------------
# 1. Segmentación en chunks con trazabilidad de página y sección
# ---------------------------------------------------------------------------

def _dividir_por_paginas(texto: str) -> list[tuple[int, str]]:
    """
    Divide el texto (ya con marcadores '--- PÁGINA N/total ---') en
    una lista de (numero_pagina, texto_de_esa_pagina).
    """
    partes = _PATRON_PAGINA.split(texto)
    # re.split con grupos de captura intercala: [preludio, pag, total, texto, pag, total, texto, ...]
    paginas: list[tuple[int, str]] = []
    i = 1
    while i + 1 < len(partes):
        try:
            num_pagina = int(partes[i])
        except (ValueError, TypeError):
            i += 3
            continue
        contenido = partes[i + 2] if i + 2 < len(partes) else ""
        paginas.append((num_pagina, contenido))
        i += 3
    if not paginas and texto.strip():
        # El texto no traía marcadores de página — se trata como página única
        paginas.append((1, texto))
    return paginas


def segmentar_documento(
    texto: str, max_chars_chunk: int = MAX_CHARS_POR_CHUNK
) -> list[dict]:
    """
    Segmenta el documento completo en chunks acotados por tamaño y por
    sección detectada. NUNCA trunca — procesa el documento entero sin
    importar su extensión (reemplaza el límite de 180,000 caracteres).

    Cada chunk conserva:
      - chunk_id:       índice secuencial
      - pagina_inicio:  primera página que contiene
      - pagina_fin:      última página que contiene
      - seccion_titulo: encabezado detectado más reciente (best-effort)
      - texto:          contenido del chunk

    Returns:
        Lista de dicts, uno por chunk, en orden del documento.
    """
    paginas = _dividir_por_paginas(texto)
    if not paginas:
        return []

    chunks: list[dict] = []
    buffer_texto: list[str] = []
    buffer_chars = 0
    pagina_inicio: int | None = None
    pagina_actual: int | None = None
    seccion_actual = _SECCION_SIN_DETECTAR

    def _cerrar_chunk() -> None:
        if not buffer_texto:
            return
        chunks.append({
            "chunk_id":       len(chunks),
            "pagina_inicio":  pagina_inicio,
            "pagina_fin":     pagina_actual,
            "seccion_titulo": seccion_actual,
            "texto":          "".join(buffer_texto).strip(),
        })

    for num_pagina, texto_pagina in paginas:
        if pagina_inicio is None:
            pagina_inicio = num_pagina
        pagina_actual = num_pagina

        # Detectar el encabezado de sección más reciente en esta página
        encabezados = _PATRON_SECCION.findall(texto_pagina)
        nueva_seccion = encabezados[-1].strip() if encabezados else None

        # Si aparece una sección nueva y ya hay contenido acumulado
        # sustancial, cerrar el chunk actual antes de seguir.
        if nueva_seccion and buffer_chars > 200 and nueva_seccion != seccion_actual:
            _cerrar_chunk()
            buffer_texto, buffer_chars = [], 0
            pagina_inicio = num_pagina
            seccion_actual = nueva_seccion
        elif nueva_seccion:
            seccion_actual = nueva_seccion

        fragmento = f"\n--- PÁGINA {num_pagina} ---\n{texto_pagina}"
        buffer_texto.append(fragmento)
        buffer_chars += len(fragmento)

        # Tope de tamaño — cierra a mitad de sección si hace falta,
        # la sección larga simplemente continúa en el siguiente chunk.
        if buffer_chars >= max_chars_chunk:
            _cerrar_chunk()
            buffer_texto, buffer_chars = [], 0
            pagina_inicio = None

    _cerrar_chunk()
    return chunks


# ---------------------------------------------------------------------------
# 2. Pass 1 — Extracción de hechos (paralelizable)
# ---------------------------------------------------------------------------

def _limpiar_json(raw: str) -> str:
    raw = re.sub(r"```(?:json)?\s*", "", raw)
    raw = raw.strip().strip("`").strip()
    return re.sub(r',\s*([\]}])', r'\1', raw)


def _parsear_hechos(raw: str) -> list[dict]:
    """Parsea el arreglo JSON de hechos con una capa de fallback."""
    cleaned = _limpiar_json(raw)
    match = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        datos = json.loads(cleaned)
        return datos if isinstance(datos, list) else []
    except json.JSONDecodeError:
        cleaned2 = re.sub(r'[\x00-\x1f\x7f]', ' ', cleaned)
        cleaned2 = re.sub(r',\s*([\]}])', r'\1', cleaned2)
        try:
            datos = json.loads(cleaned2)
            return datos if isinstance(datos, list) else []
        except json.JSONDecodeError:
            return []


def _extraer_hechos_chunk(
    client: anthropic.Anthropic, model_id: str, chunk: dict
) -> list[dict]:
    """
    Envía un único chunk a Claude para extracción de hechos.
    Salida acotada (MAX_TOKENS_EXTRACCION) porque solo pide datos, no prosa.
    Cada hecho devuelto se enriquece con la ubicación del chunk de origen.
    """
    try:
        msg = client.messages.create(
            model=model_id,
            max_tokens=MAX_TOKENS_EXTRACCION,
            system=SYSTEM_PROMPT_EXTRACCION,
            messages=[{
                "role": "user",
                "content": (
                    f"Fragmento del informe (páginas {chunk['pagina_inicio']}"
                    f"-{chunk['pagina_fin']}, sección: {chunk['seccion_titulo']}):\n\n"
                    f"{chunk['texto']}"
                ),
            }],
        )
        hechos = _parsear_hechos(msg.content[0].text.strip())
    except anthropic.APIStatusError:
        hechos = []
    except Exception:
        hechos = []

    for h in hechos:
        h["chunk_id"] = chunk["chunk_id"]
        h["pagina_inicio"] = chunk["pagina_inicio"]
        h["pagina_fin"] = chunk["pagina_fin"]
        h["seccion_titulo"] = chunk["seccion_titulo"]
    return hechos


def extraer_hechos_documento(
    client: anthropic.Anthropic,
    model_id: str,
    chunks: list[dict],
    max_workers: int = MAX_WORKERS_EXTRACCION,
    mostrar_progreso: bool = True,
) -> list[dict]:
    """
    Ejecuta Pass 1 sobre todos los chunks del documento en paralelo.
    Esta es la ÚNICA pasada que toca el documento completo — el resto de
    los módulos de auditoría trabajan sobre esta salida estructurada, no
    sobre el texto crudo.

    Returns:
        Lista de hechos extraídos, cada uno con entidad/valor/cita_textual
        y su ubicación exacta (chunk_id, pagina_inicio, pagina_fin, seccion_titulo).
    """
    if not chunks:
        return []

    todos_los_hechos: list[dict] = []
    progreso = st.progress(0, text=f"Extrayendo datos… 0/{len(chunks)} fragmentos") \
        if mostrar_progreso else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futuros = {
            pool.submit(_extraer_hechos_chunk, client, model_id, chunk): chunk
            for chunk in chunks
        }
        completados = 0
        for futuro in concurrent.futures.as_completed(futuros):
            todos_los_hechos.extend(futuro.result())
            completados += 1
            if progreso is not None:
                progreso.progress(
                    completados / len(chunks),
                    text=f"Extrayendo datos… {completados}/{len(chunks)} fragmentos",
                )

    if progreso is not None:
        progreso.empty()

    return todos_los_hechos


# ---------------------------------------------------------------------------
# 3. Reduce — Detección de discrepancias (Python puro, sin Claude)
# ---------------------------------------------------------------------------

# Entidades donde una repetición con el MISMO valor en distintas páginas es
# normal y no debe compararse como si fuera una sola ocurrencia esperada
# (ej. "municipio" se repite legítimamente en todo el documento).
_ENTIDADES_UNICAS_ESPERADAS = {
    "volumen_derramado_litros", "area_afectada_m2", "volumen_suelo_m3",
    "coordenadas_x", "coordenadas_y", "km_autopista", "fecha_siniestro",
    "numero_pozos_muestreo",
}


def detectar_discrepancias(hechos: list[dict]) -> list[dict]:
    """
    Compara en Python (sin llamar a Claude) las apariciones de la misma
    entidad a lo largo del documento. Si el mismo dato aparece con valores
    distintos en páginas distintas, es una discrepancia CONFIRMADA — se
    encontró textualmente, no se infirió.

    Solo evalúa las entidades de _ENTIDADES_UNICAS_ESPERADAS: son las que
    tiene sentido que tengan un único valor correcto en todo el informe.
    Esto es exactamente lo que Pass 1 (Claude) no puede garantizar de forma
    reproducible y Python sí.

    Returns:
        Lista de discrepancias con página y cita de cada valor en conflicto.
    """
    por_entidad: dict[str, list[dict]] = defaultdict(list)
    for h in hechos:
        entidad = str(h.get("entidad", "")).strip().lower()
        if entidad in _ENTIDADES_UNICAS_ESPERADAS and h.get("valor") not in (None, ""):
            por_entidad[entidad].append(h)

    discrepancias: list[dict] = []
    for entidad, ocurrencias in por_entidad.items():
        ocurrencias.sort(key=lambda h: (h.get("pagina_inicio") or 0))

        grupos: dict[str, dict] = {}   # valor_normalizado -> primera ocurrencia
        for h in ocurrencias:
            valor_str, valor_num = _normalizar_valor(h["valor"])
            clave = f"{valor_num:.4f}" if valor_num is not None else valor_str
            if clave not in grupos:
                grupos[clave] = h

        if len(grupos) <= 1:
            continue   # todos los valores encontrados son equivalentes

        referencia, *discrepantes = sorted(
            grupos.values(), key=lambda h: (h.get("pagina_inicio") or 0)
        )
        for disc in discrepantes:
            discrepancias.append({
                "entidad":            entidad,
                "valor_referencia":   referencia.get("valor"),
                "pagina_referencia":  referencia.get("pagina_inicio"),
                "cita_referencia":    referencia.get("cita_textual", ""),
                "valor_discrepante":  disc.get("valor"),
                "pagina_discrepante": disc.get("pagina_inicio"),
                "cita_discrepante":   disc.get("cita_textual", ""),
                "estado_verificacion": "CONFIRMADO",
            })

    return discrepancias


def construir_tabla_hechos(hechos: list[dict]) -> dict[str, list[dict]]:
    """
    Agrupa los hechos extraídos por entidad para consulta rápida de los
    módulos de Pass 2 (ej. validación geográfica lee tabla["municipio"]
    sin tener que volver a tocar el texto crudo del documento).
    """
    tabla: dict[str, list[dict]] = defaultdict(list)
    for h in hechos:
        entidad = str(h.get("entidad", "")).strip().lower()
        if entidad:
            tabla[entidad].append(h)
    return dict(tabla)
