"""Agentes de Trading: la web sobre el codigo original de TradingAgents."""
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

import requests
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup

from motor import almacen as al, arbitro, objetivos as ob
from web import acceso

AQUI = Path(__file__).parent
app = FastAPI(title="Agentes de Trading", docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(acceso.verificar_csrf)])
acceso.instalar(app)
app.include_router(acceso.router)
app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")
plantillas = Jinja2Templates(directory=AQUI / "templates")
# html=False: lo que escriben los agentes o una noticia nunca llega como HTML al navegador.
_md = MarkdownIt("commonmark", {"html": False}).enable("table")
plantillas.env.filters["md"] = lambda t: Markup(_md.render(t or ""))

TICKER_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,14}$")
ANALISTAS = {"market": "Técnico", "social": "Sentimiento", "news": "Noticias", "fundamentals": "Fundamental"}
POR_DEFECTO = (os.getenv("AGENTES_ANALISTAS") or "market,social,news,fundamentals").split(",")
REPO = "wilgut-trading/agentes-de-trading"
MAX_CELDAS = 200
_tienda_abierta = None


def _tienda():
    global _tienda_abierta
    if _tienda_abierta is None:
        _tienda_abierta = al.abrir()
    return _tienda_abierta


def _ticker(valor: str) -> str:
    valor = (valor or "").strip().upper()
    if not TICKER_RE.match(valor):
        raise HTTPException(422, f"Ticker no válido: {valor[:20]}")
    return valor


def _tickers(texto: str) -> list[str]:
    return list(dict.fromkeys(_ticker(t) for t in re.split(r"[,\s]+", texto or "") if t))


def _fecha(valor: str) -> str:
    try:
        f = date.fromisoformat(valor)
    except ValueError:
        raise HTTPException(422, "Fecha no válida.") from None
    if f.isoformat() > al.hoy():
        raise HTTPException(422, "La fecha no puede ser futura.")
    return f.isoformat()


def _analistas(valores: list[str]) -> list[str]:
    elegidos = [a for a in ANALISTAS if a in valores]
    if not elegidos:
        raise HTTPException(422, "Elige al menos un analista.")
    return elegidos


def _etiqueta(t) -> str:
    """Lo que el motor aplico de verdad: un analisis anterior a los objetivos no fue de ninguno."""
    aplicado = (t.get("arbitro") or {}).get("objetivo") if t.get("estado") == "terminado" else t.get("objetivo")
    return ob.OBJETIVOS.get(aplicado, "Anterior a objetivos")


plantillas.env.globals["etiqueta"] = _etiqueta


def _objetivo(valor: str) -> str:
    if valor not in ob.OBJETIVOS:
        raise HTTPException(422, "Elige si es inversión o swing.")
    return valor


def _lanzar() -> bool:
    """Lanza el Job solo si el candado esta libre. Si falla, el trabajo sigue en cola y se ve."""
    if al.candado_vivo(_tienda()):
        return True
    try:
        al.lanzar_motor()
        return True
    except Exception:
        return False


def _ver(request, plantilla, **ctx):
    import tradingagents
    return plantillas.TemplateResponse(request, plantilla, {"ambiente": al.AMBIENTE, "csrf": acceso.token_csrf(request), "objetivos": ob.OBJETIVOS,
                                                            "version": tradingagents.__version__, **ctx})


def _entradas(objetivo):
    from tradingagents.memory import TradingMemoryLog
    hallada = _tienda().leer(f"{al.prefijo()}private/tradingagents/{ob.ruta_memoria(objetivo)}")
    if not hallada:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "memoria.md"
        ruta.write_bytes(hallada[0])
        return TradingMemoryLog({"memory_log_path": str(ruta)}).load_entries()


def _propios(tipo):
    return [t for t in al.trabajos(_tienda()) if t["ambiente"] == al.AMBIENTE and t["tipo"] == tipo]


def _trabajo_o_404(id_):
    t = al.trabajo(_tienda(), id_)
    if not t or t["ambiente"] != al.AMBIENTE:
        raise HTTPException(404, "No existe.")
    return t


@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    firebase = {k: os.getenv(f"FIREBASE_{k.upper()}", "") for k in ("apiKey", "authDomain", "projectId")}
    return plantillas.TemplateResponse(request, "login.html", {"firebase": firebase})


