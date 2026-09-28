# 0001 — Núcleo sin interfaz, con CLI y TUI como frontends

Fecha: 2026-09-28 · Estado: aceptado

## Contexto

`modman` era un script de 791 líneas dentro del repo de un servidor concreto, con las rutas de los
dos destinos, la versión de Minecraft y el loader como constantes, y con la red, las decisiones y la
impresión mezcladas en las mismas funciones. Funcionaba bien para ese pack y no servía para ningún
otro.

Hacía falta: usarlo en cualquier repositorio de modpack (con servidor, sin servidor o con varias
instancias), que lo siguieran usando agentes por línea de comandos, y poder mirarlo cómodamente a
mano cuando hay 191 mods.

## Decisión

Tres capas, con el núcleo sin interfaz:

- `core/` no importa nada de terminal y **no llama a `sys.exit`**: lanza excepciones.
- `cli/` imprime plano y ofrece `--json`. Es el frontend principal, porque la salida la consumen
  agentes y scripts.
- `tui/` (Textual) es un segundo frontend sobre el mismo núcleo, no una alternativa a la CLI.

Stack: **Python + Textual + Rich**, sin dependencias en el núcleo ni en la CLI. Es lo que ya usa
`McConsole` en este mismo workspace (mismo par de entry points, `uv` y `hatchling`), el código
existente ya era Python, y el volumen de datos no justifica un binario compilado.

Dos consecuencias buscadas:

1. **`deploy` planifica antes de actuar.** `plan()` devuelve pasos y no toca el disco; `apply()`
   los ejecuta. `--dry` deja de ser un `if` repetido en cada rama, el planificador se puede probar
   sin un pack real, y la TUI puede enseñar exactamente lo que va a pasar antes de que pase.
2. **Cuatro enganches de extensión** (`Analyzer`, `SideAdvisor`, `Reporter`, `Source`) con
   descubrimiento por entry points, de modo que un módulo futuro —otro escáner, un detector de lado
   distinto, otra fuente— no obligue a tocar el núcleo y pueda incluso vivir en otro paquete.

## Alternativas descartadas

- **Dejarlo como script y parametrizar con variables de entorno.** Más barato, pero no arregla lo
  de fondo: seguiría sin poder probarse sin un pack, y cada regla nueva seguiría siendo una edición
  del mismo archivo.
- **Reescribir en Go para tener un binario único.** El criterio de distribución no aplica aquí: la
  herramienta la usa su autor y sus agentes, en máquinas con Python. Tirar 1.235 líneas que
  funcionan para ganar un binario que nadie necesita no se sostiene.
- **TUI primero.** Una TUI sin CLI debajo es una trampa para la automatización, y aquí el consumidor
  principal es un agente.

## Consecuencias

- El índice existente sigue valiendo: el repositorio conserva los campos que no modela, verificado
  sobre 191 entradas reales sin perder ni cambiar nada.
- Hay más archivos y más indirección que en un script. Se acepta a cambio de poder probar el núcleo
  sin red ni pack, que es lo que ha destapado tres fallos reales durante la extracción.
- La heurística de lado deja de ser una función que decide y pasa a ser varios asesores que votan
  con su motivo. Coincide con la decisión humana alrededor de la mitad de las veces, así que decidir
  sola nunca fue una opción defendible.
