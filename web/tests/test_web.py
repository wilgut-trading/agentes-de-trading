import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from motor import almacen as al
from web import acceso, app as modulo


@pytest.fixture
def tienda(tmp_path, monkeypatch):
    t = al.Local(tmp_path)
    monkeypatch.setattr(modulo, "_tienda_abierta", t)
    monkeypatch.setattr(al, "lanzar_motor", lambda *a: None)
    return t


@pytest.fixture
def dentro(tienda, monkeypatch):
    monkeypatch.setenv("AGENTES_SIN_LOGIN", "1")
    monkeypatch.setattr(acceso, "_es_local", lambda r: True)
    for v in ("K_SERVICE", "CLOUD_RUN_JOB", "AGENTES_BUCKET"):
        monkeypatch.delenv(v, raising=False)
    return TestClient(modulo.app, base_url="http://x.test", headers={"origin": "http://x.test"})


def _csrf(c):
    return re.search(r'name="csrf" value="(\w+)"', c.get("/").text).group(1)


def _analisis(c, **kw):
    datos = {"ticker": "NVDA", "fecha": al.hoy(), "analistas": ["market"], "csrf": _csrf(c), **kw}
    return c.post("/analizar", data=datos, follow_redirects=False)


def test_todas_las_rutas_exigen_sesion(tienda, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "agentes-web")
    c = TestClient(modulo.app, base_url="https://x.test")
    probadas = 0
    for r in modulo.app.routes:
        if not isinstance(r, APIRoute) or r.path in acceso.PUBLICAS:
            continue
        ruta = r.path.replace("{id_}", "abc")
        for metodo in r.methods - {"HEAD"}:
            respuesta = c.request(metodo, ruta, headers={"origin": "https://x.test"} if metodo == "POST" else {}, follow_redirects=False)
            assert respuesta.status_code in (303, 401, 403), f"{metodo} {ruta}"
            probadas += 1
    assert probadas >= 12


def test_en_la_nube_no_hay_puerta_abierta(tienda, monkeypatch):
    monkeypatch.setenv("AGENTES_SIN_LOGIN", "1")
    monkeypatch.setenv("K_SERVICE", "agentes-web")
    monkeypatch.setattr(acceso, "_es_local", lambda r: True)
    assert TestClient(modulo.app).get("/", follow_redirects=False).status_code == 303


def test_otra_cuenta_o_correo_sin_verificar_rechazados(monkeypatch):
    monkeypatch.setenv("AGENTES_CORREOS", "dueno@x.com")
    assert acceso._permitido({"email": "dueno@x.com", "email_verified": True})
    assert not acceso._permitido({"email": "otro@x.com", "email_verified": True})
    assert not acceso._permitido({"email": "dueno@x.com", "email_verified": False})
    assert not acceso._permitido({"email": "dueno@x.com"})
    monkeypatch.setenv("AGENTES_CORREOS", "")
    assert not acceso._permitido({"email": "", "email_verified": True})


def test_csrf(dentro):
    assert dentro.post("/analizar", data={"ticker": "NVDA", "fecha": al.hoy(), "analistas": ["market"]}).status_code == 403
    assert dentro.post("/analizar", data={"ticker": "NVDA", "csrf": _csrf(dentro)}, headers={"origin": "https://malo.test"}).status_code == 403
    assert _analisis(dentro).status_code == 303


@pytest.mark.parametrize("ticker", ["../etc", "<script>", "", "A B", "NVDA;rm"])
def test_ticker_invalido(dentro, ticker):
    assert _analisis(dentro, ticker=ticker).status_code == 422


def test_fecha_futura(dentro):
    assert _analisis(dentro, fecha="2999-01-01").status_code == 422


def test_informe_no_ejecuta_nada_y_markdown_escapa(dentro, tienda):
    id_ = _analisis(dentro).headers["location"].rsplit("/", 1)[1]
    tienda.escribir(f"informes/{id_}.html", b"<script>alert(1)</script>")
    r = dentro.get(f"/informes/{id_}/completo")
    assert "sandbox" in r.headers["content-security-policy"]
    al.actualizar(tienda, id_, estado="terminado", rating="Hold", veredicto="NO OPERAR", vivo=True, version="x", decision="<img src=x onerror=alert(1)> [a](javascript:alert(1))",
                  arbitro={"veredicto": "NO OPERAR", "controles": [{"control": "<b>", "resultado": "NO OPERAR", "detalle": "<script>x</script>"}], "referencia": {}, "debate": {"objeciones": []}})
    pagina = dentro.get(f"/analisis/{id_}").text
    assert "<img src=x" not in pagina and "<script>x" not in pagina and 'href="javascript' not in pagina
    assert "no-store" in dentro.get("/").headers["cache-control"]


