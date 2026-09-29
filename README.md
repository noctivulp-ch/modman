# modman

Gestión de los mods de un modpack: **un índice en git** que es la fuente de verdad de las
decisiones, y **N destinos** que se sincronizan con él. CLI para agentes y scripts, TUI para
mirarlo con calma. Sin acoplamiento a ningún repositorio: cada pack se describe en su propio
`modman.toml`.

Nació dentro de un repo de servidor concreto (`CNNY-Server/tools/modman`) y se extrajo para poder
usarlo en cualquier pack, con o sin servidor.

## Qué problema resuelve

Un modpack en desarrollo no es una carpeta de jars: es un montón de decisiones que no están
escritas en ninguna parte. Por qué este mod está desactivado, en qué lado hace falta, cuál de los
dos duplicados se quedó, qué versión había antes de que se rompiera algo. Copiar jars a mano pierde
todo eso, y al mes siguiente nadie sabe por qué el pack está como está.

modman guarda la decisión al lado del dato: un TOML por mod, en git, con el lado, el estado, el
grupo de duplicados y **el motivo**. Los jars se deducen de ahí, no al revés.

## Instalación

```bash
uv tool install --from /ruta/a/modman modman        # o: pipx install /ruta/a/modman
modman help                                          # referencia completa
```

El núcleo y la CLI **no tienen dependencias**: se usan en repos ajenos y en CI, donde cada
dependencia es una razón más para que no arranque. La TUI sí necesita Textual:

```bash
uv tool install --from /ruta/a/modman "modman[tui]"
modman-tui
```

## Para agentes y automatización

`-h`/`--help` está en el comando raíz y en cada subcomando, con la descripción, **qué escribe** y
ejemplos reales. Además:

```bash
modman help                 # referencia completa, separando lo que solo lee de lo que escribe
modman help deploy          # detalle de un comando
modman --json help          # la especificación entera, en JSON
```

Esa última es la pensada para automatizar: cada comando trae `read_only`, `writes`, sus opciones
—si toman valor, si son repetibles, qué valores admiten— y ejemplos. Sirve para saber qué se puede
lanzar sin tocar el pack sin tener que parsear el texto de la ayuda:

```bash
modman --json help | jq '[.commands[] | select(.read_only) | .name]'
```

Los comandos que escriben lo dicen en su propia ayuda (`Escribe: las carpetas de mods de los
destinos`) y llevan `[escribe]` en el listado. `doctor` devuelve código 1 si encuentra problemas,
para encadenarlo en CI.

## Puesta en marcha

```bash
cd /ruta/a/mi-modpack
modman init            # escribe un modman.toml de partida y pregunta si hay servidor
```

El archivo se busca **hacia arriba** desde donde estés, como hace git con `.git`, así que los
comandos funcionan desde cualquier subdirectorio del pack.

## Las tres formas de gestionar un pack

### 1. Solo cliente (un modpack para jugar o repartir)

Un único destino. Es el caso de un pack de CurseForge/Modrinth que mantienes para ti o para un
grupo, sin servidor propio.

```toml
[game]
version = "1.20.1"
loader = "neoforge"

[[targets]]
name = "cliente"
path = "~/.local/share/PrismLauncher/instances/MiPack/minecraft/mods"
accepts = ["client"]
```

Aquí el lado casi siempre será `both` o `client`, y `modman deploy` mantiene esa instancia
alineada con el índice. El valor está en el historial: qué se actualizó, qué se desactivó y por qué.

### 2. Solo servidor (un servidor dedicado, sin tocar clientes)

```toml
[[targets]]
name = "servidor"
path = "server/mods"
accepts = ["server"]
```

Un mod marcado `client` queda registrado en el índice pero **no se despliega**: sigue documentado
para cuando haya un cliente que lo necesite. Esto es lo que evita la trampa clásica de instalar en
el servidor un mod que además hace falta en el cliente y no enterarse hasta que alguien entra.

### 3. Servidor y cliente a la vez (desarrollo de un pack propio)

Dos destinos, y el lado de cada mod decide dónde aterriza.

```toml
[[targets]]
name = "servidor"
path = "server/mods"
accepts = ["server"]

[[targets]]
name = "cliente"
path = "~/.local/share/PrismLauncher/instances/MiPack/minecraft/mods"
accepts = ["client"]
```

Nada impide más de dos: una instancia limpia para exportar y otra privada con utilidades de
prueba son dos destinos con los mismos lados, y `deploy --target limpia` toca solo una.

## Conceptos

**Lado** (`side`): `server` · `client` · `both`. Es una propiedad **del mod**, no del destino: un
mod que registra bloques hace falta en el cliente aunque tu pack solo tenga servidor. Se decide a
mano; los analizadores solo sugieren.

