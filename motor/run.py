"""El motor: atiende la cola con el codigo original de TradingAgents, sin modificarlo.

    python -m motor.run            vacia los trabajos de este ambiente y termina
    python -m motor.run --diario   antes, encola la watchlist del dia

Uno a la vez y sin reintentos: cada renovacion de la sesion de ChatGPT invalida la
anterior. ~/.tradingagents se copia del bucket al disco del contenedor y se devuelve; la
sesion ademas se sube en cuanto cambia, para que una caida a mitad de un analisis no deje
en el bucket un token ya usado.
"""
import hashlib
import logging
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from motor import almacen as al

log = logging.getLogger("motor")
LATIDO_S = 30
ETAPAS = (("market_report", "Analista técnico"), ("sentiment_report", "Analista de sentimiento"),
          ("news_report", "Analista de noticias"), ("fundamentals_report", "Analista fundamental"),
          ("investment_plan", "Debate e investigación"), ("trader_investment_plan", "Trader"),
          ("final_trade_decision", "Riesgo y gerente de cartera"))
FALLO_DATOS = ("DATA_UNAVAILABLE:", "NO_DATA_AVAILABLE:")
LIMITE = "Pausado por límite del plan de ChatGPT. Pulsa Reactivar cuando se renueve."
SESION_NOMBRE = "chatgpt-auth.json"


def _simulado() -> bool:
    if not os.getenv("AGENTES_SIMULADO"):
        return False
    if os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_JOB") or os.getenv("AGENTES_BUCKET"):
        raise RuntimeError("El modo simulado no puede correr en la nube.")
    return True


def _es_limite(exc_o_texto) -> bool:
    texto = str(exc_o_texto).lower()
    return (getattr(exc_o_texto, "status_code", None) == 429 or type(exc_o_texto).__name__ == "RateLimitError"
            or any(p in texto for p in ("rate limit", "rate_limit", "usage limit", "usage_limit", "error code: 429")))


class Carpeta:
    """~/.tradingagents en el disco del contenedor, nunca montado: usa flock y renames."""

    def __init__(self, tienda, casa: Path):
        self.tienda, self.casa = tienda, casa
        self.base = f"{al.prefijo()}private/tradingagents/"
        self.vistos = {}

    def _remota(self, relativa: str) -> str:
        # La sesion es una sola: tambien la vista previa usa la de produccion.
        return al.SESION if relativa == SESION_NOMBRE else self.base + relativa

    def traer(self):
        claves = {c[len(self.base):]: c for c in self.tienda.listar(self.base)}
        claves[SESION_NOMBRE] = al.SESION
        for relativa, clave in claves.items():
            hallado = self.tienda.leer(clave)
            if hallado:
                ruta = self.casa / relativa
                ruta.parent.mkdir(parents=True, exist_ok=True)
                ruta.touch(mode=0o600)
                ruta.write_bytes(hallado[0])
                self.vistos[relativa] = hashlib.sha256(hallado[0]).digest()

    def _subir(self, relativa):
        ruta = self.casa / relativa
        try:
            datos = ruta.read_bytes()
        except FileNotFoundError:
            return
        huella = hashlib.sha256(datos).digest()
        if self.vistos.get(relativa) != huella:
            self.tienda.escribir(self._remota(relativa), datos)
            self.vistos[relativa] = huella

    def subir_sesion(self):
        self._subir(SESION_NOMBRE)

    def devolver(self):
        self.subir_sesion()
        for ruta in self.casa.rglob("*"):
            relativa = ruta.relative_to(self.casa).as_posix()
            if ruta.is_file() and not ruta.name.endswith((".lock", ".tmp")) and relativa != SESION_NOMBRE:
                self._subir(relativa)


def _latido(tienda, dueno, carpeta, parar):
    while not parar.wait(LATIDO_S):
        try:
            if not al.tomar_candado(tienda, dueno):
                log.critical("Otro motor tomo el candado; salgo para no invalidar la sesion.")
                os._exit(3)
            carpeta.subir_sesion()
        except Exception:
            log.exception("Latido fallido; se reintenta")


def _config():
    from tradingagents.default_config import build_default_config
    config = build_default_config()
    config["llm_provider"] = os.getenv("TRADINGAGENTS_LLM_PROVIDER", "chatgpt")
    return config


