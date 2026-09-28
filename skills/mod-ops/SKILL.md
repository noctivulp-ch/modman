---
name: mod-ops
description: >
  Metodología completa para gestionar mods de un modpack NeoForge en estado dev:
  actualizar mods, integrar/activar mods nuevos, y rescatar mods problemáticos
  (fix propio por mixin, update, o documentar como unfixed para bug report).
  Incluye el montaje del entorno de gestión (modman como herramienta externa + logs)
  en un modpack nuevo, y campañas de activación por olas hacia el "modpack full"
  (mapeo de dependencias y perfil modpack-debug). Usar siempre que el usuario
  pida: actualizar el modpack, "integra/activa este mod", "arregla los mods problemáticos",
  "revisa updates", "activa el modpack full", "ejecuta la ola N", "monta la gestión de
  mods en X", o cualquier campaña sobre lib/mods.
  Triggers: /mod-ops, "campaña de mods", "mod problemático", "update de mods", "ola de activación".
---

# mod-ops: gestión y rescate de mods en modpack dev

Metodología destilada de las campañas DeceasedCraft 2026-07 (rescate de problemáticos:
7 rescatados / 3 unfixed; + Ola 1 de activación: 32/33 verdes). Aplica a cualquier
modpack NeoForge en desarrollo con la infraestructura descrita abajo.

---

## El entorno (qué existe y dónde)

| Pieza | Dónde | Qué es |
|---|---|---|
| **CLI de gestión** | **`modman`** (herramienta externa, no vive en el repo del pack) | Índice, despliegue, updates, catálogo. `modman-tui` es su interfaz de pantalla completa |
| Índice del pack | `pack/index/<slug>.toml`, **en git** | La fuente de verdad: lado, estado, grupo, versión y **el motivo** de cada decisión |
| Jars | `pack/cache/` (fuera de git) | Se descargan del índice; nunca se copian a mano |
| Jars parcheados | `pack/patched/` + receta en `pack/patches/<slug>.md` | Registrados con `modman patched`; ganan al original |
| Destinos | los que declare `modman.toml` | Uno (pack sin servidor), dos (servidor + cliente) o varios |
| Historial de fixes | `pack/log/fixlog.md` | Cada mod tratado: problema, vía, verificación |
| Lista unfixed | `pack/log/unfixed.md` | Solo infixeables — datos completos para el bug report que presenta el usuario |
| Historial de versiones | `pack/log/update-ledger.md` + copias en `pre-update/` | Lo escribe `modman update`; `modman rollback` lo deshace |

> **No memorices los comandos: pregúntale a la herramienta.**
> ```bash
> modman help                 # referencia completa, separando lo que solo lee de lo que escribe
> modman --json help          # la especificación: read_only, writes, opciones y ejemplos
> ```
> Esta skill ha quedado desfasada dos veces por llevar la lista de comandos escrita dentro. La
> herramienta la genera de su propia especificación, así que ahí no puede mentir.

**Regla de sincronía:** el índice refleja SIEMPRE lo que se quiere en los destinos, y los destinos
se derivan de él con `modman deploy` — nunca al revés. `check` no lo toca; `update` adelanta el
índice y el jar juntos. El diff de `pack/index/` es el historial del pack.

**Jars que modman no gestiona** (un mod propio en desarrollo, algo copiado a mano): `status` los
lista y `deploy` **jamás los toca**. Si uno declara el mismo modid que un mod del índice, ese mod no
se despliega y se avisa: dos jars con el mismo modid tumban el juego.

---

## Bootstrap en un modpack similar (incrustar el entorno)

Para replicar esta gestión en otra instancia dev con instancia Prism hermana:

