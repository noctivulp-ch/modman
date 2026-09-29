"""La especificación de los comandos, declarada una sola vez.

De aquí sale el parser de `argparse` **y** la referencia que `modman help --json` entrega a un
agente. Tenerlo en dos sitios garantizaba que uno se quedara viejo: la ayuda es lo primero que se
olvida al añadir una opción, y una ayuda que miente es peor que no tenerla.

Lo importante para quien automatiza está en dos campos que `argparse` no contempla:

- **`mutates`**: si el comando escribe algo (índice, destinos, red). Un agente necesita saber qué
  puede lanzar para inspeccionar y qué le va a cambiar el pack, y eso no se deduce del nombre:
  `libs` y `scan` suenan inocentes y los dos escriben en el índice.
- **`examples`**: la invocación real. Una lista de opciones no dice en qué orden se usan, y el
  orden importa: `add` deja las librerías sin lado hasta que corre `libs`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Flag:
    name: str
    help: str
    kind: str = "store_true"  # store_true · value · int · append
    choices: tuple[str, ...] = ()
    default: object = None
    dest: str | None = None


@dataclass(frozen=True)
class Arg:
    name: str
    help: str
    nargs: str | None = None


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    description: str
    mutates: str = ""
    args: tuple[Arg, ...] = ()
    flags: tuple[Flag, ...] = ()
    examples: tuple[str, ...] = ()
    json_output: bool = True

    @property
    def read_only(self) -> bool:
        return not self.mutates

    def epilog(self) -> str:
        partes = []
        if self.mutates:
            partes.append(f"Escribe: {self.mutates}")
        else:
            partes.append("Solo lectura: no toca nada.")
        if self.json_output:
            partes.append("Acepta --json.")
        if self.examples:
            partes.append("Ejemplos:\n  " + "\n  ".join(self.examples))
        return "\n".join(partes)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "help": self.help,
            "description": self.description,
            "read_only": self.read_only,
            "writes": self.mutates or None,
            "json_output": self.json_output,
            "arguments": [{"name": a.name, "help": a.help, "nargs": a.nargs} for a in self.args],
            "flags": [
                {
                    "name": f.name,
                    "help": f.help,
                    "takes_value": f.kind != "store_true",
                    "repeatable": f.kind == "append",
                    "choices": list(f.choices) or None,
                    "default": f.default,
                }
                for f in self.flags
            ],
            "examples": list(self.examples),
        }


_SIDES = ("server", "client", "both")
_STATES = ("on", "off", "hold")

COMMANDS: tuple[Command, ...] = (
    Command(
        "init",
        "crear un modman.toml de partida",
        "Escribe la configuración inicial del pack en el directorio indicado. No sobreescribe una "
        "que ya exista salvo con --force.",
        mutates="modman.toml",
        args=(Arg("dir", "directorio del pack (por defecto, el actual)", nargs="?"),),
        flags=(
            Flag("--mc", "versión de Minecraft", kind="value"),
            Flag("--loader", "neoforge · forge · fabric · quilt", kind="value"),
            Flag("--client-only", "pack sin servidor: un solo destino de cliente"),
            Flag("--force", "reescribir si ya existe"),
        ),
        examples=("modman init .", "modman init . --mc 1.20.1 --loader forge --client-only"),
        json_output=False,
    ),
    Command(
        "status",
        "tabla de mods y su presencia en cada destino",
        "Una fila por mod con su lado, estado, grupo y si el jar está puesto en cada destino. Al "
        "final lista los jars que hay en los destinos pero no en el índice, que nunca se tocan.",
        examples=("modman status", "modman --json status | jq '.mods[] | select(.state==\"off\")'"),
    ),
    Command(
        "targets",
        "los destinos configurados y si existen",
        "Lo primero que hay que mirar cuando un mod no aparece donde se esperaba: comprueba que la "
        "ruta de cada destino existe de verdad.",
        examples=("modman targets", "modman --json targets"),
    ),
    Command(
        "find",
        "buscar por slug, nombre o jar",
        "Búsqueda por subcadena en el slug, el nombre del proyecto y el nombre del jar.",
        args=(Arg("pattern", "texto a buscar"),),
        examples=("modman find jei", "modman find optim"),
    ),
    Command(
        "versions",
        "versiones publicadas de un mod",
        "Lista las versiones de la fuente, marcando la instalada. Mira esto antes de add o update: "
        "una versión puede traer varias variantes del archivo y hay que elegir con --file.",
        args=(Arg("slug", "slug o parte del nombre"),),
        flags=(
            Flag("-n", "cuántas mostrar", kind="int", default=15),
            Flag("--all", "sin filtrar por versión de MC ni loader"),
            Flag("--files", "listar los archivos de cada versión"),
        ),
        examples=("modman versions jei", "modman versions create -n 40 --all --files"),
    ),
    Command(
        "deps",
        "dependencias de un mod y quién las provee",
        "Los modids que el jar declara necesitar, y qué mod del índice los provee. Un 'nadie del "
        "índice lo provee' en una dependencia obligatoria es un arranque fallido.",
        args=(Arg("slug", "slug o parte del nombre"),),
        examples=("modman deps jei",),
    ),
    Command(
        "doctor",
        "¿algún mod activo le falta una dependencia en su lado?",
        "Comprueba lado a lado que cada mod activo tenga sus dependencias provistas por otro mod "
        "activo del mismo lado. Devuelve código 1 si encuentra problemas, para usarlo en CI.",
        examples=("modman doctor", "modman doctor || echo 'revisar antes de arrancar'"),
    ),
    Command(
        "shaders",
        "¿los shaderpacks instalados van con el cargador que hay?",
        "Compara las capacidades que cada shaderpack exige en su shaders.properties "
        "(iris.features.required) con las que implementa de verdad el cargador instalado, "
        "leídas de su jar y no de una tabla que envejece. Responde sin abrir el juego la "
        "pregunta de quien usa Oculus, que va por detrás de Iris: si falta una capacidad, el "
        "shader no carga y el log no lo relaciona con la versión del cargador. Mira la carpeta, "
        "no el índice.",
        examples=("modman shaders", "modman --json shaders | jq '.packs[]|select(.ok|not)'"),
    ),
    Command(
        "requires",
        "dependencias declaradas a mano (mod ↔ shader ↔ paquete de texturas)",
        "Las que ningún metadato trae y ningún launcher comprueba: un mod que necesita una "
        "versión concreta de un shaderpack, un shaderpack que necesita Iris u Oculus, un paquete "
        "de texturas que sin Continuity carga a medias. Sin argumentos las lista todas; con "
        "--add las escribe en el índice; con --suggest mira dentro del zip y propone. Cuando los "
        "requisitos no son una lista sino un menú de stacks excluyentes —uno por loader y rango "
        "de versiones—, --discover los lee de la descripción del proyecto, --options los ordena "
        "por lo que te falta de cada uno y --choose fija el que use el pack. Los comprueba "
        "`doctor`.",
        mutates="el índice, solo con --add, --drop, --choose o --discover --save",
        args=(Arg("slug", "mod, shader o paquete que tiene la dependencia", nargs="?"),),
        flags=(
            Flag("--add", "slug del que se necesita", kind="value"),
            Flag("--drop", "quitar la dependencia con este slug", kind="value"),
            Flag("--kind", "qué es lo que se necesita", kind="value",
                 choices=("mod", "shader", "resourcepack", "datapack")),
            Flag("--version", "versión exacta; se busca como subcadena en nombre y versión", kind="value"),
            Flag("--min-version", "versión mínima, comparada por números", kind="value"),
            Flag("--or", "alternativa que también cumple; repetible", kind="append", dest="alternatives"),
            Flag("--level", "required (rompe) · partial (carga a medias) · optional",
                 kind="value", choices=("required", "partial", "optional")),
            Flag("--reason", "por qué; se guarda y se enseña en doctor", kind="value"),
            Flag("--suggest", "mirar dentro del zip y proponer, sin escribir"),
            Flag("--discover", "leer los requisitos de la descripción del proyecto y ordenarlos"),
            Flag("--save", "con --discover, guardar las opciones en el índice"),
            Flag("--options", "listar los stacks alternativos resueltos contra este pack",
                 dest="show_options"),
            Flag("--choose", "fijar cuál de los stacks usa este pack", kind="value"),
        ),
        examples=(
            "modman requires",
            "modman requires euphoria-patcher --add complementary-reimagined --kind shader "
            "--version r5.9.3 --reason 'parchea esa versión exacta'",
            "modman requires complementary-reimagined --add iris --or oculus "
            "--min-version 1.7 --reason 'un shaderpack necesita cargador'",
            "modman requires faithful --add continuity --or ctm --level partial "
            "--reason 'texturas conectadas de OptiFine'",
            "modman requires faithful --suggest",
            "modman requires glowing-emissive-ores --discover --save",
            "modman requires glowing-emissive-ores --options",
            "modman requires glowing-emissive-ores --choose neoforge-1",
        ),
    ),
    Command(
        "check",
        "¿hay versiones más nuevas?",
        "Consulta la fuente y compara con la versión instalada. No cambia nada: solo informa.",
        args=(Arg("slugs", "mods a comprobar", nargs="*"),),
        flags=(Flag("--all", "comprobar todo el índice"),),
        examples=("modman check --all", "modman --json check --all | jq '[.[]|select(.outdated)]'"),
    ),
    Command(
        "set",
        "editar decisiones: side=, state=, group=, note=",
        "Cambia lo que se decide a mano. El lado y el estado no los toca ningún escaneo: esto es "
        "el único sitio donde se fijan.",
        mutates="el índice (pack/index/<slug>.toml)",
        args=(
            Arg("slug", "slug o parte del nombre"),
            Arg("assignments", "pares clave=valor", nargs="+"),
        ),
        examples=(
            "modman set jei side=both",
            "modman set tide state=off note='mata el cliente, mixin fatal'",
            "modman set sodium group=rendimiento state=hold",
        ),
        json_output=False,
    ),
    Command(
        "scan",
        "re-escanear jars y ver qué opina la heurística",
        "Abre los jars y guarda lo observado en el índice. NO cambia el lado decidido: los asesores "
        "solo votan con su motivo, y aciertan alrededor de la mitad de las veces.",
        mutates="la sección [scan] del índice",
        args=(Arg("slugs", "mods a escanear (por defecto, todos)", nargs="*"),),
        examples=("modman scan", "modman scan jei --json"),
    ),
    Command(
        "libs",
        "recalcular lado y estado de las librerías",
        "Una librería no tiene lado propio: toma el de quien la usa, y el estado del dependiente "
        "más activo. Hay que correrlo después de cada add que arrastre dependencias.",
        mutates="el índice: lado, estado y nota de cada librería",
        flags=(Flag("--dry", "mostrar sin escribir"),),
        examples=("modman libs --dry", "modman libs"),
    ),
    Command(
        "deploy",
        "sincronizar los destinos con el índice",
        "Copia, renombra y quita jars para que cada destino refleje el índice. Nunca toca un jar "
        "que no gestiona, y se niega a desplegar un mod cuyo modid ya provea uno de esos.",
        mutates="las carpetas de mods de los destinos",
        flags=(
            Flag("--dry", "mostrar el plan sin tocar nada"),
            Flag("--target", "limitar a un destino (repetible)", kind="append"),
        ),
        examples=("modman deploy --dry", "modman deploy", "modman deploy --target servidor"),
    ),
    Command(
        "add",
        "registrar mods nuevos (y sus dependencias)",
        "Resuelve la última versión compatible, la descarga, la escanea y registra el mod. Las "
        "dependencias obligatorias entran marcadas como librería, sin lado definitivo: hay que "
        "correr libs después.",
        mutates="el índice y la caché de jars; descarga de la red",
        args=(Arg("slugs", "slugs de la fuente", nargs="+"),),
        flags=(
            Flag("--side", "lado inicial", kind="value", choices=_SIDES),
            Flag("--state", "estado inicial", kind="value", choices=_STATES, default="on"),
            Flag("--group", "grupo de duplicados", kind="value"),
            Flag("--note", "por qué está aquí", kind="value"),
            Flag("--version", "fijar una versión concreta", kind="value", dest="version"),
            Flag("--file", "elegir una variante del archivo", kind="value"),
            Flag("--no-deps", "no arrastrar dependencias"),
            Flag("--kind", "qué se añade: mod (por defecto) · shader · resourcepack · datapack",
                 kind="value", choices=("mod", "shader", "resourcepack", "datapack")),
        ),
        examples=(
            "modman add jei",
            "modman add create --version 6.0.4 --note 'pedido por el owner'",
            "modman add sodium --side client --state hold --group rendimiento",
        ),
    ),
    Command(
        "update",
        "actualizar mods (con copia previa) y redesplegar",
        "Guarda una copia del TOML antes de tocarlo, cambia de versión, reescanea y despliega. El "
        "jar viejo queda anotado para que deploy lo retire del destino.",
        mutates="el índice, el historial, la caché y los destinos",
        args=(Arg("slugs", "mods a actualizar", nargs="+"),),
        flags=(
            Flag("--version", "fijar una versión concreta (solo con un mod)", kind="value", dest="version"),
            Flag("--file", "elegir una variante del archivo", kind="value"),
            Flag("--no-deploy", "no desplegar después"),
        ),
        examples=("modman update jei", "modman update geckolib --version 5.5.6 --no-deploy"),
        json_output=False,
    ),
    Command(
        "rollback",
        "volver a la versión anterior",
        "Usa la última entrada de update del historial. Falla si esa versión ya no está publicada, "
        "en vez de dejar el índice a medias.",
        mutates="el índice, el historial y los destinos",
        args=(Arg("slug", "mod a revertir"),),
        flags=(Flag("--no-deploy", "no desplegar después"),),
        examples=("modman rollback jei",),
        json_output=False,
    ),
    Command(
        "ledger",
        "historial de cambios de versión",
        "Las últimas entradas de update y rollback. También queda en pack/log/update-ledger.md "
        "para leerlo desde git.",
        flags=(Flag("-n", "cuántas entradas", kind="int", default=20),),
        examples=("modman ledger", "modman ledger -n 5 --json"),
    ),
    Command(
        "catalog",
        "regenerar los documentos del pack desde el índice",
        "Escribe el catálogo, la hoja de duplicados y la de caídos. ANTES lee las casillas que se "
        "hayan marcado en esos documentos y las guarda en el índice: por eso hay que regenerar "
        "después de marcar, y por eso regenerar no pierde las marcas.",
        mutates="los documentos de [docs].dir y las marcas en el índice",
        flags=(
            Flag("--dry", "mostrar qué se escribiría, sin escribir"),
            Flag("--force", "sobreescribir un documento que no lleve la marca de modman"),
        ),
        examples=("modman catalog --dry", "modman catalog"),
        json_output=False,
    ),
    Command(
        "apply-picks",
        "aplicar las marcas de duplicados: prefiero → on, descarte → off",
        "Lee lo marcado en mods-duplicados.md (ya guardado en el índice por catalog) y cambia los "
        "estados. No enciende un mod marcado como roto ni uno que dependa de una librería rota. "
        "Recalcula las librerías al terminar; después hay que desplegar.",
        mutates="el estado y las notas del índice",
        examples=("modman catalog && modman apply-picks && modman deploy",),
        json_output=False,
    ),
    Command(
        "patched",
        "registrar un jar reconstruido con un parche propio",
        "Copia el jar a pack/patched/, guarda su hash y hace que gane al original en cada "
        "despliegue. Si luego falta o no cuadra el hash, el despliegue se niega en vez de poner el "
        "original y deshacer el parche sin avisar.",
        mutates="el índice y pack/patched/",
        args=(Arg("slug", "mod al que pertenece"), Arg("jar", "ruta del jar reconstruido")),
        flags=(
            Flag("--note", "qué se parcheó y por qué", kind="value"),
            Flag("--on", "además, ponerlo en estado on"),
        ),
        examples=(
            "modman patched tide ./build/tide-2.1.1-cnny.jar --note 'sin EntityRenderDispatcherMixin'",
            "modman patched terra ./terra-cnny.jar --on",
        ),
        json_output=False,
    ),
    Command(
        "help",
        "referencia completa de los comandos",
        "Todo en un bloque, para leerlo de una vez. Con --json entrega la especificación de cada "
        "comando, sus opciones y si escribe algo: es la forma pensada para que un agente sepa qué "
        "puede lanzar sin tocar el pack.",
        args=(Arg("command", "detalle de un comando concreto", nargs="?"),),
        examples=(
            "modman help",
            "modman help deploy",
            "modman --json help | jq '[.commands[]|select(.read_only)|.name]'",
        ),
    ),
)

BY_NAME = {c.name: c for c in COMMANDS}
