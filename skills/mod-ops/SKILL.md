---
name: mod-ops
description: >
  Metodología para gestionar y reparar un modpack: actualizar mods, integrar o activar mods
  nuevos, rescatar los problemáticos (fix propio por mixin, update, o documentarlo como
  infixeable para el bug report), y **recuperar un pack que funcionaba y se rompió tras una
  actualización**. Cubre tres escenarios —modpack con desarrollo estilo DCPort (proyecto Gradle
  con perfil de depuración), instancia normal de launcher (Prism, CurseForge, Modrinth), y un
  pack ajeno sin nada montado— y cómo montar el entorno en cada uno, empezando siempre por
  asegurar la vuelta atrás con git o equivalente. Incluye campañas de activación por olas y
  bisección disciplinada. Usar siempre que el usuario pida: actualizar el modpack, "integra/activa
  este mod", "arregla los mods problemáticos", "revisa updates", "activa el modpack full",
  "ejecuta la ola N", "monta la gestión de mods en X", "se me rompió el modpack", "dejó de
  arrancar tras actualizar", o cualquier campaña sobre los mods de un pack.
  Triggers: /mod-ops, "campaña de mods", "mod problemático", "update de mods", "ola de
  activación", "modpack roto", "no arranca el modpack", "reparar modpack", "bisecar mods".
---

# mod-ops: gestionar, rescatar y reparar los mods de un modpack

Metodología destilada de las campañas DeceasedCraft 2026-07 (rescate de problemáticos:
7 rescatados / 3 unfixed; + Ola 1 de activación: 32/33 verdes) y del trabajo posterior en
CNNY-Server. Aplica a **cualquier** modpack —NeoForge, Forge o Fabric; en desarrollo o
simplemente instalado— con los tres escenarios de montaje descritos abajo.

**Lo primero, siempre:** asegurar que se puede volver atrás. Reparar es prueba y error, y el error
es destructivo. Ver la sección siguiente antes de desactivar el primer mod.

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

## Antes de tocar nada: poder volver atrás

Reparar un modpack es prueba y error, y el error **es destructivo**: desactivas ocho mods, el
arranque cambia, y a la tercera ronda ya no recuerdas cuál estaba activado antes ni con qué versión.
Sin poder volver atrás, cada prueba fallida te deja peor que al empezar, y el pack acaba en un
estado que no es ni el roto original ni uno que funcione.

**Antes de desactivar el primer mod, asegura la vuelta atrás.** No es ceremonia: es lo que hace que
bisecar sea barato. Si volver al estado anterior cuesta dos minutos, pruebas sin miedo; si cuesta
una tarde, empiezas a "probar con cuidado", que es como no probar.

### Qué versionar y qué no

| Versionar | Ignorar |
|---|---|
| El **índice** (`pack/index/*.toml`): qué mod, qué versión, qué lado, qué estado y **por qué** | Los **jars**: pesan, y se vuelven a descargar desde el índice |
| `config/`, `kubejs/`, `defaultconfigs/`, datapacks | `logs/`, `crash-reports/` (útiles, pero no en git) |
| La lista de mods del launcher (`mods/.index/*.pw.toml` en Prism/packwiz) | El **mundo** (`saves/`): binario y enorme; va aparte, con copia normal |
| `options.txt` y los keybinds si te importan | Cachés (`.mixin.out/`, `libraries/`, `assets/`) |

Esa es la "copia de seguridad inteligente": **versionar la metadata, no los binarios**. Un repo de
un modpack de 300 mods así ocupa unos pocos megas y te deja hacer `git diff` para ver exactamente
qué cambió entre el pack que funcionaba y el que no — que es la pregunta que estás intentando
responder.

### Si el pack no tiene control de versiones (el caso normal)

Empieza por ahí, aunque sea un modpack que bajaste hecho. Cinco minutos:

```bash
cd <carpeta del pack>          # la que tiene mods/, config/, options.txt
git init
cat > .gitignore <<'EOF'
# Binarios y cachés: se regeneran
mods/*.jar
mods/*.jar.disabled
libraries/
assets/
versions/
.mixin.out/
crash-reports/
logs/
saves/
resourcepacks/
shaderpacks/
EOF
git add -A && git commit -m "estado de partida: el pack tal y como está roto"
```

Ese primer commit es el más importante de todos: es **el estado roto**, y vas a querer volver a él
más de una vez para comparar. Etiquétalo: `git tag roto-original`.

