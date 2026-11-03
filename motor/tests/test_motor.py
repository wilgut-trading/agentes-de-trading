import json
import threading

import pytest

from motor import almacen as al
from motor import arbitro as ar
from motor import run

MEDIDO = {"n": 100, "aciertos": 70, "tasa": .7, "bajo": .6, "alto": .8, "alfa_medio": 2.0, "ventaja": True}
LARGO = "x" * 300
REF = {"cierre": 100.0, "fecha": "2026-11-02", "rango_medio": 2.0}


def _estado(**kw):
    return {"market_report": LARGO, "news_report": LARGO, "investment_plan": "**Recommendation**: Buy",
            "trader_investment_plan": "**Action**: Buy\n**Entry Price**: 100\n**Stop Loss**: 95", "final_trade_decision": "**Rating**: Buy", **kw}


def _controles(estado=None, rating="Buy", ref=REF, resultados=None, fallos=0, medicion=MEDIDO, fecha="2026-11-03", ticker="NVDA"):
    c = ar.controles_deterministas(estado or _estado(), ticker, fecha, rating, ["market", "news"], ref, resultados, fallos, medicion, False)
    return {(x["control"], x["resultado"]) for x in c}


def test_decision_limpia():
    assert _controles() == set()


@pytest.mark.parametrize("kw,esperado", [
    ({"rating": "REVIEW"}, ("Calificación", ar.NO_OPERAR)),
    ({"ref": {}}, ("Frescura", ar.NO_OPERAR)),
    ({"fecha": "2026-11-20"}, ("Frescura", ar.NO_OPERAR)),
    ({"fallos": 2}, ("Datos", ar.RESERVAS)),
    ({"resultados": "2026-11-05"}, ("Resultados", ar.RESERVAS)),
    ({"resultados": Ellipsis}, ("Resultados", ar.RESERVAS)),
    ({"medicion": {**MEDIDO, "n": 3}}, ("Historial", ar.RESERVAS)),
    ({"medicion": {**MEDIDO, "ventaja": False}}, ("Historial", ar.RESERVAS)),
])
def test_controles(kw, esperado):
    assert esperado in _controles(**kw)


def test_stop_al_lado_equivocado_y_nivel_lejano():
    assert ("Niveles", ar.NO_OPERAR) in _controles(_estado(trader_investment_plan="**Entry Price**: 100\n**Stop Loss**: 105"))
    assert ("Niveles", ar.NO_OPERAR) in _controles(_estado(trader_investment_plan="**Entry Price**: 500"))
    assert ("Niveles", ar.NO_OPERAR) in _controles(_estado(trader_investment_plan="**Stop Loss**: 110"), rating="Buy")
    assert ("Niveles", ar.NO_OPERAR) in _controles(_estado(trader_investment_plan="**Entry Price**: 100\n**Stop Loss**: 95"), rating="Sell")


def test_incoherencia_e_informes():
    assert ("Coherencia", ar.RESERVAS) in _controles(_estado(trader_investment_plan="**Action**: Sell"))
    assert ("Informes", ar.RESERVAS) in _controles(_estado(news_report=""))
    assert ("Informes", ar.NO_OPERAR) in _controles(_estado(news_report="", market_report=""))


def test_citas_inventadas_no_cuentan():
    o = ar.Objecion(afirmacion="el margen subió mucho", evidencia="esto nunca se dijo en ninguna parte", objecion="x")
    buena = ar.Objecion(afirmacion="el margen subió mucho", evidencia="los **ingresos** cayeron 10 %", objecion="y")
    assert ar.citas_validas([o, buena], "Dice que el margen subió mucho.", "Los ingresos\ncayeron 10 % este año") == [buena]