def _analizar(tienda, t, carpeta, simulado):
    from motor import arbitro
    from tradingagents.agents.rating import run_rating
    from tradingagents.dataflows.symbols import crypto_base
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.memory import TradingMemoryLog
    from tradingagents.portfolio import PortfolioContext
    id_, ticker, fecha = t["id"], t["ticker"], t["fecha"]
    config = _config()
    config.update(max_debate_rounds=t["rondas"], max_risk_discuss_rounds=t["rondas"])
    cartera = None
    if t.get("usar_cartera"):
        datos = al.leer_json(tienda, f"{al.prefijo(t['ambiente'])}config/cartera.json")
        cartera = PortfolioContext.model_validate(datos) if datos else None
    vivo = fecha == al.hoy() and not simulado
    medicion = arbitro.historial(TradingMemoryLog(config).load_entries())
    if not vivo:
        # Una fecha pasada no entra al historial: las noticias de ese dia no estan archivadas,
        # asi que no mediria lo mismo que una decision en vivo.
        config["memory_log_path"] = str(carpeta.casa / "exploracion" / f"{id_}.md")

    grafo = TradingAgentsGraph(t["analistas"], config=config)
    inicio = grafo.create_run_state(ticker, fecha, "crypto" if crypto_base(ticker) else "stock", cartera)
    final, fallos, vistos, hechas = {}, 0, set(), []
    for mensajes, estado in grafo.stream_run(inicio, **grafo.propagator.get_graph_args()):
        for m in mensajes:
            clave = getattr(m, "id", None) or id(m)
            if clave not in vistos:
                vistos.add(clave)
                if getattr(m, "type", "") == "tool" and str(m.content).startswith(FALLO_DATOS):
                    fallos += 1
        final.update(estado or {})
        ahora_hechas = [n for k, n in ETAPAS if final.get(k)]
        if ahora_hechas != hechas:
            hechas = ahora_hechas
            total = len(ETAPAS) - (4 - len(t["analistas"]))
            al.actualizar(tienda, id_, etapa=f"Terminó: {hechas[-1]}", avance=min(95, round(100 * len(hechas) / total)))
    if not final.get("final_trade_decision"):
        raise RuntimeError("TradingAgents terminó sin decisión final.")
    grafo.record_decision(ticker, fecha, final)
    rating = run_rating(final)
    al.actualizar(tienda, id_, etapa="Árbitro: revisando la decisión", avance=96)
    juicio = arbitro.arbitrar(final, ticker, fecha, rating, t["analistas"], grafo.quick_thinking_llm,
                              grafo.deep_thinking_llm, medicion, fallos, vivo)
    ruta = Path(grafo.save_reports(final, ticker, Path(config["results_dir"]) / "reports" / id_))
    tienda.escribir(f"{al.prefijo(t['ambiente'])}informes/{id_}.html", ruta.with_name("complete_report.html").read_bytes())
    al.actualizar(tienda, id_, estado="terminado", etapa="Terminado", avance=100, terminado=al.ahora(), rating=rating,
                  veredicto=juicio["veredicto"], arbitro=juicio, decision=final["final_trade_decision"],
                  simulado=simulado, vivo=vivo, con_cartera=bool(cartera), version=grafo.run_settings()["version"])


def _backtest(tienda, t):
    from tradingagents.backtest import iter_grid, run_backtest, summarize

    def avance(hecho, total, ticker, fecha):
        al.actualizar(tienda, t["id"], etapa=f"Análisis {hecho} de {total}: {ticker} {fecha}",
                      avance=round(100 * (hecho - 1) / total))
    fechas = iter_grid(t["desde"], t["hasta"], t["cada"])
    r = run_backtest(t["tickers"], fechas, _config(), selected_analysts=t["analistas"], run_id=t["id"], progress=avance)
    if not r.cells_run and r.failures:
        if _es_limite(r.failures[0][2]):
            raise RuntimeError("rate limit: " + r.failures[0][2][:200])
        raise RuntimeError(f"Fallaron los {len(r.failures)} análisis: {r.failures[0][2][:200]}")
    al.actualizar(tienda, t["id"], estado="terminado", etapa="Terminado", avance=100, terminado=al.ahora(),
                  resumen=summarize(r).render() if r.log_path.is_file() else "Sin resultados.",
                  fallos=len(r.failures), simulado=bool(os.getenv("AGENTES_SIMULADO")))


def _pausar_todo(tienda, primero):
    for t in [primero, *al.en_cola(tienda, al.AMBIENTE)]:
        al.sacar_de_cola(tienda, t["id"])
        al.actualizar(tienda, t["id"], estado="pausado", etapa=LIMITE)


