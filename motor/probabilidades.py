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
from tradingagents.agents.context import resolve_instrument_identity

logger = logging.getLogger(__name__)

NOMBRES = {"market": "Técnico", "social": "Sentimiento", "news": "Noticias", "fundamentals": "Fundamentales"}
ORDENES = {"a": ("rise", "fall"), "b": ("fall", "rise")}   # Jev puede preferir la primera opcion: se pregunta en ambos ordenes
ESPERA = 60   # segundos; la del framework es para publicaciones cortas, no para informes
# Por debajo, los informes casi no hablan de direccion y no se resume ningun lado.
MIN_EVIDENCIA = 0.3


def _direccion(campo: str, sesiones: int, orden: tuple) -> dict:
    criterios = {
        "rise": f"`{campo}` gives more weight to reasons for the price to be higher at the end of those sessions.",
        "fall": f"`{campo}` gives more weight to reasons for the price to be lower at the end of those sessions.",
    }
    return {
        "type": "choice",
        "instructions": f"Which way does `{campo}` point for the price of `instrument` over the next {sesiones} trading sessions?",
        "criteria": {k: criterios[k] for k in orden},
    }


def _hay_evidencia(campo: str, sesiones: int) -> dict:
    return {
        "type": "noul",
        "instructions": f"Does `{campo}` give specific reasons for the price of `instrument` to move over the next {sesiones} trading sessions?",
        "criteria": {
            "true": f"`{campo}` gives specific reasons for the price to rise or to fall in those sessions.",
            "false": f"`{campo}` only describes the company or says that no data was found, and gives no reason for a price move.",
        },
    }


def _preguntas(informes: dict, sesiones: int) -> dict:
    campos = {"conjunto": "reports", **{k: f"reports.{k}" for k in informes}}
    preguntas = {f"{k}_{o}": _direccion(c, sesiones, orden) for k, c in campos.items() for o, orden in ORDENES.items()}
    preguntas.update({f"{k}_evidencia": _hay_evidencia(c, sesiones) for k, c in campos.items()})
    return preguntas


def _estado(informes: dict, ticker: str, fecha: str, sin_cierre: bool) -> dict:
    empresa = resolve_instrument_identity(ticker).get("company_name")
    estado = {"instrument": {"ticker": ticker, **({"company": empresa} if empresa else {})},
              "analysis_date": fecha, "reports": informes}
    if sin_cierre:
        estado["price_data_note"] = "The market was still open: the latest daily row behind `reports` is an unfinished session, not a final close."
    return estado


def _probabilidad(x) -> float:
    if type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1:
        raise ValueError("probabilidad no válida")
    return x


def _sube_baja(respuesta: dict) -> tuple:
    sube, baja = (_probabilidad(respuesta["probabilities"][k]) for k in ("rise", "fall"))
    if abs(sube + baja - 1) > 0.02:
        raise ValueError("probabilidades no válidas")
    return sube, baja


def _fila(nombre: str, clave: str, respuestas: dict) -> dict:
    uno, otro = (_sube_baja(respuestas[f"{clave}_{o}"]) for o in ORDENES)
    sube, baja = uno[0] + otro[0], uno[1] + otro[1]
    evidencia = _probabilidad(respuestas[f"{clave}_evidencia"]["noul"])
    return {"nombre": nombre, "sube": sube / (sube + baja), "baja": baja / (sube + baja), "evidencia": evidencia,
            "con_evidencia": evidencia >= MIN_EVIDENCIA}


def de_informes(estado, ticker: str, fecha: str, lado: int | None, sesiones: int, sin_cierre: bool = False) -> dict | None:
    """None sin clave. Un fallo se devuelve como tal: nunca debe leerse como una respuesta.
    `auditoria` lleva lo enviado y lo recibido, para repetir o revisar una respuesta."""
    if not os.environ.get("TYPESAFE_API_KEY"):
        return None
    informes = {k: estado[c] for k, c in INFORMES.items() if str(estado.get(c) or "").strip()}
    if not informes:
        return {"error": "no había informes que leer"}
    modelo = os.environ.get("TYPESAFE_DEFAULT_MODEL") or post_screen._DEFAULT_MODEL
    auditoria = {"modelo": modelo, "peticion": None, "respuestas": None}
    try:
        auditoria["peticion"] = {"state": _estado(informes, ticker, fecha, sin_cierre), "questions": _preguntas(informes, sesiones)}
        with mock.patch.object(post_screen, "_TIMEOUT", ESPERA):
            r = auditoria["respuestas"] = post_screen.system_one(auditoria["peticion"]["state"], auditoria["peticion"]["questions"])
        filas = [_fila("Todos los informes", "conjunto", r)] + [_fila(NOMBRES[k], k, r) for k in informes]
    except Exception as e:   # despues de un analisis largo, un fallo aqui no debe costarlo
        logger.warning("Jev no respondio: %s", type(e).__name__)
        return {"error": str(e)[:80] if type(e).__name__ == "TypeSafeError" else "respuesta no válida", "auditoria": auditoria}
    total = filas[0]
    con_evidencia = total["con_evidencia"]
    a_favor, en_contra = (None, None)
    if con_evidencia and lado in (1, -1):
        a_favor, en_contra = (total["sube"], total["baja"]) if lado == 1 else (total["baja"], total["sube"])
    return {"lado": lado, "sesiones": sesiones, "modelo": modelo, "lecturas": filas, "con_evidencia": con_evidencia,
            "a_favor": a_favor, "en_contra": en_contra, "auditoria": auditoria}