def test_simulado_se_ve(dentro, tienda):
    id_ = _analisis(dentro).headers["location"].rsplit("/", 1)[1]
    al.actualizar(tienda, id_, estado="terminado", rating="Hold", veredicto="CON RESERVAS", vivo=False, simulado=True, version="x", decision="d",
                  arbitro={"veredicto": "CON RESERVAS", "controles": [], "referencia": {}, "debate": {"objeciones": []}})
    assert "SIMULADO" in dentro.get(f"/analisis/{id_}").text


def test_web_no_lanza_si_hay_candado(dentro, tienda, monkeypatch):
    llamadas = []
    monkeypatch.setattr(al, "lanzar_motor", lambda *a: llamadas.append(1))
    al.tomar_candado(tienda, "otro")
    _analisis(dentro)
    assert not llamadas
    al.soltar_candado(tienda, "otro")
    _analisis(dentro)
    assert llamadas == [1]


def test_fallo_al_lanzar_se_ve(dentro, tienda, monkeypatch):
    def falla(*a):
        raise OSError("x")
    monkeypatch.setattr(al, "lanzar_motor", falla)
    ruta = _analisis(dentro).headers["location"]
    assert "sigue en cola" in dentro.get(ruta).text
    assert "ningún motor" in dentro.get("/").text


def test_cartera_y_watchlist(dentro, tienda):
    t = _csrf(dentro)
    assert dentro.post("/ajustes/cartera", data={"cartera": "{malo", "csrf": t}).status_code == 422
    assert dentro.post("/ajustes/cartera", data={"cartera": '{"cash": 5, "positions": []}', "csrf": t}).status_code == 200
    assert dentro.post("/ajustes/watchlist", data={"tickers": "nvda, msft nvda", "csrf": t}).status_code == 200
    assert tienda.leer("config/watchlist.txt")[0] == b"NVDA\nMSFT"


def test_id_con_ruta_no_escapa(dentro):
    assert dentro.get("/analisis/..%2F..%2Fprivate").status_code == 404
    assert dentro.get("/informes/..%2Fprivate/completo").status_code == 404


def test_backtest_pide_confirmacion_y_tope(dentro):
    base = {"tickers": "NVDA", "desde": "2026-01-05", "hasta": "2026-01-12", "cada": 7, "analistas": ["market"], "csrf": _csrf(dentro)}
    assert dentro.post("/backtests", data=base).status_code == 422
    assert dentro.post("/backtests", data={**base, "confirmar": "2"}, follow_redirects=False).status_code == 303
    assert dentro.post("/backtests", data={**base, "cada": 1, "desde": "2020-01-01", "confirmar": "9"}).status_code == 422


def test_r2_c8_vista_previa_puede_entrar_y_otros_origenes_no(monkeypatch):
    from starlette.requests import Request
    monkeypatch.setenv("K_SERVICE", "agentes-web")
    monkeypatch.setenv("AGENTES_AMBIENTE", "preview")

    def peticion(origen):
        return Request({"type": "http", "method": "POST", "path": "/login", "scheme": "https", "query_string": b"", "server": ("p.run.app", 443),
                        "headers": [(b"host", b"p.run.app"), (b"origin", origen.encode())]})
    assert acceso._origen_valido(peticion("https://p.run.app"))
    assert not acceso._origen_valido(peticion("https://malo.test"))
    monkeypatch.setenv("AGENTES_AMBIENTE", "prod")
    assert not acceso._origen_valido(peticion("https://p.run.app"))


def test_r2_c9_pr_de_un_fork_no_cuenta(monkeypatch):
    class R:
        ok = True

        def json(self):
            return [{"head": {"ref": "actualizacion/v9", "repo": {"full_name": "atacante/fork"}}, "html_url": "x"}]
    monkeypatch.setattr(modulo.requests, "get", lambda *a, **k: R())
    assert modulo._pr_pendiente() is None
