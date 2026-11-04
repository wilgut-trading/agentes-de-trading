"""Cuanto gasta cada analisis del plan de ChatGPT: lo que no se mide no se puede optimizar."""
import threading

from langchain_core.callbacks import BaseCallbackHandler


class Consumo(BaseCallbackHandler):
    def __init__(self, previo: dict | None = None):
        """Un backtest que se reanuda sigue sumando sobre lo que ya llevaba."""
        previo = previo or {}
        self._turno = threading.Lock()
        self.llamadas, self.herramientas = previo.get("llamadas", 0), previo.get("herramientas", 0)
        self.entrada, self.salida = previo.get("entrada", 0), previo.get("salida", 0)
        self.por_modelo = dict(previo.get("por_modelo", {}))

    def on_chat_model_start(self, serialized, messages, **kw):
        modelo = (kw.get("metadata") or {}).get("ls_model_name") or (kw.get("invocation_params") or {}).get("model") or "modelo"
        with self._turno:
            self.llamadas += 1
            self.por_modelo[modelo] = self.por_modelo.get(modelo, 0) + 1

    def on_tool_start(self, serialized, input_str, **kw):
        with self._turno:
            self.herramientas += 1

    def on_llm_end(self, response, **kw):
        try:
            uso = response.generations[0][0].message.usage_metadata or {}
        except (AttributeError, IndexError, TypeError):
            return
        with self._turno:
            self.entrada += uso.get("input_tokens", 0)
            self.salida += uso.get("output_tokens", 0)

    def resumen(self) -> dict:
        with self._turno:
            return {"llamadas": self.llamadas, "herramientas": self.herramientas, "entrada": self.entrada,
                    "salida": self.salida, "por_modelo": dict(self.por_modelo)}