A partir de ahí, un commit por ronda de pruebas (`git commit -am "ola 2: off los 6 de render"`). El
historial se convierte en el registro de qué probaste, que si no acabas anotando a mano y peor.

### Si además no tienes el pack anterior que sí funcionaba

Es lo habitual tras una actualización que rompió algo. Búscalo antes de rendirte:

- **El launcher suele guardar copias.** Prism hace copia de la instancia al actualizar un modpack;
  CurseForge y Modrinth mantienen versiones anteriores del pack descargables. Recuperar el
  `mods/.index/` o el `manifest.json` de la versión que funcionaba te da **la lista exacta de
  versiones buenas**, que es media reparación hecha.
- **El propio `.minecraft` tiene pistas**: `logs/` con los arranques buenos (los mods y versiones
  salen en la cabecera del log) y `crash-reports/` con el primero que falló, que acota la fecha.
- Si lo encuentras, **commitea también ese estado** en una rama (`git checkout -b funcionaba`) y
  entonces `git diff` entre las dos ramas te dice literalmente qué cambió.

### Alternativas si git no encaja

Git es lo recomendable porque el diff es lo que responde la pregunta, pero cualquier cosa que
permita volver atrás sirve más que nada:

- **modman** ya versiona por su cuenta lo suyo: `pack/log/` guarda copia del índice antes de cada
  cambio de versión, y `modman rollback <slug>` deshace el último update. No sustituye a git para
  configs, pero cubre el mod a mod.
- **packwiz** si el pack ya lo usa: su metadata (`*.pw.toml`) es texto y va a git igual de bien.
- **Snapshots del sistema de archivos** (btrfs, ZFS, Timeshift) si tienes: baratísimos y cubren
  todo, incluido el mundo. Útiles como red de seguridad **además** de git, no en su lugar: un
  snapshot no te deja hacer diff de qué mod cambió.
- **Copia con fecha a pelo** (`cp -r pack pack.2026-09-28-antes-de-tocar`) si no hay nada más. Feo,
  pero un `diff -r` entre dos carpetas también responde preguntas. Acuérdate de borrarlas después.

> Regla: **nunca empieces a desactivar mods en la única copia que existe.** Si el usuario no quiere
> git, pide al menos la copia con fecha antes de la primera prueba, y dilo explícitamente.

---

## Montar el entorno — tres escenarios

Instalar la herramienta, una vez por máquina:

```bash
uv tool install --force --with textual \
    /ruta/a/modman
modman help          # qué comandos hay, y cuáles escriben
```


El flujo de reparación es el mismo; lo que cambia es **cómo iteras**, y eso depende de qué tipo de
pack tienes delante. Identifícalo antes de nada.

### A. Modpack con desarrollo estilo DCPort (proyecto Gradle)

El pack es un repo con `build.gradle`, los jars en `lib/mods/` y un runner (`./run-test.sh`). Es el
caso cómodo: puedes iterar **sin tocar el estado real del pack**.

- **Perfil de depuración separado.** `--modpack-debug` carga los `.jar` activos **más** una lista
  curada (`lib/profiles/debug-mods.txt`) cuyos jars se cargan aunque sigan `.disabled` en disco.
  Iterar una ola = editar esa lista de texto y rebootear; `lib/mods/` no cambia, y con él no cambia
  el perfil `--modpack-progress`, que es el estado real.
- Comentar un candidato con `#` lo saca de la ola **sin perder la anotación de por qué**.
- Si el pack destino no tiene ese perfil, crearlo es parte del montaje: un `case` en
  `build.gradle` que lea la lista y resuelva cada nombre a `.jar` o `.jar.disabled`, más un flag en
  el runner.

Ventaja real: una ronda de bisección cuesta editar una línea. Es lo que hace viable partir por
mitades treinta veces.

### B. Modpack normal en una instancia de launcher (Prism, CurseForge, Modrinth)

No hay Gradle ni perfiles: lo que carga el juego es lo que hay en `mods/`. Aquí iteras **moviendo
jars**, así que lo que tiene que ser barato es deshacerlo.

```bash
# Montar modman sobre la instancia (no hace falta que sea un "proyecto")
cd <carpeta de la instancia>        # la que tiene mods/ y config/
git init && git add -A && git commit -m "estado de partida"   # ver la sección anterior
modman init . --mc <version> --loader <neoforge|forge|fabric> --client-only
$EDITOR modman.toml                 # el destino apunta a ./mods
modman targets                      # comprobar que la ruta existe
```

