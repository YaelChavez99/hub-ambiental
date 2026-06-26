"""
Módulo: inegi_api.py
Integración con la API real del INEGI para enriquecer el Capítulo 5
con datos verificables de población, vivienda y geografía municipal.

FUENTES REALES UTILIZADAS:
  1. Catálogo geoestadístico (gaia.inegi.org.mx/wscatgeo/v2/)
     — Sin token, devuelve claves oficiales de estado/municipio.
  2. API del Banco de Indicadores (www.inegi.org.mx/app/api/indicadores)
     — Requiere token gratuito (alta en inegi.org.mx, ~5 min).
     — Indicadores: población total, viviendas, PEA (Censo 2020).

LIMITACIÓN CONOCIDA: si no hay token configurado en secrets.toml,
el módulo degrada graciosamente devolviendo None — el Capítulo 5
sigue generándose, solo sin el enriquecimiento numérico verificado.

Compatible con: Python 3.10+, requests>=2.31, streamlit>=1.35, psycopg2>=2.9
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

import psycopg2
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Constantes — Claves de indicadores del Censo 2020 (Banco de Indicadores)
# ---------------------------------------------------------------------------
_URL_CATALOGO   = "https://gaia.inegi.org.mx/wscatgeo/v2/"
_URL_INDICADOR  = "https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml/INDICATOR"

# Claves de indicadores INEGI (Censo de Población y Vivienda 2020)
_INDICADORES = {
    "poblacion_total": "1002000001",   # Población total
    "viviendas_total": "1003000001",   # Total de viviendas particulares habitadas
    "pea":             "1004000016",   # Población Económicamente Activa
    "grado_escolar":   "6200240553",   # Grado promedio de escolaridad
}

_TIMEOUT = 8   # segundos — no bloquear la UI si INEGI está lento/caído


def _normalizar(texto: str) -> str:
    """Quita acentos y normaliza mayúsculas para comparar nombres de municipios."""
    nfkd = unicodedata.normalize("NFKD", texto)
    sin_acentos = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sin_acentos.strip().upper()


# ---------------------------------------------------------------------------
# Conexión BD para cache
# ---------------------------------------------------------------------------

def _conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(st.secrets["DATABASE_URL"])


def migrar_bd_inegi() -> bool:
    """Crea tabla de cache para no re-consultar INEGI en cada generación."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS inegi_cache (
                id_cache       SERIAL PRIMARY KEY,
                estado         TEXT NOT NULL,
                municipio      TEXT NOT NULL,
                clave_entidad  TEXT,
                clave_municipio TEXT,
                poblacion_total TEXT,
                viviendas_total TEXT,
                pea            TEXT,
                grado_escolar  TEXT,
                fuente         TEXT DEFAULT 'INEGI Censo 2020',
                fecha_consulta TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(estado, municipio)
            )
        """)
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception:
        return False


def _leer_cache(estado: str, municipio: str) -> dict | None:
    """Lee del cache local si ya se consultó antes este municipio."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """SELECT clave_entidad, clave_municipio, poblacion_total,
                      viviendas_total, pea, grado_escolar, fecha_consulta
               FROM inegi_cache WHERE estado = %s AND municipio = %s""",
            (_normalizar(estado), _normalizar(municipio)),
        )
        row = c.fetchone()
        c.close()
        conn.close()
        if row:
            return {
                "clave_entidad":   row[0], "clave_municipio": row[1],
                "poblacion_total": row[2], "viviendas_total": row[3],
                "pea":             row[4], "grado_escolar":   row[5],
                "fecha_consulta":  row[6], "origen": "cache",
            }
    except Exception:
        pass
    return None


def _guardar_cache(estado: str, municipio: str, datos: dict) -> None:
    """Guarda el resultado en cache para futuras consultas."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """INSERT INTO inegi_cache
               (estado, municipio, clave_entidad, clave_municipio,
                poblacion_total, viviendas_total, pea, grado_escolar)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (estado, municipio) DO UPDATE SET
                 clave_entidad   = EXCLUDED.clave_entidad,
                 clave_municipio = EXCLUDED.clave_municipio,
                 poblacion_total = EXCLUDED.poblacion_total,
                 viviendas_total = EXCLUDED.viviendas_total,
                 pea             = EXCLUDED.pea,
                 grado_escolar   = EXCLUDED.grado_escolar,
                 fecha_consulta  = CURRENT_TIMESTAMP""",
            (
                _normalizar(estado), _normalizar(municipio),
                datos.get("clave_entidad"), datos.get("clave_municipio"),
                datos.get("poblacion_total"), datos.get("viviendas_total"),
                datos.get("pea"), datos.get("grado_escolar"),
            ),
        )
        conn.commit()
        c.close()
        conn.close()
    except Exception:
        pass   # El cache nunca debe romper el flujo principal


# ---------------------------------------------------------------------------
# 1. Catálogo geoestadístico — claves oficiales (sin token)
# ---------------------------------------------------------------------------

def buscar_clave_municipio(estado: str, municipio: str) -> dict | None:
    """
    Busca la clave oficial de entidad/municipio en el catálogo INEGI.
    No requiere token. Si falla, retorna None (degradación elegante).
    """
    try:
        # El catálogo geoestadístico INEGI expone consulta por nombre
        resp = requests.get(
            _URL_CATALOGO,
            params={"type": "json"},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        # Nota: la estructura exacta del catálogo varía; este parser
        # busca coincidencia por nombre normalizado en la respuesta.
        data = resp.json()
        estado_norm    = _normalizar(estado)
        municipio_norm = _normalizar(municipio)
        # Estructura esperada: lista de entidades con sus municipios
        for ent in data if isinstance(data, list) else data.get("entidades", []):
            nombre_ent = _normalizar(ent.get("nombre", ""))
            if estado_norm in nombre_ent or nombre_ent in estado_norm:
                for mun in ent.get("municipios", []):
                    nombre_mun = _normalizar(mun.get("nombre", ""))
                    if municipio_norm in nombre_mun or nombre_mun in municipio_norm:
                        return {
                            "clave_entidad":   ent.get("clave", ""),
                            "clave_municipio": mun.get("clave", ""),
                            "nombre_entidad":  ent.get("nombre", ""),
                            "nombre_municipio":mun.get("nombre", ""),
                        }
        return None
    except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 2. Banco de Indicadores — datos del Censo 2020 (requiere token)
# ---------------------------------------------------------------------------

def _obtener_token() -> str | None:
    """Lee el token de INEGI desde secrets.toml. None si no está configurado."""
    try:
        return st.secrets.get("inegi", {}).get("token")
    except Exception:
        return None


def _consultar_indicador(
    clave_indicador: str, clave_geo: str, token: str
) -> str | None:
    """
    Consulta un indicador específico del Banco de Indicadores INEGI.
    clave_geo: clave de área geográfica (ej. '24001' = entidad+municipio).
    """
    url = (
        f"{_URL_INDICADOR}/{clave_indicador}/es/{clave_geo}"
        f"/false/BISE/2.0/{token}?type=json"
    )
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json()
        series = data.get("Series", [])
        if series and series[0].get("OBSERVATIONS"):
            obs = series[0]["OBSERVATIONS"]
            # Tomar el valor más reciente disponible
            ultimo = sorted(obs, key=lambda o: o.get("TIME_PERIOD", ""))[-1]
            return ultimo.get("OBS_VALUE")
        return None
    except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError):
        return None


def obtener_indicadores_municipio(
    estado: str, municipio: str, usar_cache: bool = True
) -> dict | None:
    """
    Función principal: obtiene datos verificados de INEGI para un municipio.

    Returns:
        Dict con población, viviendas, PEA, grado escolar y metadatos
        de origen (cache/api/no_disponible), o None si no se pudo
        resolver ni la clave del municipio.
    """
    # 1. Revisar cache primero
    if usar_cache:
        cached = _leer_cache(estado, municipio)
        if cached:
            return cached

    # 2. Resolver clave del municipio
    clave_info = buscar_clave_municipio(estado, municipio)
    if not clave_info:
        return {
            "clave_entidad": None, "clave_municipio": None,
            "poblacion_total": None, "viviendas_total": None,
            "pea": None, "grado_escolar": None,
            "origen": "no_disponible",
            "nota": (
                f"No se pudo resolver la clave geoestadística de "
                f"'{municipio}, {estado}' en el catálogo INEGI."
            ),
        }

    clave_geo = f"{clave_info['clave_entidad']}{clave_info['clave_municipio']}"

    # 3. Consultar indicadores (requiere token)
    token = _obtener_token()
    if not token:
        resultado = {
            **clave_info,
            "poblacion_total": None, "viviendas_total": None,
            "pea": None, "grado_escolar": None,
            "origen": "sin_token",
            "nota": (
                "Token de INEGI no configurado en secrets.toml. "
                "Solo se resolvió la clave geográfica oficial. "
                "Agrega [inegi] token='...' para datos del Censo 2020."
            ),
        }
        return resultado

    datos = {**clave_info, "origen": "api"}
    for nombre, clave_ind in _INDICADORES.items():
        datos[nombre] = _consultar_indicador(clave_ind, clave_geo, token)

    _guardar_cache(estado, municipio, datos)
    return datos


# ---------------------------------------------------------------------------
# Renderizado — Widget de estado de la integración
# ---------------------------------------------------------------------------

def render_estado_inegi(datos: dict | None) -> None:
    """Muestra un indicador visual del origen y calidad de los datos INEGI."""
    if not datos:
        st.warning("⚠️ INEGI: sin datos disponibles para este municipio.")
        return

    origen = datos.get("origen", "no_disponible")
    if origen == "api":
        st.success(
            f"✅ **INEGI (API en vivo)** — Clave {datos.get('clave_entidad','')}"
            f"{datos.get('clave_municipio','')} · "
            f"Población: {datos.get('poblacion_total','—')} hab."
        )
    elif origen == "cache":
        fecha = datos.get("fecha_consulta")
        fecha_s = fecha.strftime("%d/%m/%Y") if fecha else "—"
        st.info(f"💾 **INEGI (cache)** — Consultado el {fecha_s}")
    elif origen == "sin_token":
        st.warning(f"⚠️ **INEGI (parcial)** — {datos.get('nota','')}")
    else:
        st.warning(f"⚠️ **INEGI no disponible** — {datos.get('nota','')}")
