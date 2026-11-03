"""El motor: atiende la cola con el codigo original de TradingAgents, sin modificarlo.

    python -m motor.run            vacia los trabajos de este ambiente y termina
    python -m motor.run --diario   antes, encola la watchlist del dia

Uno a la vez y sin reintentos: cada renovacion de la sesion de ChatGPT invalida la
anterior. ~/.tradingagents se copia del bucket al disco del contenedor y se devuelve; la
sesion ademas se sube en cuanto cambia, para que una caida a mitad de un analisis no deje
en el bucket un token ya usado.
"""
import contextlib
import hashlib
import logging
import os
import re
import signal
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest import mock

from motor import almacen as al

log = logging.getLogger("motor")
LATIDO_S = 30
ETAPAS = (("market_report", "Analista técnico"), ("sentiment_report", "Analista de sentimiento"),
          ("news_report", "Analista de noticias"), ("fundamentals_report", "Analista fundamental"),
          ("investment_plan", "Debate e investigación"), ("trader_investment_plan", "Trader"),
          ("final_trade_decision", "Riesgo y gerente de cartera"))
FALLO_DATOS = ("DATA_UNAVAILABLE:", "NO_DATA_AVAILABLE:", "news unavailable")
PARAR = "parar"
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


def _limpio(exc) -> str:
    """El texto de un error se guarda y se muestra: sin nada con forma de credencial."""
    return re.sub(r"(sk-|eyJ|Bearer\s+|rt_)[\w.\-]+", "[oculto]", f"{type(exc).__name__}: {str(exc)[:300]}")


class Carpeta:
    """~/.tradingagents en el disco del contenedor, nunca montado: usa flock y renames."""

    def __init__(self, tienda, casa: Path):
        self.tienda, self.casa = tienda, casa
        self.base = f"{al.prefijo()}private/tradingagents/"
        self.vistos = {}
        self.lista = False   # antes de traerla, una sesion a medio escribir no se sube
        self.turno = threading.RLock()   # el latido y el final no deben pisarse con un token viejo

    def _remota(self, relativa: str) -> str:
        # La sesion es una sola: tambien la vista previa usa la de produccion.
        return al.SESION if relativa == SESION_NOMBRE else self.base + relativa

    def traer(self):
        # logs/ solo crece y nada lo lee de vuelta, salvo un backtest que se reanuda.
        claves = {c[len(self.base):]: c for c in self.tienda.listar(self.base)
                  if not c[len(self.base):].startswith("logs/") or c[len(self.base):].startswith("logs/backtest/")}
        claves[SESION_NOMBRE] = al.SESION
        for relativa, clave in claves.items():
            hallado = self.tienda.leer(clave)
            if hallado:
                ruta = self.casa / relativa
                ruta.parent.mkdir(parents=True, exist_ok=True)
                ruta.touch(mode=0o600)
                ruta.write_bytes(hallado[0])
                self.vistos[relativa] = hashlib.sha256(hallado[0]).digest()
        self.lista = True

    def _subir(self, relativa):
        with self.turno:
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
        if self.lista:
            self._subir(SESION_NOMBRE)

    def devolver(self):
        """Perder un token ya rotado acaba con la sesion: se insiste y, si no se puede, se avisa."""
        for espera in (1, 2, 4, 8, 16, 0):
            try:
                self.subir_sesion()
                break
            except Exception:
                log.exception("No se pudo guardar la sesion de ChatGPT")
                if not espera:
                    _marcar_sesion(self.tienda, False)
                time.sleep(espera)
        for ruta in self.casa.rglob("*") if self.lista else ():
            relativa = ruta.relative_to(self.casa).as_posix()
            if ruta.is_file() and not ruta.name.endswith((".lock", ".tmp")) and relativa != SESION_NOMBRE \
                    and not relativa.startswith("exploracion/"):
                try:   # un archivo que falla no debe dejar sin guardar los demas
                    self._subir(relativa)
                except Exception:
                    log.exception("No se pudo guardar %s", relativa)


def _marcar_sesion(tienda, ok):
    try:
        al.guardar_json(tienda, "estado/sesion.json", {"ok": ok, "cuando": al.ahora()})
    except Exception:
        log.exception("No se pudo anotar el estado de la sesion")


