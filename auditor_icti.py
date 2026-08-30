"""
Módulo: auditor_icti.py
Calculadora determinística del Índice de Calidad Técnica del Informe (Fase 2).

Responsabilidad única: convertir una lista de hallazgos ya clasificados
(categoría + criticidad) en un puntaje ICTI de 0-100, con pesos fijos y
descuentos fijos por criticidad. Claude detecta hallazgos; ESTA FUNCIÓN
calcula el puntaje — nunca al revés. Dos corridas sobre los mismos
hallazgos producen siempre el mismo resultado, y cada punto perdido queda
asociado a un hallazgo y su página, para que el reporte sea explicable
línea por línea.

No depende de app.py — se importa como módulo.
Compatible con: Python 3.10+ (sin dependencias externas).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Pesos fijos por categoría — suman 100.
# ---------------------------------------------------------------------------
PESOS_CATEGORIA: dict[str, int] = {
    "consistencia_datos":       25,
    "completitud_regulatoria":  30,
    "solidez_tecnica":          25,
    "validacion_geografica":    10,
    "validacion_hidrica":       10,
}
assert sum(PESOS_CATEGORIA.values()) == 100

# ---------------------------------------------------------------------------
# Descuento fijo por nivel de criticidad (Problema 8 — 5 niveles).
# INFORMATIVO no resta: es contexto útil, no un incumplimiento.
# ---------------------------------------------------------------------------
DESCUENTOS_POR_CRITICIDAD: dict[str, int] = {
    "CRITICO":     10,
    "ALTO":         5,
    "MEDIO":        2,
    "BAJO":         1,
    "INFORMATIVO":  0,
}

_NIVELES_ICTI = (
    # (umbral_minimo_inclusive, nombre_nivel)
    (80, "APROBADO"),
    (60, "OBSERVACIONES"),
    (40, "DEFICIENTE"),
    (0,  "RECHAZABLE"),
)


def _determinar_nivel(puntaje_total: int) -> str:
    for umbral, nombre in _NIVELES_ICTI:
        if puntaje_total >= umbral:
            return nombre
    return "RECHAZABLE"


def calcular_icti(
    hallazgos: list[dict],
    cobertura_externa: dict | None = None,
) -> dict:
    """
    Calcula el ICTI de forma determinista.

    Args:
        hallazgos: lista de hallazgos ya clasificados. Cada uno debe traer
            al menos:
              - "categoria":   una clave de PESOS_CATEGORIA
              - "criticidad":  una clave de DESCUENTOS_POR_CRITICIDAD
              - "descripcion": texto corto del hallazgo (para explicar el punto perdido)
              - "pagina":      página de origen (puede ser None si no aplica)
            Un hallazgo con categoria/criticidad no reconocida se excluye del
            cálculo y se reporta en "hallazgos_invalidos" — no rompe el resto.

        cobertura_externa: {
            "geografica_no_verificable": bool,
            "hidrica_no_verificable": bool,
        }
            Cuando una de estas es True, esa categoría NO se penaliza por
            ningún hallazgo — se otorgan sus puntos completos y se marca
            explícitamente como no evaluada contra fuente externa. Esto
            implementa la regla acordada para CONAGUA (y, por el mismo
            principio de no penalizar por ausencia de fuente externa, para
            INEGI): la falta de dato de referencia nunca resta puntos.

    Returns:
        {
          "puntaje_total": int,
          "nivel": str,
          "categorias": {
              "<categoria>": {
                  "puntaje": int,
                  "maximo": int,
                  "no_verificable_externamente": bool,
                  "descuentos": [
                      {"descripcion": str, "pagina": Any, "criticidad": str, "puntos": int},
                      ...
                  ],
              },
              ...
          },
          "hallazgos_invalidos": [hallazgo, ...],   # categoria/criticidad no reconocida
        }
    """
    cobertura_externa = cobertura_externa or {}
    geo_no_verificable = bool(cobertura_externa.get("geografica_no_verificable", False))
    hid_no_verificable = bool(cobertura_externa.get("hidrica_no_verificable", False))

    categorias: dict[str, dict] = {
        cat: {
            "puntaje": maximo,
            "maximo": maximo,
            "no_verificable_externamente": False,
            "descuentos": [],
        }
        for cat, maximo in PESOS_CATEGORIA.items()
    }
    categorias["validacion_geografica"]["no_verificable_externamente"] = geo_no_verificable
    categorias["validacion_hidrica"]["no_verificable_externamente"] = hid_no_verificable

    hallazgos_invalidos: list[dict] = []

    for h in hallazgos:
        categoria = h.get("categoria")
        criticidad = h.get("criticidad")

        if categoria not in PESOS_CATEGORIA or criticidad not in DESCUENTOS_POR_CRITICIDAD:
            hallazgos_invalidos.append(h)
            continue

        # Regla de cobertura externa: si la categoría está marcada como
        # NO VERIFICABLE por falta de fuente externa, no se descuenta —
        # el hallazgo queda registrado pero sin efecto en el puntaje.
        if categoria == "validacion_geografica" and geo_no_verificable:
            continue
        if categoria == "validacion_hidrica" and hid_no_verificable:
            continue

        puntos = DESCUENTOS_POR_CRITICIDAD[criticidad]
        if puntos == 0:
            continue   # INFORMATIVO — contexto, no descuenta

        categorias[categoria]["descuentos"].append({
            "descripcion": h.get("descripcion", ""),
            "pagina":      h.get("pagina"),
            "criticidad":  criticidad,
            "puntos":      puntos,
        })

    # Aplicar descuentos con piso en 0 por categoría — nunca negativo.
    for cat, datos in categorias.items():
        total_descontado = sum(d["puntos"] for d in datos["descuentos"])
        datos["puntaje"] = max(0, datos["maximo"] - total_descontado)

    puntaje_total = sum(datos["puntaje"] for datos in categorias.values())

    return {
        "puntaje_total": puntaje_total,
        "nivel": _determinar_nivel(puntaje_total),
        "categorias": categorias,
        "hallazgos_invalidos": hallazgos_invalidos,
    }


def explicar_perdida_puntos(resultado_icti: dict) -> list[str]:
    """
    Genera una lista de líneas de texto, una por cada punto de descuento
    aplicado, en el formato que el consultor necesita para entender por qué
    obtuvo 72 y no 85: categoría, puntos perdidos, criticidad, página y
    descripción del hallazgo que lo causó.
    """
    lineas: list[str] = []
    for categoria, datos in resultado_icti["categorias"].items():
        if datos["no_verificable_externamente"]:
            lineas.append(
                f"{categoria}: {datos['puntaje']}/{datos['maximo']} pts — "
                "NO VERIFICABLE externamente, puntos completos otorgados por defecto."
            )
            continue
        for d in datos["descuentos"]:
            pagina_txt = f"pág. {d['pagina']}" if d["pagina"] is not None else "página no determinada"
            lineas.append(
                f"{categoria}: -{d['puntos']} pts ({d['criticidad']}) — "
                f"{d['descripcion']} [{pagina_txt}]"
            )
    return lineas
