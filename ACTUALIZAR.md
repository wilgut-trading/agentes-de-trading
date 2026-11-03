# Actualizar TradingAgents

Cada lunes, o cuando pulses "Run workflow" en **Actions → Nueva version de TradingAgents**, se
revisa si hay una versión nueva. Si la hay, aparece un PR en borrador. **Nada se instala solo.**

## 1. Pide la explicación

Pega esto en Claude Code:

> Revisa el PR de actualización abierto en wilgut-trading/agentes-de-trading. Explícame en español y sin tecnicismos:
> (a) qué funciones nuevas trae y si me sirven para swing trading;
> (b) qué cambia en cómo decide o en sus datos;
> (c) si consume más de mi plan ChatGPT Plus;
> (d) riesgos o cambios que rompen algo;
> (e) qué habría que ajustar en la web para aprovechar lo nuevo.
> Si te pido aprovechar algo, haz el ajuste en la misma rama del PR. No fusiones nada.

## 2. Prueba la vista previa

La vista previa no se crea sola, porque usa tu sesión de ChatGPT y aún es código sin aprobar:

1. Lee la explicación del paso 1.
2. En el PR, abre **Actions**, entra en la corrida y pulsa **Review deployments**.
3. Marca **vista-previa** y pulsa **Approve and deploy**.
4. Cuando termine, abre la URL que aparece como comentario en el PR.

Verás la franja **VISTA PREVIA**. Usa tu sesión y tu cola, pero guarda informes y memoria aparte: tus
datos reales no se tocan.

## 3. Decide

**Aprobar:**
1. Abre el PR en github.com/wilgut-trading/agentes-de-trading/pulls.
2. Clic en **Ready for review**.
3. Clic en **Merge pull request** y luego en **Confirm merge**.

Se hace un respaldo de tu historial e informes y después se instala.

**Saltarte la versión:**
1. Abre el PR.
2. Clic en **Close pull request**.

No se vuelve a proponer, salvo que lo pidas a mano.

## Si algo sale mal

1. En GitHub, abre **Actions → Volver atrás → Run workflow**.
2. En **imagen**, pega el commit anterior: lo ves en **Actions → Desplegar**, en la última corrida que funcionó.
3. En **respaldo**, déjalo vacío o escribe la carpeta (por ejemplo `2026-12-01-v0.7.2`) si la nueva versión dañó el historial.
4. Clic en **Run workflow**.
