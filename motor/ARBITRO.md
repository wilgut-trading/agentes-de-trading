# Criterios del árbitro

Escritos antes del código, para que no se ajusten a lo que el código ya hace. Hay dos
árbitros y no son lo mismo.

## 1. Árbitro de desarrollo (el loop que construye esta app)

Cada ronda: tres atacantes independientes intentan romper la app y un árbitro aparte, que
no escribe el código, reproduce cada hallazgo y decide.

- **Atacante A: dinero y decisión.** ¿Puede la app mostrar una calificación equivocada,
  vieja o de otro ticker como si fuera válida? Mirada futura, fechas, zonas horarias,
  calificación mal leída, estadísticas infladas, informes simulados que parezcan reales.
- **Atacante B: acceso y secretos.** Entrar sin ser el dueño, CSRF, XSS desde noticias o
  informes, lectura de la sesión de ChatGPT, rutas sin protección, secretos en el repo.
- **Atacante C: fiabilidad.** Dos análisis a la vez, caída a mitad de un análisis, sesión
  de ChatGPT perdida, cola atascada, vista previa pisando producción, actualización rota.

Reglas del fallo:

- Un hallazgo solo cuenta si el árbitro lo **reproduce** con una prueba o un comando. Sin
  reproducción se rechaza, por grave que suene.
- Severidad: **crítica** (decisión falsa mostrada como válida, acceso ajeno, secreto
  expuesto, sesión perdida), **alta** (fallo silencioso o bloqueo sin salida), **media**,
  **baja**.
- Todo arreglo crítico o alto deja una prueba que falla sin el arreglo.
- **Se aprueba** cuando una ronda completa termina con cero críticas y cero altas abiertas
  y todas las pruebas pasan. Máximo 5 rondas; si no se aprueba, se informa lo que queda.
- El árbitro no puede aprobar lo que no pudo ejecutar: se declara **no verificado**.

## 2. Árbitro de cada análisis (dentro de la app)

Revisa cada decisión de TradingAgents antes de mostrarla. **Solo puede rebajar una
decisión, nunca mejorarla, y no cambia la calificación del framework.**

Veredictos: `NO OPERAR`, `CON RESERVAS`, `SIN OBJECIONES`. Este último significa que no se
encontró un defecto, **no** que la operación vaya a ganar.

Controles deterministas (código, sin modelo):

| Control | Falla si | Resultado |
| --- | --- | --- |
| Calificación | Es `REVIEW` o no se puede leer | No operar |
| Frescura | Sin precio, o último cierre demasiado viejo | No operar |
| Informes | Falta un analista elegido | Reservas (2 o más: no operar) |
| Datos | Una herramienta de datos falló durante el análisis | Reservas |
| Niveles | Entrada o stop lejos del precio, o stop al lado equivocado en Buy u Overweight | No operar |
| Niveles | Sin stop legible, u objetivo al lado contrario. En Underweight o Sell, stop por debajo del precio: vale si reduces una posición larga, no si abres un corto | Reservas |
| Lectura | Solo cuenta lo escrito en la forma exacta: precios como número limpio, plazo como `10 sesiones`, invalidez con un nivel. Lo demás no se adivina | Reservas |
| Coherencia | Gerente de investigación, trader y gerente de cartera en sentidos opuestos, o ninguno respalda la dirección | Reservas |
| Resultados | Hay presentación de resultados dentro de la operación (swing: 28 días; inversión: 10 días) | Reservas |
| Instrumento | Lo analizado no es lo que escribiste (WTI como petróleo CL=F) | Reservas |
| Fecha | Es una fecha pasada: exploración, sin comprobar resultados | Reservas |
| Repetición | Otra ejecución de hoy dio otra calificación | Reservas |
| Historial | Menos de 30 **periodos independientes** medidos al plazo vigente, o sin ventaja medida | Reservas |
| Plan (solo swing) | Falta entrada, objetivo, condición de invalidez o plazo cerrado en sesiones, o el plazo supera 20 sesiones | Reservas |
| Plan (solo swing) | Objetivo no válido o fuera de lo alcanzable en el plazo, o stop dentro del ruido de un día | No operar |
| Niveles (solo swing) | El último precio ya rebasó el stop | No operar en Buy u Overweight; reservas en Underweight o Sell o si la entrada aún no se activa |
| Niveles | Un nivel escrito de forma que no se puede leer como precio | Reservas |
| Plan (solo swing) | El trader propuso no operar | Reservas |
| Riesgo/beneficio (solo swing) | Menor que tu mínimo (2,5 por defecto, se cambia en Ajustes) | No operar |
| Riesgo/beneficio (solo swing) | Con esos niveles no se puede calcular, o solo cumple con una entrada lejana del último precio | Reservas |

Debate adversarial (con el modelo):

1. **Fiscal:** busca lo que invalida la decisión. Cada objeción debe citar textualmente la
   decisión y la evidencia.
2. **Verificación de citas (código):** se descarta toda objeción cuya cita no aparezca
   literalmente. Así una objeción inventada no puede tumbar una decisión; pero el descarte deja
   la decisión en reservas, porque tampoco se puede dar por refutada.
3. **Defensor:** responde cada objeción que sobrevivió, también con citas.
4. **Árbitro (modelo profundo):** falla cada objeción: sostenida o rechazada, grave o leve.

Una objeción grave sostenida lleva a no operar; una leve, a reservas. Si el modelo falla,
el resultado es como mucho `CON RESERVAS`: nunca se aprueba por omisión. Los informes y las
noticias se tratan como datos, no como instrucciones.
