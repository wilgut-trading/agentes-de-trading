"""Los dos tipos de analisis: inversion y swing.

TradingAgents no tiene idea de horizonte, asi que el mandato viaja en el contexto del
instrumento, el texto comun que leen todos sus agentes. No hace falta tocar su codigo.
Cada objetivo tiene su propia memoria: mezclar decisiones medidas a plazos distintos
falsearia el historial.
"""
import math
from unittest import mock

from motor import almacen as al

OBJETIVOS = {"swing": "Swing trading", "inversion": "Inversión"}
# Se configuran en Ajustes. Sesiones = sesiones de mercado en las que se mide cada decision.
AJUSTES = {"diario": "swing", "sesiones_swing": 10, "sesiones_inversion": 63, "rr_minimo": 2.5}
MAX_SWING = 20
CLAVE = "config/ajustes.json"
_MEMORIA = {"swing": "memory/trading_memory_swing.md", "inversion": "memory/trading_memory.md"}


def validar(datos: dict) -> dict:
    """Solo valores con sentido; lanza ValueError si alguno no lo tiene."""
    v = {**AJUSTES, **{k: datos[k] for k in AJUSTES if k in datos}}
    try:
        v["sesiones_swing"], v["sesiones_inversion"], v["rr_minimo"] = int(v["sesiones_swing"]), int(v["sesiones_inversion"]), float(v["rr_minimo"])
    except (ValueError, TypeError):
        raise ValueError("Escribe solo números.") from None
    if v["diario"] not in OBJETIVOS:
        raise ValueError("Objetivo diario no válido.")
    if not 1 <= v["sesiones_swing"] <= MAX_SWING:
        raise ValueError(f"En swing se mide entre 1 y {MAX_SWING} sesiones.")
    if not MAX_SWING < v["sesiones_inversion"] <= 756:
        raise ValueError(f"En inversión se mide entre {MAX_SWING + 1} y 756 sesiones.")
    if not (1 <= v["rr_minimo"] <= 20 and math.isfinite(v["rr_minimo"])):
        raise ValueError("La relación riesgo/beneficio mínima va de 1 a 20.")
    return v


def ajustes(tienda, ambiente=None) -> dict:
    try:
        return validar(al.leer_json(tienda, f"{al.prefijo(ambiente)}{CLAVE}", {}))
    except (ValueError, TypeError, KeyError):   # un archivo danado no debe tumbar el motor
        return dict(AJUSTES)


def sesiones(valores: dict, objetivo: str) -> int:
    return valores[f"sesiones_{objetivo}"]


def ruta_memoria(objetivo: str) -> str:
    return _MEMORIA[objetivo]


def mandato(objetivo: str, rr_minimo: float) -> str:
    if objetivo == "swing":
        return (f"\n\nMANDATE: this is a SWING TRADE analysis over 1 to {MAX_SWING} trading sessions. Judge only what can move the "
                "price inside that window: trend, momentum, key levels, volatility (ATR), volume, relative strength, and catalysts "
                "or earnings dates inside it. Use long-term valuation or balance sheet facts only if they can act as a catalyst "
                "inside the window. Never suggest open-ended holding or periodic reviews. The Trader must give Entry Price and Stop "
                "Loss as plain numbers. The Portfolio Manager must give Price Target as a plain number, Time Horizon written exactly like "
                f"`10 sessions` (never above {MAX_SWING}), and end the thesis with exactly these three lines, in this order, with nothing after them and no quote marks: "
                "Entry: <price>\nStop: <price>\nInvalidation: close <below or above> <price>. Write them also when the rating is Hold and the trade is only "
                f"conditional. The reward/risk from entry to target versus entry to stop must be at least {rr_minimo:g}. If no setup "
                "reaches it or the levels cannot be stated, rate Hold, say so, and write no Entry or Stop line and no Price Target.")
    return ("\n\nMANDATE: this is an INVESTMENT analysis over months to years, at least three months. Weigh valuation, earnings "
            "quality, cash flow, competitive position, capital allocation and balance sheet. Use short-term technicals only for "
            "timing. The Portfolio Manager must give Price Target as an absolute price and Time Horizon in months, and in the "
            "thesis a line `Invalidation: <condition>`.")


def con_mandato(objetivo: str, rr_minimo: float):
    """Cubre tambien run_backtest, que construye su propio grafo: todo pasa por aqui."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    original = TradingAgentsGraph.resolve_instrument_context

    def con_texto(grafo, *a, **k):
        return original(grafo, *a, **k) + mandato(objetivo, rr_minimo)
    return mock.patch.object(TradingAgentsGraph, "resolve_instrument_context", con_texto)