**Estado** (`state`): `on` (jar activo) · `off` (`.jar.disabled`, con la causa en `note`) · `hold`
(duplicado de función pendiente de decidir; se despliega desactivado para poder probarlo).

**Destino** (`target`): un sitio donde acaban los jars, con los lados que acepta.

**Jars sin gestionar**: los que están en un destino pero no en el índice —un mod propio en
desarrollo, algo copiado a mano—. Se informan y **nunca se tocan**. Si uno declara el mismo modid
que un mod del índice, ese mod no se despliega y se avisa: dos jars con el mismo modid tumban el
juego.

## Shaders y paquetes de texturas

Un pack no es solo `mods/`. Los gestores de los launchers tratan `shaderpacks/` y
`resourcepacks/` como carpetas de archivos sueltos: no saben qué versión del juego espera cada
uno ni de qué dependen. Aquí son un tipo más (`kind`), con su propio destino:

```toml
[[targets]]
name = "shaders"
path = "minecraft/shaderpacks"
accepts = ["client"]
kinds = ["shader"]          # sin esto, un destino solo acepta mods
```

```bash
modman add complementary-reimagined --kind shader --version r5.9.3
```

Dos diferencias con un mod, y las dos vienen de cómo las lee el juego: un shaderpack o un
paquete de texturas **desactivado no se copia** —`.disabled` solo lo entiende el cargador de
mods; en `shaderpacks/` sería un archivo raro a la vista—, y al buscar en el destino se miran
`*.zip` y no `*.jar`.

### Dependencias que nadie declara

El motivo real de todo esto. Las dependencias entre mods, shaders y texturas **no están en
ningún metadato**, así que ninguna herramienta las comprueba y todas fallan en silencio: el
juego arranca, y lo que no funciona se descubre mirando. Las cuatro formas que tienen:

| Caso | Cómo se declara |
|---|---|
| Un mod necesita **una versión exacta** de un shader — EuphoriaPatcher parchea *ese* Complementary | `--kind shader --version r5.9.3` |
| Un shaderpack necesita **un cargador**, Iris **u** Oculus, de cierta versión en adelante | `--add iris --or oculus --min-version 1.7` |
| Un paquete de texturas necesita un mod para que **parte** de su contenido haga algo (Continuity o CTM para las conectadas, ETF/EMF para las emisivas, CIT Resewn para los ítems) | `--add continuity --or ctm --level partial` |
| Un mod necesita otro y no lo declara | `--add otromod` |

```bash
modman requires euphoria-patches --add complementary-reimagined --kind shader \
        --version r5.9.3 --reason "parchea esa versión exacta; con otra no aplica nada"
modman requires                       # ver todas las declaradas
modman requires faithful --suggest    # mirar dentro del zip y proponer
modman doctor                         # comprobarlas
```

`--level partial` existe porque no todas rompen igual: Faithful sin Continuity **carga y se
ve**, solo pierde las texturas conectadas. Sale como aviso y no cuenta para el código de salida,
o `doctor` fallaría en CI por algo cosmético y se dejaría de mirar.

### Cuando los requisitos son un menú, no una lista

El caso extremo, y el que obliga a modelarlo bien: *Glowing Emissive Ores* no necesita
Continuity. Necesita **una de doce combinaciones**, cada una válida para un loader y un rango de
versiones, algunas con alternativas dentro (`Sodium` **o** `Embeddium`), otras con piezas
recomendadas pero no obligatorias, y una que el propio autor desaconseja. Elegir dos a la vez es
peor que ninguna. Un gestor de launcher no sólo no comprueba esto: no tiene dónde escribirlo.

```bash
modman requires glowing-emissive-ores --discover --save   # de dónde salen las opciones
modman requires glowing-emissive-ores --options           # resueltas contra ESTE pack
modman requires glowing-emissive-ores --choose neoforge-3  # fijar la del pack
```

```
glowing-emissive-ores — requisitos  [elegida: neoforge-3]
  ✓ neoforge-3: cubierta por glowing-emissive-ores-fusion, fusion-connected-textures ←
    neoforge-1: faltan neocontinuity
    neoforge-2: faltan continuity, connector; sin indexar pero parece puesto: sodium ≈ sodium-neoforge-0.8.13.jar
  · fabric-1: no aplica a este pack
  …
```

De las doce, tres aplican a NeoForge 1.21.1, y se ordenan **por lo que te falta de cada una**:
la decisión real no es «¿sirve?» sino «¿cuál me cuesta menos?».

**Un pack heredado no está indexado entero**, así que se miran también los jars que hay en los
destinos sin estar en el índice. Eso no se da por cubierto —el slug de un proyecto y el modid de
su jar no siempre coinciden— pero se enseña el jar como prueba y `doctor` lo cuenta como «no lo
sé», no como error: tratarlo como error daría rojo en cualquier pack real y el aviso se
aprendería a ignorar.