```bash
# 1. Instalar la herramienta (una vez por máquina)
uv tool install --force --with textual \
    /ruta/a/modman

# 2. Declarar el pack. Escribe modman.toml; --client-only si no hay servidor
cd <repo-del-pack>
modman init . --mc <version> --loader neoforge
$EDITOR modman.toml        # revisar las rutas de los destinos

# 3. Comprobar que los destinos existen antes de nada
modman targets

# 4. Sembrar el índice desde lo que ya hay instalado
modman add <slug> <slug> …   # arrastra las dependencias obligatorias
modman libs                  # fija lado y estado de las librerías según quién las usa
modman status                # los jars sin metadata salen listados: decidir uno a uno

# 5. mkdir -p pack/log && crear fixlog.md + unfixed.md (cabeceras de este skill)
# 6. Sección en el CLAUDE.md del pack apuntando a modman y a este skill
```

`pack/index/` va a git; `pack/cache/` y `pack/patched/`, no.

---

## Campaña de activación por olas (llevar un pack a "modpack full")

Cuando el objetivo es activar MUCHOS mods desactivados, no ir uno por uno a ciegas:

1. **Mapear primero.** `modman scan` abre todos los jars —`mods.toml` externo **e interno
   (jarjar)** y `fabric.mod.json`— y guarda lo que encuentra en el índice: modids que provee,
   dependencias, número de mixin configs, si se declara solo-cliente, si registra canal de red.
   `modman deps <slug>` dice quién provee cada dependencia y `modman doctor` lista las que faltan
   por lado. Con `--json` sale todo estructurado para puntuar riesgo.
2. **Ordenar de menos a más riesgo** en olas: integraciones/informativos (JEI, QOL) →
   contenido/registros → cliente/render/UI (mucha superficie de mixins) → stacks
   delicados uno a uno → capas Fabric/Connector al final. Bloqueados conocidos y
   dependencias fuera del pack van a un "parking", no a una ola.
3. **Perfil `--modpack-debug`** (separado de `--modpack-progress`, que es el estado real
   del pack que NO se toca durante la iteración): carga los `.jar` activos MÁS la lista
   curada `lib/profiles/debug-mods.txt`, cuyos jars se cargan **aunque sigan `.disabled`
   en disco**. Iterar una ola = editar esa lista de texto y rebootear; el estado de
   `lib/mods` (y con él modpack-progress) nunca cambia. Comentar un candidato con `#` =
   sacarlo de la ola sin perder la anotación de por qué.
4. Un boot limpio por ola (+ los de bisección). El culpable de cada ola pasa por las 3
   vías de abajo; los inocentes se reintegran y se re-confirma verde con TODOS juntos.

> El perfil se implementa en `build.gradle` (case `modpack-debug`, lee debug-mods.txt y
> resuelve cada nombre a `.jar` o `.jar.disabled`) + un flag en `run-test.sh`. Si el pack
> destino no lo tiene, crearlo es parte del bootstrap de la campaña.

---

## Proceso por mod problemático — las 3 vías, EN ESTE ORDEN

```
1. DIAGNOSTICAR   repro real > hipótesis archivada. Los registros viejos del vault
                  pueden estar mal (carpet: "NaturalSpawnerMixin" era falso; cbc_at
                  cargaba la culpa de un crash que era de AdvancementPlaques). Capturar
                  stack completo antes de decidir. Si el crash apunta a OTRO mod (p.ej.
                  "createbigcannons ... already exists in tab"), el culpable suele ser un
                  tercero que altera ese sistema — bisecar, no acusar al del stack.
2. ¿UPDATE?       modman check <slug>. Si hay versión nueva → modman update <slug> + verificar.
                  (update deja copia previa; modman rollback <slug> la deshace.)
3. ¿FIX DC?       Sin update (o update falló) → mixin/código propio en DC.
                  Decompilar el target (vineflower) ANTES de escribir el mixin.
4. ¿UNFIXED?      Solo si 2 y 3 se agotaron → entrada en unfixed.md con TODO lo
                  necesario para el bug report (el usuario lo presenta a mano).
```

### Criterios de decisión (aprendidos a golpes)

- **Pre-scan de bytecode antes de bisecar.** Si el crash implica un sistema conocido
  (registro de Create, creative tabs, worldgen), grep los `.class` de los candidatos de
  la ola por la referencia relevante (`com/simibubi/create`, `BuildCreativeModeTabContents`,
  `isDevelopmentEnvironment`) para pre-ordenar sospechosos. En la Ola 1 eso señaló a
  topaddon en 1 ciclo (sin bisección por mitades). Aviso: el culpable puede NO tener la
  referencia (AdvancementPlaques no toca tabs en su bytecode — actúa indirecto vía otra
  lib); el pre-scan acota, no concluye.
