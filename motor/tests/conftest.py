"""Antes de importar TradingAgents: su carpeta sale de HOME y nunca debe ser la real."""
import os
import tempfile

os.environ["HOME"] = tempfile.mkdtemp(prefix="agentes-pruebas-")