def _interrumpir(tienda):
    """Lo que estaba corriendo se detuvo. Un backtest se reanuda; un analisis se relanza a mano."""
    for t in al.trabajos(tienda):
        if t["estado"] == "corriendo" and t["tipo"] == "backtest":
            al.actualizar(tienda, t["id"], estado="pausado", etapa="Interrumpido. Pulsa Reactivar para seguir donde quedó.")
        elif t["estado"] == "corriendo":
            al.actualizar(tienda, t["id"], estado="fallido", etapa="Interrumpido",
                          error="El motor se detuvo a mitad del trabajo. No se reintenta solo: lánzalo de nuevo.")


def _latido(tienda, dueno, carpeta, parar):
    vueltas = 0
    while not parar.wait(LATIDO_S / 15):   # la sesion se revisa cada 2 s; el candado, cada 30 s
        vueltas += 1
        try:
            if vueltas % 15 == 0 and not al.tomar_candado(tienda, dueno):
                log.critical("Otro motor tomo el candado; salgo para no invalidar la sesion.")
                try:
                    carpeta.subir_sesion()
                finally:
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
    from datetime import date

    from motor import arbitro
    from tradingagents.agents.analysts import sentiment_analyst as sentimiento
    from tradingagents.agents.rating import run_rating
    from tradingagents.dataflows.symbols import crypto_base, normalize_symbol
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
    # Sabado o domingo no hay sesion que medir, salvo en cripto.
    vivo = fecha == al.hoy() and not simulado and (date.fromisoformat(fecha).weekday() < 5 or bool(crypto_base(ticker)))
    registradas = TradingMemoryLog(config).load_entries()
    medicion = arbitro.historial(registradas)
    previa = next((e for e in registradas if vivo and normalize_symbol(e["ticker"]) == normalize_symbol(ticker) and e["date"] == fecha), None)
    if not vivo:
        # Una fecha pasada no entra al historial: las noticias de ese dia no estan archivadas,
        # asi que no mediria lo mismo que una decision en vivo.
        config["memory_log_path"] = str(carpeta.casa / "exploracion" / f"{id_}.md")

    grafo = TradingAgentsGraph(t["analistas"], config=config)
    inicio = grafo.create_run_state(ticker, fecha, "crypto" if crypto_base(ticker) else "stock", cartera)
    final, fallos, vistos, hechas = {}, 0, set(), []
    # El analista de sentimiento recibe sus fuentes dentro del prompt, no como herramientas.
    construir = sentimiento._build_system_message
    sin_fuentes = []

    def vigilar(*a, **k):
        sin_fuentes.extend(v for n, v in k.items() if n.endswith("_block") and any(f in str(v) for f in (*FALLO_DATOS, "unavailable")))
        return construir(*a, **k)
    with mock.patch.object(sentimiento, "_build_system_message", vigilar):
        for mensajes, estado in grafo.stream_run(inicio, **grafo.propagator.get_graph_args()):
            for m in mensajes:
                clave = getattr(m, "id", None) or id(m)
                if clave not in vistos:
                    vistos.add(clave)
                    if getattr(m, "type", "") == "tool" and any(f in str(m.content) for f in FALLO_DATOS):
                        fallos += 1
            final.update(estado or {})
            ahora_hechas = [n for k, n in ETAPAS if final.get(k)]
            if ahora_hechas != hechas:
                hechas = ahora_hechas
                total = len(ETAPAS) - (4 - len(t["analistas"]))
                al.actualizar(tienda, id_, etapa=f"Terminó: {hechas[-1]}", avance=min(95, round(100 * len(hechas) / total)))
    fallos += len(sin_fuentes)
    if not final.get("final_trade_decision"):
        raise RuntimeError("TradingAgents terminó sin decisión final.")
    grafo.record_decision(ticker, fecha, final)
    rating = run_rating(final)
    al.actualizar(tienda, id_, etapa="Árbitro: revisando la decisión", avance=96)
    extra = []
    if previa and previa["rating"] != rating:
        # Las ejecuciones varian, y el historial solo guarda la primera decision de cada dia.
        extra = [{"control": "Repetición", "resultado": arbitro.RESERVAS,
                  "detalle": f"Hoy ya salió {previa['rating']} para {ticker}. El historial mide esa, no esta: el desacuerdo es una señal de duda."}]
    juicio = arbitro.arbitrar(final, ticker, fecha, rating, t["analistas"], grafo.quick_thinking_llm,
                              grafo.deep_thinking_llm, medicion, fallos, vivo, extra=extra)
    ruta = Path(grafo.save_reports(final, ticker, Path(config["results_dir"]) / "reports" / id_))
    tienda.escribir(f"{al.prefijo(t['ambiente'])}informes/{id_}.html", ruta.with_name("complete_report.html").read_bytes())
    al.actualizar(tienda, id_, estado="terminado", etapa="Terminado", avance=100, terminado=al.ahora(), rating=rating,
                  veredicto=juicio["veredicto"], arbitro=juicio, decision=final["final_trade_decision"],
                  simulado=simulado, vivo=vivo, con_cartera=bool(cartera), simbolo=normalize_symbol(ticker), version=grafo.run_settings()["version"])