- **Bisección empírica > análisis estático.** Cuando el pre-scan no basta, bisecar por
  mitades. Disciplina: nombrar las mitades (A/B, B1/B2…), **veredicto por diff de
  crash-report** (el más reciente antes vs después — NO por `pgrep` tras pulsar E: el
  proceso tarda segundos en morir y da falsos "vivo"), y **reintegrar los inocentes** en
  el siguiente ciclo para reconfirmar. Un boot de 5 min responde lo que una hora de
  análisis no.
- **Combinar mods en un boot SOLO si sus firmas de fallo son distinguibles** (mcpitanlib
  falla en construcción, carpet en clinit, cbc_at en registro — un boot, tres respuestas).
  Si dos fallos pueden confundirse, rondas separadas.
- **Corregir la atribución falsa.** Si la bisección exonera a un mod que estaba culpado
  (en unfixed.md, en el vault o en un histórico), ir a corregir ese registro — un cargo
  falso hace que alguien reintente el fix equivocado meses después.
- **Cuidado con fallos colaterales:** un mod que aborta la carga (constructor throw) hace
  fallar lookups de ModList en OTROS mods. Arreglar el primero y re-evaluar el resto.
- **Presupuesto de cadena:** si un update arrastra dependencias (CBC→aeronautics→sable→
  sodium), evaluar el blast radius contra el objetivo original. Si excede (p.ej. rescatar
  un addon menor exige migrar el motor de render), **REVERTIR TODO** y anotar la cadena
  como tarea futura propia. Revertir = `modman rollback <slug>` (usa la copia previa del
  historial), o `git checkout pack/index/<slug>.toml` + `modman deploy` para volver a un estado
  cualquiera del índice.
- **Un cambio de riesgo por ronda.** No mezclar updates de cadena con activaciones nuevas.
- **Parchear jars: prohibido por defecto** (complica distribución). Excepciones: fallos en la fase
  de bootstrap de Mixin (antes de que ningún mixin pueda aplicar, p.ej. un mixin config declarado
  sin recurso) y mixins con `"required": true` que matan el arranque sin alternativa. En ese caso:
  **un script reproducible** en `tools/patches/<mod>-<qué-hace>.sh` que reconstruya el jar, y luego
  `modman patched <slug> <jar> --note "<qué se quitó>"`. modman guarda el hash del jar parcheado y
  a partir de ahí **gana al original en cada despliegue**; si falta o cambia, el despliegue se
  niega en vez de poner el original y deshacer el parche en silencio. Documentar en fixlog y dejarlo
  como candidato a bug report. Tras cada `update` hay que rehacer el parche y volver a registrarlo.
- **"Funciona en prod, falla en dev" es un patrón real:** guards de
  `isDevelopmentEnvironment()` (mcpitanlib construía su ExampleMod de test solo en dev)
  y jars **wrapper+jarjar** (CrashAssistant, sodium 0.8+) que el classpath-loading de
  dev no descubre. Preguntarse siempre: ¿este fallo existiría en Prism?
- **Patrón "constructor toca Create → registro corrupto":** un mod que en su constructor
  (o en un static init) class-loadea providers/clases de Create/Registrate lo hace EN
  PARALELO con la init de Create → su registro de items queda a medias → NPE tardío
  "Trying to access unbound value: create:<item>" en el RegisterEvent de TRIGGER_TYPES.
  Visto en cbc_at y topaddon. Fix DC: `@Inject` HEAD cancelable en su método de registro
  temprano + re-invocarlo diferido en `FMLCommonSetupEvent.enqueueWork` (patrón
  TOPHandlerMixin — re-entrada por flag `dead$allowed`, re-invocación por reflexión para
  no necesitar el jar en compileOnly).