### De dónde salen las opciones: tres niveles, no uno

Esto es lo que hace que sirva para cualquier paquete y no solo para el que lo motivó. De cinco
paquetes reales mirados, **uno** documenta sus combinaciones: `faithful-32x` no menciona un solo
requisito y `complementary-reimagined` lo despacha en una frase. Así que la detección va de lo
que siempre está a lo que casi nunca está:

1. **Metadatos y contenido del archivo** (`core/features.py`). Un shaderpack declara en Modrinth
   para qué cargadores es (`iris`, `optifine`, `canvas`); un `.zip` de texturas lleva dentro las
   carpetas de OptiFine que usa. La correspondencia *capacidad → mod que la implementa, por
   loader* la tiene modman en una tabla, no la página: cuando aparezca otro mod de texturas
   conectadas se añade una fila y lo hereda el catálogo entero.
2. **La descripción del proyecto** (`core/reqparse.py`). Si el autor publica sus combinaciones,
   ganan: es él quien sabe con qué lo ha probado. Reconoce tanto `### Option N (versiones)` bajo
   `<loader> Requirements` como la forma corriente de `# Requirements` + lista.
3. Si la descripción no aporta nada aplicable a este pack, se usan las del paso 1.

El paso 2 es un parser de prosa y **se equivoca**, así que `--discover` no escribe sin `--save`.
Marca lo que no cuadra en lugar de callarlo: en la propia página de GEO hay un enlace escrito
«Continuity» que apunta a *connectedness*, y sale como `[revisar: el enlace de «Continuity»
apunta a otro proyecto]`.

### `modman shaders`: ¿tus shaderpacks van con el cargador que tienes?

Iris define unas *feature flags* y cada shaderpack declara cuáles necesita:

```properties
iris.features.required = CUSTOM_IMAGES SSBO COMPUTE_SHADERS
```

Si el cargador no implementa una, el shader **no carga**: sale una pantalla de error al
seleccionarlo y en el log no queda nada que lo relacione con la versión del cargador. Es la duda
constante de quien usa **Oculus**, el puerto de Iris a Forge, que va por detrás.

```bash
modman shaders
```

```
cargador: oculus-mc1.20.1-1.8.0.jar
implementa: BLOCK_EMISSION_ATTRIBUTE, COMPUTE_SHADERS, CUSTOM_IMAGES, ENTITY_TRANSLUCENT, …

✓  rethinking-voxels_r0.1-beta9.zip   COMPUTE_SHADERS, CUSTOM_IMAGES, SSBO
—  Mellow v3.4.1a.zip                 no exige capacidades concretas
```

**La lista de capacidades se lee del jar del cargador**, del enum `FeatureFlags` que Iris y
Oculus comparten, no de una tabla en el código: una tabla envejecería con cada versión y daría
veredictos falsos sobre lo único que se quiere saber con certeza. Y se mira la carpeta, no el
índice, porque los shaderpacks de una instancia heredada casi nunca están indexados.

### `pack_format`: el «incompatible» del menú

Cada versión del juego espera un número de formato en el `pack.mcmeta` del paquete. Cuando no
coincide, Minecraft lo lista bajo **Incompatible** y lo carga igual —casi siempre bien—. Eso
convierte «se me ve rara una textura» en algo imposible de atribuir. `modman scan` lee ese
número y `doctor` lo compara con la versión del pack:

```
faithful: pack_format 15 (es de 1.20, 1.20.1) y 1.21.1 espera 34
          → el juego lo marcará como incompatible
```

La tabla de formatos vive en `core/packmeta.py` y se actualiza a mano con cada versión de
Minecraft. Lo que no esté en ella **no se juzga**: un veredicto inventado es peor que no
comprobar nada.

## Arquitectura

Tres capas, y las dos de interfaz son intercambiables:

```
core/     núcleo sin interfaz: dominio, configuración, índice, fuentes, despliegue, extensiones
cli/      frontend de línea de comandos (salida plana y --json)
tui/      frontend de pantalla completa (Textual)
```

El núcleo no importa nada de terminal y no llama a `sys.exit`: lanza excepciones y decide el
frontend. Por eso un fallo de red corta la CLI pero solo pinta un aviso en la TUI.

`deploy` **planifica** antes de actuar: devuelve una lista de pasos que la CLI imprime con `--dry`
y la TUI enseña para confirmar. Planificar no toca el disco, así que se puede probar sin un pack.

### Módulos: cómo se le añaden capacidades

Cuatro enganches, con contrato explícito. Ver `docs/arquitectura/extensiones.md`.

| Enganche | Aporta | De fábrica |
|---|---|---|
| `Analyzer` | hechos observados de un jar | `jarscan` |
| `SideAdvisor` | una opinión sobre el lado, con su motivo y confianza | 4 reglas |
| `Reporter` | documentos generados desde el índice | catálogo |
| `Source` | de dónde salen los jars | Modrinth y CurseForge |