def test_historial_y_wilson():
    entradas = [{"rating": "Buy", "alpha": "+2.0%", "pending": False}] * 25 + [{"rating": "Sell", "alpha": "+1.0%", "pending": False}] * 5 + \
               [{"rating": "Hold", "alpha": "+9%", "pending": False}, {"rating": "Buy", "alpha": None, "pending": True}]
    m = ar.historial(entradas)
    assert (m["n"], m["aciertos"]) == (30, 25) and m["ventaja"]
    assert not ar.historial(entradas[:29])["ventaja"]
    assert ar.wilson(5, 10)[0] < .5 < ar.wilson(5, 10)[1]


class Lento:
    def with_structured_output(self, schema):
        raise RuntimeError("cuota")


def test_debate_caido_nunca_aprueba():
    j = ar.arbitrar(_estado(), "NVDA", "2026-11-03", "Buy", ["market", "news"], Lento(), Lento(), MEDIDO, vivo=False,
                    precio=lambda *a: REF, eventos=lambda *a: None)
    assert j["veredicto"] == ar.RESERVAS


def test_precio_que_falla_bloquea():
    def falla(*a):
        raise OSError
    j = ar.arbitrar(_estado(), "NVDA", "2026-11-03", "Buy", ["market", "news"], Lento(), Lento(), MEDIDO, vivo=False, precio=falla)
    assert j["veredicto"] == ar.NO_OPERAR


# --- almacen, cola y candado ---

def test_escritura_condicional(tmp_path):
    t = al.Local(tmp_path)
    v = t.escribir("a/b", b"1", si_version=0)
    with pytest.raises(al.Conflicto):
        t.escribir("a/b", b"2", si_version=0)
    t.escribir("a/b", b"2", si_version=v)
    with pytest.raises(al.Conflicto):
        t.escribir("a/b", b"3", si_version=v)
    with pytest.raises(ValueError):
        t.leer("../fuera")