def _backtest(tienda, t, carpeta):
    from tradingagents.backtest import iter_grid, run_backtest, summarize
    from tradingagents.llm_clients.chatgpt_auth import ChatGPTAuthError
    celdas = [(ti, f) for ti in t["tickers"] for f in iter_grid(t["desde"], t["hasta"], t["cada"])]
    fallos, r = [], None
    # De una en una: run_backtest se traga los errores de cada celda, y con un limite o una
    # sesion caida seguir con las demas solo gastaria intentos. Reanudar salta lo ya hecho.
    for i, (ticker, fecha) in enumerate(celdas):
        al.actualizar(tienda, t["id"], etapa=f"Análisis {i + 1} de {len(celdas)}: {ticker} {fecha}", avance=round(100 * i / len(celdas)))
        r = run_backtest([ticker], [fecha], _config(), selected_analysts=t["analistas"], run_id=t["id"])
        for _, _, error in r.failures:
            if _es_limite(error):
                raise RuntimeError("rate limit: " + error[:200])
            if "tradingagents login chatgpt" in error:
                raise ChatGPTAuthError(error[:200])
        fallos += r.failures
        carpeta.devolver()   # si el motor cae, lo ya analizado no se repite
    if len(fallos) == len(celdas):
        raise RuntimeError(f"Fallaron los {len(fallos)} análisis: {fallos[0][2][:200]}")
    al.actualizar(tienda, t["id"], estado="terminado", etapa="Terminado", avance=100, terminado=al.ahora(),
                  resumen=summarize(r).render() if r.log_path.is_file() else "Sin resultados.",
                  fallos=len(fallos), simulado=bool(os.getenv("AGENTES_SIMULADO")))


def _pausar_todo(tienda, primero):
    for t in [primero, *al.en_cola(tienda, al.AMBIENTE)]:
        al.actualizar(tienda, t["id"], estado="pausado", etapa=LIMITE)
        al.sacar_de_cola(tienda, t["id"])


def _atender(tienda, t, carpeta, simulado) -> bool:
    """Devuelve False si hay que dejar de procesar (limite del plan o sesion caida)."""
    from tradingagents.llm_clients.chatgpt_auth import ChatGPTAuthError, ChatGPTUnreachable
    # Primero el estado: una caida entre los dos pasos no debe dejarlo "en cola" fuera de la cola.
    if not al.actualizar(tienda, t["id"], solo_si=("en_cola",), estado="corriendo", etapa="Preparando", iniciado=al.ahora()):
        al.sacar_de_cola(tienda, t["id"])
        return True   # se cancelo justo antes de empezar
    al.sacar_de_cola(tienda, t["id"])
    try:
        _backtest(tienda, t, carpeta) if t["tipo"] == "backtest" else _analizar(tienda, t, carpeta, simulado)
        _marcar_sesion(tienda, True)
        return True
    except SystemExit:
        _interrumpir(tienda)
        raise
    except ChatGPTUnreachable:
        al.actualizar(tienda, t["id"], estado="fallido", etapa="OpenAI no respondió", error="Tu sesión sigue bien. Lánzalo de nuevo más tarde.")
        return False
    except ChatGPTAuthError:
        _marcar_sesion(tienda, False)
        al.actualizar(tienda, t["id"], estado="fallido", etapa="Sesión de ChatGPT no válida", error="Hay que volver a iniciar sesión. Mira README-AGENTES.md.")
        return False
    except Exception as exc:
        if _es_limite(exc):
            _pausar_todo(tienda, t)
            return False
        log.exception("Trabajo %s fallido", t["id"])
        al.actualizar(tienda, t["id"], estado="fallido", etapa="Falló", error=_limpio(exc))
        return True