- **Mixins a clases de otros mods:** si el target carga en fase de plugin/bootstrap →
  `MixinTargetAlreadyLoadedException` → mixear en su lugar la clase TARDÍA que hace la
  llamada (envolver el call-site en el `<clinit>` con @WrapOperation, patrón
  TranslationCheckerMixin). `targets = "paquete.Clase"` + `remap = false` para clases
  de mods; prefijo `dead$`/`dc$` en los handlers.

---

## Verificación estándar (criterio de "funciona")

1. **Boot a título:** lanzar `run-test.sh`, monitorear con until-loop sobre
   `run/logs/latest.log` + `/tmp/dc_gradle_stdout.log` buscando
   `Sound engine started|FAILED|MixinApplyError|Exception in thread|Caught exception during event`.
2. **Mixins propios aplicados:** `grep "Mixing.*<MiMixin>" /tmp/dc_gradle_stdout.log`.
   Ojo: la clase target puede cargar tarde (crear mundo la fuerza si es de worldgen).
3. **Mundo nuevo** vía MCP: `mc.world.join {"name":"TT-<tag>","gamemode":"creative","cheats":true}`
   → esperar `"inWorld":true` (patrón estricto — `playerName:null` contiene "player").
4. **Trigger específico del bug:** si el fallo era al abrir inventario, abrirlo
   (`mc.keyboard.press key="69"` — keycodes como string por el gotcha del puente MCP).
   Nota: los crashes de **build de creative tabs** NO necesitan abrir el inventario — el
   pre-index de JEI al entrar al mundo ya construye los tabs y los dispara. Un mundo que
   carga limpio con JEI activo ya descarta ese tipo de fallo.
5. **Stall (no crash):** si el mundo no carga y no hay crash report →
   `jstack $(pgrep -f 'fml[.]modFolders')` → mirar "Server thread" y "Worker-Main-*".
   Workers parked + server esperando chunk = stall real, no lentitud.

---

## Artefactos obligatorios al cerrar

- **fixlog.md** — entrada por mod: problema (con stack/causa), vía elegida, fix exacto,
  estado de verificación. Al final de campaña: tabla-resumen mod/vía/estado.
- **unfixed.md** — solo infixeables: entorno exacto (versiones), síntoma con stack,
  bisección que lo confirma, vías agotadas y por qué, estado del jar. Debe bastar para
  redactar el bug report sin re-investigar.
- **Commit** con la lista de fixes y unfixed en el cuerpo; **dev-log** al vault;
  actualizar ToDo del vault si un pendiente cambió de diagnóstico.

---

## Gotchas operativos (perderás horas si los ignoras)

- `pkill -f "patrón"`: si el patrón aparece literal en tu propia línea de comando, te
  matas a ti mismo. Usar clases de caracteres: `pkill -f 'fml[.]modFolders'`.
- El cwd del shell PERSISTE entre comandos y se resetea a veces — usar rutas absolutas
  o `cd <raíz> && ...` antes de `./run-test.sh` (lanzarlo desde otro cwd falla mudo).
- Monitores background: lanzar el boot y el until-loop en el MISMO comando; un monitor
  lanzado aparte puede leer logs viejos (rotación tardía de latest.log).
- **Los background tasks largos se matan (~10 min de tope en este entorno.)** Para
  campañas de muchos ciclos: lanzar el cliente con `nohup ./run-test.sh … & disown`
  (sobrevive) y usar monitores CORTOS y encadenados en vez de un until-loop gigante;
  guardar el veredicto en el output file. Si un task se mata a media generación de mundo,
  el cliente sigue vivo — reconectar por MCP, no relanzar.
- Errores de mixin van a stderr → `/tmp/dc_gradle_stdout.log`, NO a `run/logs/`.
- `AppenderLoggingException` en consola y los ERROR de ModernFix stitcher son ruido
  preexistente — no son señal de fallo.
- Cerrar el cliente al terminar: `save-all flush` vía MCP → `kill` del PID. No dejar
  clientes de 8GB corriendo entre rondas.
- Al terminar, parar los monitores background que queden vivos (TaskStop).
