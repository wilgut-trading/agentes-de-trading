# Agentes de Trading

TradingAgents en la web, para usarlo desde tu PC, tu Mac y tu iPhone. Solo entra tu cuenta de Google.

**Es una herramienta de investigación. No es asesoría financiera y no envía órdenes.** Los resultados
varían entre ejecuciones. Antes de usarlos en decisiones reales, haz un backtest sobre tu watchlist y
mira la tarjeta "¿Tiene ventaja?" en Historial.

## Cómo leer un resultado

Arriba verás dos cosas: la calificación de TradingAgents y el veredicto del árbitro. El árbitro solo
puede rebajar una decisión (criterios en `motor/ARBITRO.md`). "SIN OBJECIONES" significa que no encontró
defectos, no que vaya a ganar.

## Tareas comunes

- **Pausar el diario:** Google Cloud → Cloud Scheduler → `agentes-diario` → Pause.
- **Ver el gasto:** Google Cloud → Facturación → Informes. La alerta está en US$ 10 al mes.
- **Análisis pausado por límite:** el plan de ChatGPT llegó a su límite. Cuando se renueve, pulsa **Reactivar**.
- **Renovar la sesión de ChatGPT:** cuando Ajustes diga "Caducada", pide a Claude Code los pasos de
  inicio de sesión de este proyecto. Tus credenciales las escribes tú.
- **Actualizar TradingAgents:** mira [ACTUALIZAR.md](ACTUALIZAR.md).
