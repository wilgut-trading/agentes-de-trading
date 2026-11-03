"""Lo que la web usa de TradingAgents. Si esto falla en un PR de actualizacion, el PR sale en
rojo y el mensaje dice que pantalla se veria afectada."""
import inspect
from pathlib import Path

import pytest

from tradingagents.backtest import BacktestSummary, iter_grid, run_backtest, summarize
from tradingagents.default_config import build_default_config
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients import chatgpt_auth
from tradingagents.memory import TradingMemoryLog
from tradingagents.portfolio import PortfolioContext
from tradingagents.reporting import write_report_tree


@pytest.mark.parametrize("nombre,params,pantalla", [
    ("propagate", ["company_name", "trade_date", "asset_type", "portfolio"], "Analizar"),
    ("create_run_state", ["company_name", "trade_date", "asset_type", "portfolio"], "Analizar"),
    ("stream_run", ["graph_input"], "Avance en vivo"),
    ("record_decision", ["company_name", "trade_date", "final_state"], "Historial"),
    ("save_reports", ["final_state", "ticker", "save_path"], "Informes"),
    ("settle_all_pending", ["wait"], "Historial"),
    ("run_settings", [], "Analizar"),
])
def test_grafo(nombre, params, pantalla):
    firma = inspect.signature(getattr(TradingAgentsGraph, nombre)).parameters
    faltan = [p for p in params if p not in firma]
    assert not faltan, f"Pantalla afectada: {pantalla}. {nombre} ya no acepta {faltan}"


def test_backtest():
    assert {"tickers", "dates", "config", "selected_analysts", "run_id", "progress"} <= set(inspect.signature(run_backtest).parameters), "Pantalla afectada: Backtests"
    assert iter_grid("2026-01-05", "2026-01-12", 7) == ["2026-01-05", "2026-01-12"], "Pantalla afectada: Backtests"
    assert callable(summarize) and callable(BacktestSummary.render), "Pantalla afectada: Backtests"


def test_cartera():
    c = PortfolioContext.model_validate({"cash": 10, "currency": "USD", "positions": [{"ticker": "NVDA", "quantity": 1, "average_price": 2}]})
    assert "NVDA" in c.render("NVDA"), "Pantalla afectada: Ajustes (cartera)"


def test_sesion_chatgpt():
    assert chatgpt_auth.AUTH_FILE.name == "chatgpt-auth.json", "Afecta al motor: la sesion se perderia"
    assert callable(chatgpt_auth.list_models) and issubclass(chatgpt_auth.ChatGPTUnreachable, chatgpt_auth.ChatGPTAuthError)


def test_formato_memoria(tmp_path):
    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "m.md")})
    log.store_decision("NVDA", "2026-01-05", "**Rating**: Buy\nTexto", rating="Buy")
    e = log.load_entries()[0]
    assert (e["date"], e["ticker"], e["rating"], e["pending"]) == ("2026-01-05", "NVDA", "Buy", True), "Pantalla afectada: Historial"
    assert {"raw", "alpha", "reflection", "decision"} <= set(e), "Pantalla afectada: Historial"


def test_ubicacion_informe(tmp_path):
    estado = {"company_of_interest": "NVDA", "trade_date": "2026-01-05", "final_trade_decision": "**Rating**: Buy"}
    ruta = Path(write_report_tree(estado, "NVDA", tmp_path / "r"))
    assert ruta.with_name("complete_report.html").is_file(), "Pantalla afectada: Informes"


def test_config():
    c = build_default_config()
    assert {"llm_provider", "deep_think_llm", "quick_think_llm", "memory_log_path", "results_dir"} <= set(c), "Afecta al motor"
