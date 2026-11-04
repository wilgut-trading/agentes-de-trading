# Agentes de Trading

TradingAgents en la web, para usarlo desde tu PC, tu Mac y tu iPhone. Solo entra tu cuenta de Google.

**Es una herramienta de investigación. No es asesoría financiera y no envía órdenes.** Los resultados
varían entre ejecuciones. Antes de usarlos en decisiones reales, haz un backtest sobre tu watchlist y
mira la tarjeta "¿Tiene ventaja?" en Historial.

## Dos tipos de análisis

Al lanzar un análisis eliges uno, y cada uno tiene su propio historial:

- **Swing trading:** de 1 a 20 sesiones. Exige entrada, stop, objetivo y plazo, y riesgo/beneficio mínimo de 2,5.
- **Inversión:** de meses a años, con valoración y fundamentales.

En **Ajustes** cambias el tipo del análisis diario, a cuántas sesiones se mide cada uno y el riesgo/beneficio mínimo.

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

## Configuración de seguridad (se comprueba al desplegar)

- **Ambiente `vista-previa` en GitHub:** con revisor obligatorio. Compruébalo con
  `gh api repos/wilgut-trading/agentes-de-trading/environments/vista-previa`: debe mostrar `required_reviewers`.
- **Ambiente `produccion`:** solo para la rama `main`. Aquí, y no en el repositorio, van los secretos.
- **Google Cloud:** el acceso sin claves desde GitHub se limita a este repositorio y a esos dos ambientes.
- **`TOKEN_ACTUALIZACION`:** hace falta para abrir el PR de actualización, porque la organización no deja
  que Actions cree PRs, y para subir versiones que cambien `.github/workflows`. Ponle caducidad y permiso
  solo sobre este repositorio.
