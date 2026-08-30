"""
Módulo: nom138_referencias.py
Lista blanca de citas verificadas de la NOM-138-SEMARNAT/SSA1-2012 (Fase 2).

Ningún hallazgo del auditor puede citar un apartado o tabla de la norma que
no esté en REFERENCIAS_VERIFICADAS. Si un módulo de auditoría propone una
clave que no coincide con ninguna entrada de esta lista, resolver_cita()
devuelve la cita genérica sin número — el sistema PREFIERE una cita menos
específica que una incorrecta ante la autoridad. Esta regla se aplica en
código, no en el prompt: no depende de que Claude "se acuerde" de no inventar.

Para agregar una referencia nueva: verifícala contra el texto oficial de la
NOM-138-SEMARNAT/SSA1-2012 (Diario Oficial de la Federación) y agrégala aquí
con su cita exacta. No agregar nada que no se haya confirmado contra la
fuente oficial.

No depende de app.py — se importa como módulo.
"""

from __future__ import annotations

CITA_GENERICA = "NOM-138-SEMARNAT/SSA1-2012"

# ---------------------------------------------------------------------------
# Referencias verificadas — cada clave debe estar confirmada contra el texto
# oficial de la norma antes de agregarse.
# ---------------------------------------------------------------------------
REFERENCIAS_VERIFICADAS: dict[str, str] = {
    "limites_maximos_permisibles": (
        f"{CITA_GENERICA}, Tabla 1 "
        "(límites máximos permisibles por uso de suelo)"
    ),
    "criterio_uso_mixto": (
        f"{CITA_GENERICA}, Apartado 6.1.3 "
        "(criterio más restrictivo aplicable en uso de suelo mixto)"
    ),
    "metodologia_muestreo": (
        f"{CITA_GENERICA}, Apartado 7 "
        "(metodología de muestreo)"
    ),
    "evaluacion_riesgos": (
        f"{CITA_GENERICA}, Apartado 8 "
        "(evaluación de riesgos)"
    ),
}


def resolver_cita(clave: str | None) -> str:
    """
    Devuelve la cita verificada correspondiente a `clave`.

    Si `clave` es None, vacía, o no coincide con ninguna entrada de
    REFERENCIAS_VERIFICADAS, devuelve la cita genérica de la norma sin
    número de apartado. NUNCA se construye ni se adivina un número de
    apartado que no esté explícitamente en la lista blanca.
    """
    if clave and clave in REFERENCIAS_VERIFICADAS:
        return REFERENCIAS_VERIFICADAS[clave]
    return CITA_GENERICA


def claves_disponibles() -> list[str]:
    """Claves de referencia que los módulos de auditoría pueden usar al citar
    un hallazgo regulatorio. Cualquier otra clave se resuelve como genérica."""
    return list(REFERENCIAS_VERIFICADAS.keys())