Dos decisiones que sostienen esto:

- **Los analizadores aportan, no pisan.** Cada uno escribe bajo su propia clave, así que añadir
  uno no cambia lo que ven los demás.
- **Los asesores de lado votan.** Devuelven sugerencia + motivo + confianza; decide el usuario
  leyendo los motivos. Medido contra un índice real de 191 mods decididos a mano, la heurística
  coincide con la decisión humana **en torno a la mitad de las veces**: suficiente para orientar,
  ni de lejos para decidir sola. Un detector nuevo entra como un voto más sin tener que ganarle a
  los anteriores.

Un paquete externo se engancha sin que modman lo conozca:

```toml
[project.entry-points."modman.analyzers"]
mixins = "mi_paquete.analizadores:MixinAnalyzer"
```

## La TUI

```bash
modman-tui
```

Tabla de mods filtrable a la izquierda, detalle a la derecha con las dependencias y quién las
provee. Atajos: `/` filtrar · `s` alternar on/off · `f` cambiar lado · `d` desplegar · `o` revisar
dependencias · `r` recargar · `q` salir.

Editar es inmediato; **desplegar pide confirmación** enseñando el plan exacto, porque es lo único
que toca carpetas que el juego va a leer. La copia se hace en un hilo aparte para que la interfaz
no se congele con doscientos jars.

No tiene lógica propia: llama al mismo `core.manager` que la CLI. Si decidiera algo por su cuenta,
el pack acabaría con dos verdades.

## CurseForge

Media biblioteca de mods no está en Modrinth, así que hay una segunda fuente. **Necesita una API
key gratuita** de [console.curseforge.com](https://console.curseforge.com) (sección «API Keys»);
sin ella la API devuelve 403 y modman simplemente no registra la fuente:

```bash
mkdir -p ~/.config/modman && echo '<tu-key>' > ~/.config/modman/curseforge.key
# o: export CURSEFORGE_API_KEY=<tu-key>
```

Hay un tercer camino, desactivado por defecto: reutilizar la key que trae embebida el launcher
instalado (`[curseforge] use_launcher_key = true`). Funciona, pero esa key está emitida a nombre de
esa aplicación, así que la decisión es de quien ejecuta y no de la herramienta.

Dos diferencias con Modrinth que el código ya contempla: CurseForge publica **SHA-1** y no SHA-512
—`RemoteFile` guarda los dos y verifica con el que haya—, y algunos autores **prohíben la descarga
por terceros**, en cuyo caso `downloadUrl` viene vacío y el error lo dice en lugar de fallar con un
404 sin explicación.

## El ciclo de decisión

`catalog` no genera solo documentación: genera **la interfaz para decidir**. Las hojas de
duplicados y de caídos llevan casillas de tarea, clicables en Obsidian y en GitHub:

```bash
modman catalog        # escribe los documentos… y antes lee las casillas marcadas
# (marcar prefiero/descarte en mods-duplicados.md)
modman catalog        # recoge las marcas y las guarda en el índice
modman apply-picks    # prefiero → on, descarte → off
modman deploy
```

El orden importa: se lee **antes** de regenerar, o cada regeneración borraría lo que se acaba de
marcar. `apply-picks` no enciende un mod marcado como roto ni uno que dependa de una librería
rota; encenderlo solo consigue que el servidor no arranque y que cueste ver por qué.

Para un jar reconstruido con un parche propio:

```bash
modman patched tide ./build/tide-2.1.1-cnny.jar --note "sin EntityRenderDispatcherMixin"
```

A partir de ahí ese jar gana al original en cada despliegue, y se guarda su hash: si falta o
cambia, el despliegue **se niega** en vez de poner el original y deshacer el parche en silencio.

## La skill

La metodología de gestión —updates, integrar mods nuevos, rescatar los problemáticos, campañas de
activación por olas— está en [`skills/mod-ops/SKILL.md`](skills/mod-ops/SKILL.md), **en este repo**
y no en una copia dentro de `~/.claude`:

```bash
./skills/install.sh            # enlaza en ~/.claude; el repo queda como fuente de verdad
./skills/install.sh --status
```

Con enlace y no copia porque ya pasó lo contrario: esa skill llegó a describir un modman de dos
generaciones atrás, porque la copia del sistema y la herramienta evolucionaron por separado. Y por
eso la skill tampoco lleva la lista de comandos escrita dentro: remite a `modman --json help`, que
se genera de la especificación real.

## Estado

Núcleo, CLI y TUI funcionando, con los 19 comandos del original más `requires` y `shaders`. Verificado contra
un pack real de 191 mods y contra uno de 340 con shaders y texturas. 76 tests. Faltan la documentación en `docs/` y migrar el repo de origen. Ver `TODO.md`.