def test_candado_solo_uno_gana(tmp_path):
    t = al.Local(tmp_path)
    ganadores = []
    hilos = [threading.Thread(target=lambda i=i: al.tomar_candado(t, f"m{i}") and ganadores.append(i)) for i in range(12)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert len(ganadores) == 1


def test_candado_vencido_se_recupera(tmp_path):
    t = al.Local(tmp_path)
    al.guardar_json(t, al.CANDADO, {"dueno": "muerto", "vence": "2020-01-01T00:00:00-05:00"})
    assert not al.candado_vivo(t) and al.tomar_candado(t, "nuevo")


def test_cola_concurrente_no_pierde_nada(tmp_path):
    t = al.Local(tmp_path)
    hilos = [threading.Thread(target=al.encolar, args=(t, {"tipo": "analisis"})) for _ in range(10)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert len(al.en_cola(t)) == 10


def test_diario_no_duplica(tmp_path):
    t = al.Local(tmp_path)
    al.encolar(t, {"tipo": "analisis"}, "diario-a")
    with pytest.raises(al.Conflicto):
        al.encolar(t, {"tipo": "analisis"}, "diario-a")
    assert len(al.en_cola(t)) == 1


# --- motor ---

@pytest.fixture
def motor(tmp_path, monkeypatch):
    t = al.Local(tmp_path / "bucket")
    monkeypatch.setenv("HOME", str(tmp_path / "casa"))
    (tmp_path / "casa").mkdir()
    return t


def _carpeta(t, tmp_path):
    return run.Carpeta(t, tmp_path / "casa" / ".tradingagents")


def test_sesion_se_sube_al_cambiar_y_preview_usa_la_misma(motor, tmp_path, monkeypatch):
    motor.escribir(al.SESION, b"token-1")
    monkeypatch.setattr(al, "AMBIENTE", "preview")
    c = _carpeta(motor, tmp_path)
    c.traer()
    ruta = c.casa / "chatgpt-auth.json"
    assert ruta.read_bytes() == b"token-1" and ruta.stat().st_mode & 0o077 == 0
    ruta.write_bytes(b"token-2")
    (c.casa / "memory").mkdir()
    (c.casa / "memory/trading_memory.md").write_bytes(b"m")
    c.devolver()
    assert motor.leer(al.SESION)[0] == b"token-2"
    assert motor.leer("preview/private/tradingagents/memory/trading_memory.md")[0] == b"m"
    assert motor.leer("private/tradingagents/memory/trading_memory.md") is None


def _trabajo(t, **kw):
    return al.encolar(t, {"tipo": "analisis", "ticker": "NVDA", "fecha": al.hoy(), "rondas": 1, "analistas": ["market"], "usar_cartera": False, **kw})


def test_error_sube_la_sesion_y_suelta_candado(motor, tmp_path, monkeypatch):
    motor.escribir(al.SESION, b"token-1")
    _trabajo(motor)

    def rompe(tienda, t, carpeta, simulado):
        (carpeta.casa / "chatgpt-auth.json").write_bytes(b"token-2")
        raise ValueError("boom")
    monkeypatch.setattr(run, "_analizar", rompe)
    assert run._una_vuelta(motor, "yo", False)
    assert motor.leer(al.SESION)[0] == b"token-2"
    assert not al.candado_vivo(motor)
    assert al.trabajos(motor)[0]["estado"] == "fallido"


def test_limite_pausa_todo_y_no_reintenta(motor, monkeypatch):
    _trabajo(motor)
    _trabajo(motor)
    llamadas = []

    class RateLimitError(Exception):
        pass

    def rompe(*a):
        llamadas.append(1)
        raise RateLimitError("usage limit")
    monkeypatch.setattr(run, "_analizar", rompe)
    run._una_vuelta(motor, "yo", False)
    assert len(llamadas) == 1
    assert {t["estado"] for t in al.trabajos(motor)} == {"pausado"} and not al.en_cola(motor)


def test_trabajo_huerfano_se_marca(motor, monkeypatch):
    t = _trabajo(motor)
    al.sacar_de_cola(motor, t["id"])
    al.actualizar(motor, t["id"], estado="corriendo")
    run._una_vuelta(motor, "yo", False)
    assert al.trabajo(motor, t["id"])["estado"] == "fallido"


def test_con_candado_ajeno_no_corre(motor):
    al.tomar_candado(motor, "otro")
    _trabajo(motor)
    assert run._una_vuelta(motor, "yo", False) is False
    assert al.en_cola(motor)


def test_preview_no_atiende_produccion(motor, monkeypatch):
    t = _trabajo(motor, ambiente="prod")
    monkeypatch.setattr(al, "AMBIENTE", "preview")
    monkeypatch.setattr(run, "_analizar", lambda *a: pytest.fail("no debia correr"))
    run._una_vuelta(motor, "yo", False)
    assert al.trabajo(motor, t["id"])["estado"] == "en_cola"


def test_simulado_prohibido_en_la_nube(monkeypatch):
    monkeypatch.setenv("AGENTES_SIMULADO", "1")
    monkeypatch.setenv("AGENTES_BUCKET", "b")
    with pytest.raises(RuntimeError):
        run._simulado()


def test_circuito_completo_con_grafo_real(motor, monkeypatch):
    from motor import simulado
    simulado.instalar()
    monkeypatch.setattr(ar, "precio_referencia", lambda *a: {"cierre": 100.0, "fecha": al.hoy(), "rango_medio": 2.0})
    monkeypatch.setattr(ar, "proximos_resultados", lambda *a: None)
    t = _trabajo(motor)
    run._una_vuelta(motor, "yo", True)
    fin = al.trabajo(motor, t["id"])
    assert fin["estado"] == "terminado", fin.get("error")
    assert fin["rating"] == "Hold" and fin["simulado"] is True and fin["veredicto"] != ar.SIN_OBJECIONES
    assert motor.leer(f"informes/{t['id']}.html")
    assert not any("trading_memory" in c for c in motor.listar("private/")) or b"NVDA" not in motor.leer("private/tradingagents/memory/trading_memory.md")[0]
    assert json.dumps(fin)  # todo serializable
