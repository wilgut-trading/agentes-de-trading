"""Lo que apunta la evidencia, en probabilidades de Jev. Solo con TYPESAFE_API_KEY.

Jev lee los informes de los analistas y nada mas: ni la decision, ni los niveles, ni el arbitro. Asi no
puede dar la razon a una conclusion que no conoce. Sus probabilidades son de lo que dicen los informes,
no de que la operacion gane; por eso se guardan para medirlas y no cambian el veredicto.
"""
import logging
import math
import os
from unittest import mock

from motor.arbitro import INFORMES
from tradingagents.agents import post_screen

logger = logging.getLogger(__name__)

NOMBRES = {"market": "Técnico", "social": "Sentimiento", "news": "Noticias", "fundamentals": "Fundamentales"}
OPCIONES = {"rise": "sube", "fall": "baja", "unclear": "sin_direccion"}
ORDENES = {"a": ("rise", "fall"), "b": ("fall", "rise")}   # Jev puede preferir la primera opcion: se pregunta en ambos ordenes
ESPERA = 60   # segundos; la del framework es para publicaciones cortas, no para informes


def _pregunta(campo: str, sesiones: int, orden: tuple) -> dict:
    criterios = {
        "rise": f"The evidence in `{campo}` favors a higher price at the end of those sessions.",
        "fall": f"The evidence in `{campo}` favors a lower price at the end of those sessions.",
        "unclear": f"The evidence in `{campo}` is balanced, or does not bear on the direction of the price.",
    }
    return {
        "type": "choice",
        "instructions": f"Which way does the evidence in `{campo}` point for the stock price of `instrument` over the next {sesiones} trading sessions?",
        "criteria": {k: criterios[k] for k in (*orden, "unclear")},
    }


def _probabilidades(respuesta: dict) -> dict:
    p = {v: respuesta["probabilities"][k] for k, v in OPCIONES.items()}
    if not all(type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1 for x in p.values()) or abs(sum(p.values()) - 1) > 0.02:
        raise ValueError("probabilidades no válidas")
    return p


def _fila(nombre: str, clave: str, respuestas: dict) -> dict:
    uno, otro = (_probabilidades(respuestas[f"{clave}_{o}"]) for o in ORDENES)
    return {"nombre": nombre, **{v: (uno[v] + otro[v]) / 2 for v in OPCIONES.values()}}


def de_informes(estado, ticker: str, lado: int | None, sesiones: int) -> dict | None:
    """None sin clave. Un fallo se devuelve como tal: nunca debe leerse como una respuesta."""
    if not os.environ.get("TYPESAFE_API_KEY"):
        return None
    informes = {k: estado[c] for k, c in INFORMES.items() if str(estado.get(c) or "").strip()}
    if not informes:
        return {"error": "no había informes que leer"}
    campos = {"conjunto": "reports", **{k: f"reports.{k}" for k in informes}}
    preguntas = {f"{k}_{o}": _pregunta(c, sesiones, orden) for k, c in campos.items() for o, orden in ORDENES.items()}
    try:
        with mock.patch.object(post_screen, "_TIMEOUT", ESPERA):
            r = post_screen.system_one({"instrument": ticker, "reports": informes}, preguntas)
        filas = [_fila("Todos los informes", "conjunto", r)] + [_fila(NOMBRES[k], k, r) for k in informes]
    except Exception as e:   # despues de un analisis largo, un fallo aqui no debe costarlo
        logger.warning("Jev no respondio: %s", type(e).__name__)
        return {"error": str(e)[:80] if type(e).__name__ == "TypeSafeError" else "respuesta no válida"}
    total = filas[0]
    a_favor, en_contra = (total["sube"], total["baja"]) if lado == 1 else (total["baja"], total["sube"]) if lado == -1 else (None, None)
    return {"lado": lado, "sesiones": sesiones, "filas": filas, "a_favor": a_favor, "en_contra": en_contra}
