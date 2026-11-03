"""Un modelo falso para probar el circuito completo sin gastar cuota de ChatGPT.

Corre el grafo real de TradingAgents. Solo en local (`motor.run._simulado` lo impide en
la nube) y todo lo que produce queda marcado como SIMULADO y fuera del historial.
"""
from enum import Enum

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

RELLENO = "Informe de prueba generado por el modo simulado; no contiene ningún análisis real. " * 4


class Falso(BaseChatModel):
    @property
    def _llm_type(self):
        return "falso"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=RELLENO))])

    def with_structured_output(self, schema, **kw):
        from langchain_core.runnables import RunnableLambda
        return RunnableLambda(lambda _: _construir(schema))


def _construir(schema: type[BaseModel]):
    campos = {}
    for nombre, campo in schema.model_fields.items():
        tipo = campo.annotation
        if not campo.is_required():
            continue
        if isinstance(tipo, type) and issubclass(tipo, Enum):
            # La calificacion simulada es siempre neutral: no debe parecer una senal.
            campos[nombre] = "Hold" if "Hold" in tipo._value2member_map_ else next(iter(tipo))
        elif tipo in (int, float):
            campos[nombre] = 0
        elif "list" in str(tipo):
            campos[nombre] = []
        else:
            campos[nombre] = RELLENO
    return schema.model_validate(campos)


class _Cliente:
    def get_llm(self):
        return Falso()


def instalar():
    import tradingagents.graph.trading_graph as tg
    tg.create_tier_client = lambda *a, **k: _Cliente()
