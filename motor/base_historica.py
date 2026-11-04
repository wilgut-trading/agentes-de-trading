"""Que tan seguido, en el pasado de esta accion, un plan asi toco primero el objetivo o el stop.

Es la base sin senal: mide cuanto tendria que aportar el analisis, no la probabilidad de esta
operacion. Es codigo sobre precios, sin modelo. Las distancias se ajustan a la volatilidad de
cada momento. Si un dia toca los dos niveles cuenta como stop, y un salto de apertura se paga
entero, porque un stop no protege de un hueco; un salto a favor no paga mas que el objetivo.
"""
from datetime import date, timedelta

from motor.arbitro import wilson
from tradingagents.dataflows.symbols import normalize_symbol

ANOS, VENTANA_VOLATILIDAD, MIN_VENTANAS = 5, 14, 250


def _tabla(ticker: str, fecha: str):
    import yfinance as yf
    fin = date.fromisoformat(fecha) + timedelta(days=1)
    return yf.Ticker(normalize_symbol(ticker)).history(start=(fin - timedelta(days=ANOS * 366)).isoformat(), end=fin.isoformat(), auto_adjust=True)


def calcular(tabla, entrada: float, stop: float, objetivo: float, sesiones: int, sin_cierre=False) -> dict | None:
    """`tabla` solo trae datos hasta la fecha del analisis. La barra de hoy, sin cerrar, no cuenta."""
    if not (entrada > 0 and stop > 0 and objetivo > 0 and sesiones >= 1) or (objetivo - entrada) * (entrada - stop) <= 0:
        return None
    tabla = tabla.dropna(subset=["Open", "High", "Low", "Close"])
    if sin_cierre:
        tabla = tabla.iloc[:-1]
    o, h, l, c = (tabla[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    if len(c) < MIN_VENTANAS + sesiones + VENTANA_VOLATILIDAD or (c <= 0).any():
        return None
    rangos = (tabla["High"] - tabla["Low"]) / tabla["Close"]
    volatilidad = rangos.rolling(VENTANA_VOLATILIDAD).mean().to_numpy()
    if not volatilidad[-1] > 0:
        return None
    lado = 1 if objetivo > entrada else -1
    gana, pierde = abs(objetivo / entrada - 1), abs(stop / entrada - 1)
    relacion = gana / pierde
    objetivos = stops = 0
    resultados = []
    for i in range(VENTANA_VOLATILIDAD - 1, len(c) - sesiones):
        k = volatilidad[i] / volatilidad[-1]
        if not k > 0:
            continue
        riesgo = pierde * k
        nivel_objetivo, nivel_stop = c[i] * (1 + lado * gana * k), c[i] * (1 - lado * riesgo)
        r = None
        for j in range(i + 1, i + sesiones + 1):
            if (l[j] <= nivel_stop) if lado > 0 else (h[j] >= nivel_stop):
                salida = min(o[j], nivel_stop) if lado > 0 else max(o[j], nivel_stop)
                r = lado * (salida / c[i] - 1) / riesgo
                stops += 1
                break
            if (h[j] >= nivel_objetivo) if lado > 0 else (l[j] <= nivel_objetivo):
                r = relacion
                objetivos += 1
                break
        resultados.append(lado * (c[i + sesiones] / c[i] - 1) / riesgo if r is None else r)
    ventanas = len(resultados)
    if ventanas < MIN_VENTANAS:
        return None
    # Las ventanas se solapan: la incertidumbre se mide con las que serian independientes.
    independientes = ventanas // sesiones
    bajo, alto = wilson(round(objetivos / ventanas * independientes), independientes)
    return {"ventanas": ventanas, "independientes": independientes, "sesiones": sesiones, "lado": lado,
            "objetivo": objetivos / ventanas, "stop": stops / ventanas, "ninguno": 1 - (objetivos + stops) / ventanas,
            "bajo": bajo, "alto": alto, "relacion": round(relacion, 2), "minimo": 1 / (1 + relacion),
            "resueltas": objetivos / (objetivos + stops) if objetivos + stops else None, "resultado_medio": float(sum(resultados) / ventanas)}


def de_plan(plan: dict, ticker: str, fecha: str, sesiones: int, sin_cierre=False, tabla=None) -> dict | None:
    """Sin plan completo no hay nada que medir. Un fallo aqui no debe costar el analisis."""
    if plan.get("direccion") is None or plan.get("stop") is None or plan.get("objetivo") is None or plan.get("base") is None:
        return None
    try:
        return calcular(_tabla(ticker, fecha) if tabla is None else tabla, plan["base"], plan["stop"], plan["objetivo"], sesiones, sin_cierre)
    except Exception:
        return None