@app.get("/", response_class=HTMLResponse)
def inicio(request: Request):
    lista = _propios("analisis")
    pausados = any(t["estado"] == "pausado" for t in lista + _propios("backtest"))
    # Tambien lo que quedo "corriendo" sin motor vivo: reactivar lo libera.
    varados = not al.candado_vivo(_tienda()) and (bool(al.en_cola(_tienda(), al.AMBIENTE)) or any(t["estado"] == "corriendo" for t in lista))
    return _ver(request, "inicio.html", hoy=al.hoy(), analistas=ANALISTAS, por_defecto=POR_DEFECTO, recientes=lista[:8],
                pausados=pausados, varados=varados)


@app.post("/analizar")
def analizar(ticker: str = Form(), objetivo: str = Form(""), fecha: str = Form(""), rondas: int = Form(1),
             analistas: list[str] = Form(default=[]), usar_cartera: str = Form("")):  # noqa: B008
    if not 1 <= rondas <= 3:
        raise HTTPException(422, "Las rondas van de 1 a 3.")
    t = al.encolar(_tienda(), {"tipo": "analisis", "ticker": _ticker(ticker), "fecha": _fecha(fecha or al.hoy()), "rondas": rondas,
                               "analistas": _analistas(analistas), "usar_cartera": usar_cartera == "1", "objetivo": _objetivo(objetivo)})
    if not _lanzar():
        al.actualizar(_tienda(), t["id"], aviso="No se pudo arrancar el motor; sigue en cola.")
    return RedirectResponse(f"/analisis/{t['id']}", status_code=303)


@app.post("/reactivar")
def reactivar():
    """Vuelve a poner en cola lo pausado por limite y arranca el motor."""
    for t in al.trabajos(_tienda()):
        if t["ambiente"] == al.AMBIENTE and t["estado"] == "pausado" \
                and al.actualizar(_tienda(), t["id"], solo_si=("pausado",), estado="en_cola", etapa="En cola", avance=0):
            al.asegurar_en_cola(_tienda(), t["id"])
    _lanzar()
    return RedirectResponse("/", status_code=303)


@app.get("/analisis/{id_}", response_class=HTMLResponse)
def analisis(request: Request, id_: str):
    t = _trabajo_o_404(id_)
    dias = (date.fromisoformat(al.hoy()) - date.fromisoformat(t["fecha"])).days if t.get("fecha") else 0
    return _ver(request, "analisis.html", t=t, dias=dias, candado=al.candado_vivo(_tienda()))


@app.get("/analisis/{id_}/avance", response_class=HTMLResponse)
def avance(request: Request, id_: str):
    t = _trabajo_o_404(id_)
    if t["estado"] in ("terminado", "fallido", "pausado"):
        return Response(headers={"HX-Refresh": "true"})
    return _ver(request, "_avance.html", t=t, candado=al.candado_vivo(_tienda()))


@app.post("/cancelar/{id_}")
def cancelar(id_: str):
    _trabajo_o_404(id_)
    if al.actualizar(_tienda(), id_, solo_si=("en_cola", "pausado"), estado="fallido", etapa="Cancelado", error="Cancelado por ti antes de empezar."):
        al.sacar_de_cola(_tienda(), id_)
    return RedirectResponse("/", status_code=303)


@app.get("/informes", response_class=HTMLResponse)
def informes(request: Request, ticker: str = "", objetivo: str = ""):
    lista = [t for t in _propios("analisis") if t["estado"] == "terminado" and (not ticker or t["ticker"] == ticker.strip().upper())
             and (not objetivo or (t.get("arbitro") or {}).get("objetivo") == objetivo)]
    return _ver(request, "informes.html", lista=lista, ticker=ticker, objetivo=objetivo)


@app.get("/informes/{id_}/completo", response_class=HTMLResponse)
def informe_completo(id_: str):
    _trabajo_o_404(id_)
    hallado = _tienda().leer(f"{al.prefijo()}informes/{id_}.html")
    if not hallado:
        raise HTTPException(404, "El informe aún no existe.")
    # sandbox: el HTML generado no puede ejecutar nada aunque una noticia traiga un script.
    return HTMLResponse(hallado[0], headers={"Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"})


@app.get("/historial", response_class=HTMLResponse)
def historial(request: Request, ticker: str = "", decision: str = "", objetivo: str = "swing"):
    objetivo = objetivo if objetivo in ob.OBJETIVOS else "swing"
    entradas = _entradas(objetivo)
    filas = [e for e in entradas if (not ticker or e["ticker"] == ticker.strip().upper()) and (not decision or e["rating"] == decision)]
    filas.sort(key=lambda e: e["date"], reverse=True)
    return _ver(request, "historial.html", filas=filas, m=arbitro.historial(entradas, ob.sesiones(ob.ajustes(_tienda()), objetivo)), minimo=arbitro.MIN_HISTORIAL,
                objetivo=objetivo, ticker=ticker, decision=decision, ratings=list(arbitro.SENTIDO))