def _atender(tienda, t, carpeta, simulado) -> bool:
    """Devuelve False si hay que dejar de procesar (limite del plan o sesion caida)."""
    from tradingagents.llm_clients.chatgpt_auth import ChatGPTAuthError, ChatGPTUnreachable
    al.sacar_de_cola(tienda, t["id"])
    al.actualizar(tienda, t["id"], estado="corriendo", etapa="Preparando", iniciado=al.ahora())
    try:
        _backtest(tienda, t) if t["tipo"] == "backtest" else _analizar(tienda, t, carpeta, simulado)
        al.guardar_json(tienda, "estado/sesion.json", {"ok": True, "cuando": al.ahora()})
        return True
    except ChatGPTUnreachable:
        al.actualizar(tienda, t["id"], estado="fallido", etapa="OpenAI no respondió", error="Tu sesión sigue bien. Lánzalo de nuevo más tarde.")
        return False
    except ChatGPTAuthError:
        al.guardar_json(tienda, "estado/sesion.json", {"ok": False, "cuando": al.ahora()})
        al.actualizar(tienda, t["id"], estado="fallido", etapa="Sesión de ChatGPT no válida", error="Hay que volver a iniciar sesión. Mira README-AGENTES.md.")
        return False
    except Exception as exc:
        if _es_limite(exc):
            _pausar_todo(tienda, t)
            return False
        log.exception("Trabajo %s fallido", t["id"])
        al.actualizar(tienda, t["id"], estado="fallido", etapa="Falló", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        return True


def _encolar_diario(tienda):
    from motor import arbitro
    fecha = al.hoy()
    hallada = tienda.leer(f"{al.prefijo()}config/watchlist.txt")
    tickers = hallada[0].decode().split() if hallada else []
    try:
        if arbitro.precio_referencia("SPY", fecha).get("fecha") != fecha:
            log.info("Hoy no abrió la NYSE; no se encola nada.")
            return
    except Exception:
        log.warning("No se pudo comprobar si hubo mercado; se sigue.")
    for ticker in tickers:
        limpio = "".join(c if c.isalnum() else "-" for c in ticker.lower())
        try:
            al.encolar(tienda, {"tipo": "analisis", "ticker": ticker, "fecha": fecha, "rondas": 1, "usar_cartera": True,
                                "analistas": os.getenv("AGENTES_ANALISTAS", "market,news,fundamentals").split(","), "origen": "diario"},
                       f"diario-{al.AMBIENTE}-{fecha}-{limpio}")
        except al.Conflicto:
            pass


def _una_vuelta(tienda, dueno, simulado) -> bool:
    """Atiende la cola bajo el candado. Devuelve False si otro motor lo tiene."""
    if not al.tomar_candado(tienda, dueno):
        return False
    parar = threading.Event()
    casa = Path(os.environ["HOME"]) / ".tradingagents"
    carpeta = Carpeta(tienda, casa)
    try:
        carpeta.traer()
        threading.Thread(target=_latido, args=(tienda, dueno, carpeta, parar), daemon=True).start()
        for t in al.trabajos(tienda):
            if t["estado"] == "corriendo":   # tenemos el candado: su motor ya no existe
                al.actualizar(tienda, t["id"], estado="fallido", etapa="Interrumpido",
                              error="El motor se detuvo a mitad del trabajo. No se reintenta solo: lánzalo de nuevo.")
        while pendientes := al.en_cola(tienda, al.AMBIENTE):
            sigue = _atender(tienda, pendientes[0], carpeta, simulado)
            carpeta.devolver()
            if not sigue:
                break
    finally:
        parar.set()
        try:
            carpeta.devolver()   # tambien si hubo error
        finally:
            al.soltar_candado(tienda, dueno)
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    # Antes de importar TradingAgents: su carpeta sale de HOME. Asi nunca toca la del Mac,
    # y al terminar no queda ninguna copia de la sesion en el disco.
    with tempfile.TemporaryDirectory() as casa:
        os.environ["HOME"] = casa
        simulado = _simulado()
        if simulado:
            from motor import simulado as sim
            sim.instalar()
        tienda = al.abrir()
        if "--diario" in sys.argv:
            _encolar_diario(tienda)
        dueno = f"{al.AMBIENTE}-{uuid.uuid4().hex[:8]}"
        # Se vuelve a mirar despues de soltar el candado: un trabajo que llego justo entre la
        # ultima revision y la salida no debe quedar varado. Lo pausado ya no esta en cola.
        while al.en_cola(tienda, al.AMBIENTE) or "--diario" in sys.argv:
            sys.argv = [a for a in sys.argv if a != "--diario"]
            if not _una_vuelta(tienda, dueno, simulado):
                break   # otro motor vaciara la cola
            estado = al.leer_json(tienda, "estado/sesion.json", {})
            if not estado.get("ok", True):
                break
        for otro in {t["ambiente"] for t in al.en_cola(tienda)} - {al.AMBIENTE}:
            al.lanzar_motor(otro)


if __name__ == "__main__":
    main()
