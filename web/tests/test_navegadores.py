"""Recorre la app en navegadores reales. Solo con Playwright instalado:
    AGENTES_CAPTURAS=/ruta .venv/bin/python -m pytest web/tests/test_navegadores.py
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
CAPTURAS = Path(os.getenv("AGENTES_CAPTURAS", "/tmp/agentes-capturas"))


@pytest.fixture(scope="module")
def servidor(tmp_path_factory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    entorno = {**os.environ, "AGENTES_SIN_LOGIN": "1", "AGENTES_SIMULADO": "1", "PYTHONPATH": os.getcwd(),
               "AGENTES_DATOS": str(tmp_path_factory.mktemp("datos"))}
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "web.app:app", "--port", str(puerto), "--log-level", "warning"], env=entorno)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", puerto), timeout=.2).close()
            break
        except OSError:
            time.sleep(.2)
    yield f"http://127.0.0.1:{puerto}"
    p.terminate()


@pytest.mark.parametrize("motor,dispositivo,tema", [("chromium", None, "light"), ("webkit", None, "dark"), ("webkit", "iPhone 15", "dark"), ("webkit", "iPhone 15", "light")])
def test_flujo_completo(servidor, motor, dispositivo, tema):
    nombre = f"{motor}-{(dispositivo or 'escritorio').replace(' ', '')}-{tema}"
    CAPTURAS.mkdir(parents=True, exist_ok=True)
    errores = []
    with sync_api.sync_playwright() as pw:
        navegador = getattr(pw, motor).launch()
        ajustes = dict(pw.devices[dispositivo]) if dispositivo else {}
        ajustes.pop("default_browser_type", None)
        pagina = navegador.new_context(color_scheme=tema, **ajustes).new_page()
        pagina.on("pageerror", lambda e: errores.append(str(e)))
        # WebKit de Playwright da este aviso incluso sin htmx y al abrir un .js suelto: no es de la app.
        pagina.on("console", lambda m: errores.append(m.text) if m.type == "error" and not m.text.startswith("Refused to apply a stylesheet") else None)
        pagina.goto(servidor)
        pagina.screenshot(path=CAPTURAS / f"{nombre}-1-inicio.png", full_page=True)
        ancho = pagina.evaluate("[document.documentElement.scrollWidth, innerWidth]")
        assert ancho[0] <= ancho[1], f"scroll horizontal {ancho}"
        pagina.fill("input[name=ticker]", "NVDA")
        pagina.click("button.primario")
        pagina.wait_for_url("**/analisis/**")
        pagina.wait_for_selector("section.veredicto", timeout=120000)   # solo HTMX puede traerlo
        assert "SIMULADO" in pagina.inner_text("body")
        assert pagina.evaluate("[document.documentElement.scrollWidth <= innerWidth]") == [True]
        pagina.screenshot(path=CAPTURAS / f"{nombre}-2-resultado.png", full_page=True)
        for ruta in ("informes", "historial", "backtests", "ajustes"):
            assert pagina.goto(f"{servidor}/{ruta}").status == 200
            assert pagina.evaluate("document.documentElement.scrollWidth <= innerWidth"), ruta
        pagina.screenshot(path=CAPTURAS / f"{nombre}-3-ajustes.png", full_page=True)
        pagina.fill("input[name=tickers]", "nvda, msft")
        pagina.click("form[action='/ajustes/watchlist'] button")
        assert "Watchlist guardada" in pagina.inner_text("body")
        navegador.close()
    assert not errores, errores