@app.get("/backtests", response_class=HTMLResponse)
def backtests(request: Request):
    return _ver(request, "backtests.html", lista=_propios("backtest"), analistas=ANALISTAS, por_defecto=POR_DEFECTO, hoy=al.hoy(), maximo=MAX_CELDAS)


@app.post("/backtests")
def lanzar_backtest(tickers: str = Form(), desde: str = Form(), hasta: str = Form(), cada: int = Form(7),
                    analistas: list[str] = Form(default=[]), confirmar: str = Form(""), objetivo: str = Form("")):  # noqa: B008
    from tradingagents.backtest import iter_grid
    lista = _tickers(tickers)
    if not lista or cada < 1:
        raise HTTPException(422, "Faltan tickers o el intervalo no es válido.")
    try:
        celdas = len(lista) * len(iter_grid(_fecha(desde), _fecha(hasta), cada))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if celdas > MAX_CELDAS:
        raise HTTPException(422, f"Son {celdas} análisis; el máximo es {MAX_CELDAS}.")
    if confirmar.strip() != str(celdas):
        raise HTTPException(422, f"Esto lanza {celdas} análisis contra tu plan de ChatGPT. Vuelve atrás y escribe {celdas} para confirmar.")
    al.encolar(_tienda(), {"tipo": "backtest", "tickers": lista, "desde": desde, "hasta": hasta, "cada": cada, "analistas": _analistas(analistas), "objetivo": _objetivo(objetivo), "ajustes": ob.ajustes(_tienda())})
    _lanzar()
    return RedirectResponse("/backtests", status_code=303)


def _pr_pendiente():
    try:
        r = requests.get(f"https://api.github.com/repos/{REPO}/pulls?state=open&per_page=30", timeout=4)
        # Solo ramas de este repo: cualquiera puede abrir un PR desde su fork con ese nombre.
        return next((p for p in r.json() if p["head"]["ref"].startswith("actualizacion/") and p["head"]["repo"]["full_name"] == REPO), None) if r.ok else None
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


@app.get("/ajustes", response_class=HTMLResponse)
def ajustes(request: Request, aviso: str = ""):
    p = al.prefijo()
    hallada = _tienda().leer(f"{p}config/watchlist.txt")
    return _ver(request, "ajustes.html", aviso=aviso[:200], pr=_pr_pendiente(), ajustes_app=ob.ajustes(_tienda()), max_swing=ob.MAX_SWING, watchlist=hallada[0].decode().split() if hallada else [],
                cartera=json.dumps(al.leer_json(_tienda(), f"{p}config/cartera.json", {"cash": None, "currency": "USD", "positions": []}), indent=1),
                sesion=al.leer_json(_tienda(), "estado/sesion.json"), respaldo=al.leer_json(_tienda(), "respaldos/ultimo.json"))


@app.post("/ajustes/analisis")
def guardar_ajustes(diario: str = Form(), sesiones_swing: str = Form(), sesiones_inversion: str = Form(), rr_minimo: str = Form()):
    try:
        valores = ob.validar({"diario": diario, "sesiones_swing": sesiones_swing, "sesiones_inversion": sesiones_inversion, "rr_minimo": rr_minimo.replace(",", ".")})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    al.guardar_json(_tienda(), f"{al.prefijo()}{ob.CLAVE}", valores)
    return RedirectResponse("/ajustes?aviso=Ajustes+de+an%C3%A1lisis+guardados", status_code=303)


@app.post("/ajustes/watchlist")
def guardar_watchlist(tickers: str = Form("")):
    _tienda().escribir(f"{al.prefijo()}config/watchlist.txt", "\n".join(_tickers(tickers)).encode())
    return RedirectResponse("/ajustes?aviso=Watchlist+guardada", status_code=303)


@app.post("/ajustes/cartera")
def guardar_cartera(cartera: str = Form("")):
    from pydantic import ValidationError

    from tradingagents.portfolio import PortfolioContext
    try:
        valida = PortfolioContext.model_validate_json(cartera)
    except ValidationError as exc:
        raise HTTPException(422, f"Cartera no válida: {exc.errors()[0]['msg']}") from None
    al.guardar_json(_tienda(), f"{al.prefijo()}config/cartera.json", valida.model_dump())
    return RedirectResponse("/ajustes?aviso=Cartera+guardada", status_code=303)


@app.exception_handler(HTTPException)
def error_http(request: Request, exc: HTTPException):
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code)