Con el índice sembrado, una ronda es:

```bash
modman set <mod1> state=off note="ola 2: sospechoso de render"
modman set <mod2> state=off note="ola 2"
modman deploy                       # mueve los jars a .jar.disabled
# … arrancar y observar …
git commit -am "ola 2: off render (6)"
```

Y deshacer una ronda entera es `git checkout pack/index/ && modman deploy`.

**Si no quieres ni instalar modman**, el equivalente manual es mover a mano a `mods/.disabled/` y
apuntar qué moviste — pero entonces el "apuntar" lo tienes que hacer tú y es justo lo que se olvida
a la tercera ronda. Con modman la nota viaja en el índice y sale en `modman status`.

### C. El pack no es tuyo, o no hay nada de nada

Un `.zip` que te pasaron, o una instancia de alguien. Antes de tocar: **copia la carpeta entera** y
trabaja sobre la copia. Luego aplica el escenario B sobre ella. Nunca repares en la única copia,
sobre todo si no es tuya.

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
3. **Iterar según el escenario** (ver la sección anterior): en un pack estilo DCPort, editando la
   lista del perfil `--modpack-debug` sin tocar el estado real; en una instancia de launcher, con
   `modman set … state=off` + `modman deploy` y un commit por ronda.
4. Un boot limpio por ola (+ los de bisección). El culpable de cada ola pasa por las 3
   vías de abajo; los inocentes se reintegran y se re-confirma verde con TODOS juntos.

---

## Reparar un pack que funcionaba y se rompió tras una actualización

Distinto de la campaña de arriba: allí se **activa** lo que nunca funcionó, aquí se **recupera** lo
que funcionaba. Y eso cambia la estrategia, porque aquí sí existe un estado bueno conocido.

1. **No empieces desactivando.** Empieza preguntando **qué cambió**. Una actualización que rompe
   algo casi siempre cambió pocas cosas, y la lista de cambios es más barata que cualquier
   bisección:
   - Con git o packwiz: `git diff <tag-bueno> -- pack/index/` o el diff de los `.pw.toml`.
   - Sin nada: compara el `mods/` actual con el de la copia del launcher; `ls` de ambos y `diff`.
     Los nombres de archivo llevan la versión, así que el diff ya dice qué subió.
   - Si no hay copia: la cabecera de un `latest.log` de cuando funcionaba lista los mods cargados
     con su versión. Es feo de leer pero es una lista buena.
2. **Lee el fallo de verdad antes de tocar nada.** Crash report para la excepción, `latest.log`
   para la causa real —lo que dejó "above errors"—, y si el crash nombra a un mod, sospecha del
   tercero que altera ese sistema, no del que sale en la pila. (Skill `minecraft:crash-review` si
   el pack es un port en desarrollo.)
3. **La hipótesis fuerte primero.** Si el diff dice que subieron cuatro mods y el crash habla de
   worldgen, empieza por el de worldgen. Bisecar por mitades es el plan B, no el A: es sistemático
   pero cuesta log₂(n) arranques, y cada arranque de un pack grande son minutos.
4. **Volver atrás mod a mod, no el pack entero.** Revertir solo el sospechoso a su versión anterior
   (`modman update <slug> --version <la buena>`, o `modman rollback <slug>` si el update lo hizo
   modman) aísla la causa. Revertir el pack entero confirma que "algo de la actualización" lo
   rompió, que ya lo sabías.
5. **Si el culpable es una dependencia y no el mod**, `modman doctor` lo dice: una librería que
   subió de versión y dejó de proveer lo que otro esperaba sale como dependencia que falta.
6. **Cuando lo tengas**, decide por las tres vías de abajo (update / fix propio / unfixed) y
   **déjalo escrito** en `fixlog.md`: qué mod, qué versión lo rompió, cuál funciona y por qué. La
   próxima actualización va a tentarte a volver a subirlo.

> **Trampa clásica:** el pack "se rompió tras actualizar" pero lo que cambió fue **Java**, el
> loader (NeoForge/Forge) o el launcher, no los mods. Antes de bisecar 300 jars, comprueba la
> versión de Java con la que arranca y la del loader de la instancia. Un `UnsupportedClassVersion`
> o un `NoSuchMethodError` en clases del loader apuntan ahí y no a ningún mod.

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
