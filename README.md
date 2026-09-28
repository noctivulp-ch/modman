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
modman --help
```

El núcleo y la CLI **no tienen dependencias**: se usan en repos ajenos y en CI, donde cada
dependencia es una razón más para que no arranque. La TUI sí necesita Textual:

```bash
uv tool install --from /ruta/a/modman "modman[tui]"
```

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
| `Source` | de dónde salen los jars | Modrinth |

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

## Estado

En desarrollo. El núcleo está y verificado contra un pack real de 191 mods; los frontends están a
medias. Ver `TODO.md`.