def _encolar_diario(tienda):
    from motor import arbitro
    fecha = al.hoy()
    hallada = tienda.leer(f"{al.prefijo()}config/watchlist.txt")
    tickers = hallada[0].decode().split() if hallada else []
    try:
        ultima = arbitro.precio_referencia("SPY", fecha).get("fecha")
        if ultima and ultima != fecha:   # sin dato no se concluye que estuviera cerrada
            log.info("Hoy no abrió la NYSE; no se encola nada.")
            return
    except Exception:
        log.warning("No se pudo comprobar si hubo mercado; se sigue.")
    for ticker in tickers:
        limpio = "".join(c if c.isalnum() else "-" for c in ticker.lower())
        with contextlib.suppress(al.Conflicto):
            al.encolar(tienda, {"tipo": "analisis", "ticker": ticker, "fecha": fecha, "rondas": 1, "usar_cartera": True,
                                "analistas": (os.getenv("AGENTES_ANALISTAS") or "market,news,fundamentals").split(","), "origen": "diario"},
                       f"diario-{al.AMBIENTE}-{fecha}-{limpio}")


def _una_vuelta(tienda, dueno, simulado):
    """Atiende la cola bajo el candado. False: otro motor lo tiene. PARAR: no seguir intentando."""
    if not al.tomar_candado(tienda, dueno):
        return False
    parar = threading.Event()
    carpeta = Carpeta(tienda, Path(os.environ["HOME"]) / ".tradingagents")
    # El latido cubre tambien la copia inicial y la final: sin el, otro motor podria entrar.
    latido = threading.Thread(target=_latido, args=(tienda, dueno, carpeta, parar), daemon=True)
    latido.start()
    resultado = True

    def al_recibir_sigterm():
        """El hilo principal puede estar esperando a un agente durante minutos, y el aviso de
        Cloud Run da poco margen: se guarda todo aqui y se sale sin esperarlo."""
        try:
            carpeta.devolver()
            _interrumpir(tienda)
            al.soltar_candado(tienda, dueno)
        finally:
            os._exit(143)
    global _al_recibir_sigterm
    _al_recibir_sigterm = al_recibir_sigterm
    try:
        carpeta.traer()
        _interrumpir(tienda)   # tenemos el candado: lo que siga corriendo ya no tiene motor
        while pendientes := al.en_cola(tienda, al.AMBIENTE):
            sigue = _atender(tienda, pendientes[0], carpeta, simulado)
            carpeta.devolver()
            if not sigue:
                resultado = PARAR
                break
    finally:
        try:
            carpeta.devolver()   # tambien si hubo error
        finally:
            parar.set()
            latido.join(timeout=60)   # un latido tardio no debe recrear el candado ya soltado
            carpeta.subir_sesion()
            _al_recibir_sigterm = None   # el candado ya no es nuestro: un aviso tardio no debe tocar nada
            al.soltar_candado(tienda, dueno)
    return resultado


def _cerrar_vista_previa(tienda):
    """Antes de borrar el Job de la vista previa: lo suyo no quedaria atendido por nadie."""
    for t in al.en_cola(tienda, "preview"):
        al.actualizar(tienda, t["id"], estado="fallido", etapa="Cancelado", error="La vista previa se cerró.")
        al.sacar_de_cola(tienda, t["id"])


_al_recibir_sigterm = None


def _salir(*_):
    if _al_recibir_sigterm:
        _al_recibir_sigterm()
    raise SystemExit(143)


def main():
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, _salir)
    tienda = al.abrir()   # antes de cambiar HOME: en local el almacen por defecto sale de ahi
    if "--cerrar-preview" in sys.argv:
        return _cerrar_vista_previa(tienda)
    # Antes de importar TradingAgents: su carpeta sale de HOME. Asi nunca toca la del Mac,
    # y al terminar no queda ninguna copia de la sesion en el disco.
    with tempfile.TemporaryDirectory() as casa:
        os.environ["HOME"] = casa
        simulado = _simulado()
        if simulado:
            from motor import simulado as sim
            sim.instalar()
        if "--diario" in sys.argv:
            _encolar_diario(tienda)
        dueno = f"{al.AMBIENTE}-{uuid.uuid4().hex[:8]}"
        # Siempre una vuelta, aunque no haya cola: asi se liberan los trabajos huerfanos. Y se
        # vuelve a mirar al soltar el candado: lo que llego justo en ese momento no queda varado.
        while True:
            resultado = _una_vuelta(tienda, dueno, simulado)
            if resultado in (False, PARAR) or not al.en_cola(tienda, al.AMBIENTE):
                break
        if not al.candado_vivo(tienda):   # si otro motor corre, el lanzara al siguiente
            for otro in {t["ambiente"] for t in al.en_cola(tienda)} - {al.AMBIENTE}:
                try:
                    al.lanzar_motor(otro)
                except Exception:
                    log.exception("No se pudo lanzar el motor de %s", otro)


if __name__ == "__main__":
    main()
