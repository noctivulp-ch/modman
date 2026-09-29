"""Entidades del dominio: qué es un mod, un destino y un plan de despliegue.

Este módulo no sabe leer, escribir, descargar ni dibujar nada. Es el vocabulario que comparten
el índice, las fuentes, el desplegador y los dos frontends; si algo de aquí necesitara importar
`urllib`, `rich` o `pathlib.Path.write_text`, estaría en el módulo equivocado.

El **lado** (`Side`) es un concepto de Minecraft, no del repositorio: un mod que registra bloques
hace falta en el cliente aunque el pack solo tenga servidor. Por eso el lado vive en el mod y no
en el destino. Lo que cambia de un repo a otro es **cuántos destinos hay y qué lados acepta cada
uno** (`Target.accepts`), que es justo lo que la versión anterior tenía clavado en dos constantes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Kind(str, Enum):
    """Qué clase de contenido es. No todo lo que gestiona un pack es un mod.

    Se añadió porque los launchers tratan shaders y paquetes de recursos como archivos sueltos:
    no saben de versiones compatibles ni de dependencias. Y las hay —`euphoria_patcher` no
    funciona sin la versión exacta de Complementary que espera—, así que si el índice no las
    conoce, nadie las comprueba.
    """

    MOD = "mod"
    SHADER = "shader"
    RESOURCEPACK = "resourcepack"
    DATAPACK = "datapack"

    @property
    def folder(self) -> str:
        """Carpeta habitual dentro de la instancia, para `modman init`."""
        return {
            Kind.MOD: "mods",
            Kind.SHADER: "shaderpacks",
            Kind.RESOURCEPACK: "resourcepacks",
            Kind.DATAPACK: "datapacks",
        }[self]

    @property
    def modrinth_type(self) -> str:
        """`project_type` con el que lo llama Modrinth."""
        return {
            Kind.MOD: "mod",
            Kind.SHADER: "shader",
            Kind.RESOURCEPACK: "resourcepack",
            Kind.DATAPACK: "datapack",
        }[self]

    @property
    def deployed_disabled_suffix(self) -> str:
        """Un shader o un resourcepack desactivado se retira; no existe `.disabled` para ellos."""
        return ".disabled" if self is Kind.MOD else ""


class Side(str, Enum):
    """Dónde hace falta el mod. Se decide a mano; el escaneo solo sugiere."""

    SERVER = "server"
    CLIENT = "client"
    BOTH = "both"

    def reaches(self, other: "Side") -> bool:
        """¿Un mod de este lado llega a un destino que acepta `other`?"""
        return self is Side.BOTH or other is Side.BOTH or self is other


class State(str, Enum):
    """Qué hacer con el jar en los destinos.

    `HOLD` existe porque un duplicado de función no es un error: es una decisión pendiente del
    usuario. Se despliega desactivado para que se pueda probar sin perder el registro de por qué
    está ahí.
    """

    ON = "on"
    OFF = "off"
    HOLD = "hold"

    @property
    def deployed_enabled(self) -> bool:
        """`off` y `hold` se despliegan igual, como `.jar.disabled`."""
        return self is State.ON


@dataclass(frozen=True)
class Target:
    """Un sitio donde acaban los jars: un servidor, una instancia de cliente, lo que sea.

    `accepts` es lo que hace genérica la herramienta. Un pack de solo cliente declara un destino
    que acepta `client`; uno de servidor dedicado, otro que acepta `server`; y nada impide tres
    instancias distintas de cliente con perfiles diferentes.
    """

    name: str
    path: Path
    accepts: frozenset[Side]
    enabled: bool = True
    #: Qué clases de contenido aterrizan aquí. Por omisión, mods: es lo que había antes de que
    #: existieran los demás tipos, y así un `modman.toml` viejo sigue significando lo mismo.
    kinds: frozenset = frozenset({Kind.MOD})

    def wants(self, side: Side, kind: "Kind | None" = None) -> bool:
        if kind is not None and kind not in self.kinds:
            return False
        return any(side.reaches(a) for a in self.accepts)


class Level(str, Enum):
    """Cuánto duele que la dependencia falte.

    No todas rompen igual, y tratarlas igual hace inútil el informe. Un paquete de texturas con
    texturas conectadas **funciona** sin Continuity: se ve peor, nada más. Un shader sin cargador
    de shaders no se puede ni seleccionar. Si las dos salieran como error, se dejarían de leer las
    dos.
    """

    REQUIRED = "required"
    #: Funciona, pero con parte del contenido inerte (texturas conectadas, emisivas, CIT).
    PARTIAL = "partial"
    OPTIONAL = "optional"

    @property
    def blocking(self) -> bool:
        return self is Level.REQUIRED


def version_tuple(texto: str) -> tuple[int, ...]:
    """Los números de una versión, para poder compararlas. Lo que no sea número se ignora.

    Vale para `1.7.5`, `iris-1.8.8+mc1.21.1` o `r5.9.3`, que es lo que hay. No pretende ser
    semver: solo ordenar dos cadenas del mismo proyecto, que es la única comparación que se hace.
    """
    return tuple(int(n) for n in re.findall(r"\d+", texto or ""))


@dataclass(frozen=True)
class Requirement:
    """Una dependencia que ningún metadato declara y que hay que escribir a mano.

    Existe porque los acoplamientos más molestos de un pack son los que ninguna herramienta ve.
    Los cuatro que motivaron esto, y que el modelo tiene que saber expresar:

    1. **Un mod que necesita un shader concreto.** `euphoria_patcher` no funciona sin *la versión
       exacta* de Complementary que parchea, y eso no aparece en su `mods.toml` ni en la página
       del shader. Se descubre con el juego abierto, cuando el shader no se aplica.
    2. **Un shader que necesita un cargador**, y a partir de cierta versión: Iris **o** Oculus,
       indistintamente. De ahí `any_of`: la dependencia se cumple con cualquiera de ellos, y un
       modelo de un solo `slug` obligaría a mentir eligiendo uno.
    3. **Un paquete de texturas que necesita un mod** para que parte de su contenido haga algo:
       Continuity o CTM para las texturas conectadas, ETF/EMF para las emisivas. Sin él **carga
       igual**; por eso `level = partial` y no un error.
    4. Y el caso corriente de un mod que necesita otro sin declararlo.

    `version` es texto libre y se compara como subcadena contra el nombre del archivo y la versión
    del índice: los shaders no siguen semver y su versión suele vivir solo en el nombre
    (`ComplementaryReimagined_r5.6.1.zip`). `min_version` es lo contrario: compara números y se
    cumple con esa versión o cualquiera posterior, que es como se expresa «Iris 1.7 o superior».
    """

    slug: str
    kind: "Kind" = None  # type: ignore[assignment]
    version: str = ""
    reason: str = ""
    #: Alternativas que cumplen lo mismo. `slug` es la preferida; cualquiera de estas sirve.
    any_of: tuple = ()
    level: "Level" = None  # type: ignore[assignment]
    #: Versión mínima, comparada por números. Excluyente con `version`, que es exacta.
    min_version: str = ""

    def __post_init__(self):
        if self.kind is None:
            object.__setattr__(self, "kind", Kind.MOD)
        if self.level is None:
            object.__setattr__(self, "level", Level.REQUIRED)
        object.__setattr__(self, "any_of", tuple(self.any_of))

    @property
    def candidates(self) -> tuple:
        """Todos los slugs que satisfacen la dependencia, la preferida primero."""
        return (self.slug, *(a for a in self.any_of if a != self.slug))

    @property
    def label(self) -> str:
        alt = " o ".join(self.candidates)
        if self.version:
            return f"{alt} {self.version}"
        if self.min_version:
            return f"{alt} >= {self.min_version}"
        return alt


@dataclass(frozen=True)
class Need:
    """Un elemento de una opción: uno o varios mods intercambiables entre sí.

    Se escribe como texto en el índice (`"sodium|embeddium>=0.6"`, `"?indium"`) porque una
    opción lleva media docena y anidarlas en TOML convertía una línea legible en cinco tablas.
    El `?` marca lo que el autor recomienda pero no exige.
    """

    slugs: tuple
    min_version: str = ""
    optional: bool = False
    note: str = ""

    @classmethod
    def parse(cls, texto: str) -> "Need":
        t = texto.strip()
        opcional = t.startswith("?")
        t = t.lstrip("?")
        minimo = ""
        if ">=" in t:
            t, minimo = t.split(">=", 1)
        return cls(tuple(x.strip() for x in t.split("|") if x.strip()), minimo.strip(), opcional)

    def dump(self) -> str:
        t = "|".join(self.slugs)
        if self.min_version:
            t += ">=" + self.min_version
        return ("?" if self.optional else "") + t

    @property
    def label(self) -> str:
        t = " o ".join(self.slugs)
        if self.min_version:
            t += f" >= {self.min_version}"
        return t + (" (recomendado)" if self.optional else "")


@dataclass(frozen=True)
class Option:
    """Una forma completa de cubrir una necesidad: el *stack* entero, no un mod.

    Un paquete de texturas emisivas no «necesita Continuity»: necesita **una** de varias
    combinaciones, cada una válida para un loader y un rango de versiones, y elegir dos a la vez
    suele ser peor que ninguna. Glowing Emissive Ores publica doce entre los tres loaders.
    """

    id: str
    needs: tuple = ()
    loaders: tuple = ()
    mc: tuple = ()
    recommended: bool = True
    note: str = ""

    def applies(self, mc_version: str, loader: str) -> bool:
        """¿Esta opción vale para el pack que tengo delante?

        Sin loader o sin versiones declaradas se acepta: la mitad de las descripciones no lo
        dicen, y descartar por falta de dato escondería la única opción viable.
        """
        if self.loaders and loader and loader.lower() not in {x.lower() for x in self.loaders}:
            return False
        return not self.mc or not mc_version or any(_mc_match(t, mc_version) for t in self.mc)

    @property
    def required_needs(self) -> tuple:
        return tuple(n for n in self.needs if not n.optional)


def _mc_match(token: str, version: str) -> bool:
    """`token` es una versión exacta o un rango `a..b`, como lo publican las descripciones."""
    if ".." not in token:
        return token.strip() == version.strip()
    a, _, b = token.partition("..")
    v = version_tuple(version)
    return version_tuple(a) <= v <= version_tuple(b)


@dataclass(frozen=True)
class Choice:
    """El conjunto de opciones excluyentes, con la elegida si ya se decidió.

    La decisión se guarda aparte de las opciones a propósito: las opciones son lo que publica el
    autor y cambian cuando él las cambia; `chosen` es trabajo del usuario y no debe perderse al
    volver a leer la descripción.
    """

    name: str = ""
    options: tuple = ()
    chosen: str = ""
    reason: str = ""

    def option(self, oid: str) -> "Option | None":
        return next((o for o in self.options if o.id == oid), None)

    def applicable(self, mc_version: str, loader: str) -> tuple:
        return tuple(o for o in self.options if o.applies(mc_version, loader))


@dataclass
class Scan:
    """Lo que se dedujo de mirar dentro del jar. Sugerencia, nunca decisión."""

    modids: list[str] = field(default_factory=list)
    #: Los que el jar declara como suyos, sin los de sus jars internos. Vacío en índices
    #: anteriores a que existiera la distinción; entonces vale `modids`.
    own: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    suggested_side: Side | None = None
    signals: list[str] = field(default_factory=list)

    @property
    def own_modids(self) -> list[str]:
        return self.own or self.modids


@dataclass
class Mod:
    """Una entrada del índice: la decisión tomada sobre un mod, más lo observado del jar.

    Se separa lo decidido (`side`, `state`, `group`, `note`) de lo observado (`scan`) a propósito:
    un re-escaneo puede pisar lo segundo sin tocar lo primero, que es trabajo del usuario.

    `raw` guarda el TOML tal cual se leyó, y al escribir se parte de él. Los índices reales llevan
    campos que este núcleo no interpreta —`categories`, `license`, `summary`, la URL del código,
    el escaneo detallado— y modelarlos todos sería tanto trabajo como frágil: bastaría un campo
    nuevo para que `set` los borrara al guardar. Conservar el original y tocar solo lo que se
    edita es lo que permite estrenar esta versión sobre un índice ya existente sin perder nada.

    Ojo con el nombre: en el TOML `source` es la URL del repositorio del mod, así que de dónde se
    descarga se llama `provider`.
    """

    slug: str
    name: str = ""
    kind: Kind = Kind.MOD
    side: Side = Side.BOTH
    state: State = State.ON
    group: str = ""
    note: str = ""
    lib: bool = False
    provider: str = "modrinth"
    project_id: str = ""
    version_id: str = ""
    version_number: str = ""
    filename: str = ""
    url: str = ""
    sha512: str = ""
    patched: str = ""
    scan: Scan = field(default_factory=Scan)
    #: Dependencias declaradas a mano, las que no salen de ningún metadato.
    requires: tuple = ()
    #: Stacks alternativos: cuando no hay *una* dependencia sino varias formas de cubrirla.
    choice: "Choice | None" = None
    raw: dict = field(default_factory=dict)

    @property
    def artifact(self) -> str:
        """El jar que se despliega: el parcheado si lo hay, si no el descargado.

        Un jar reconstruido con un parche propio tiene que ganarle al original; si no, el
        siguiente `deploy` desharía el parche sin avisar.
        """
        return self.patched or self.filename

    @property
    def deployed_name(self) -> str:
        """Cómo se llama el archivo en el destino: activo o `.disabled`.

        Solo los mods admiten `.disabled`: el cargador los ignora por la extensión. Un shader o un
        paquete de recursos desactivado simplemente no se copia, porque ahí el sufijo no significa
        nada y dejaría basura en la carpeta que el juego lista.
        """
        art = self.artifact
        if not art:
            return ""
        if self.state.deployed_enabled:
            return art
        sufijo = self.kind.deployed_disabled_suffix
        return art + sufijo if sufijo else ""


class Action(str, Enum):
    COPY = "copiar"
    RENAME = "renombrar"
    REMOVE = "quitar"
    SKIP = "sin cambios"


@dataclass(frozen=True)
class Step:
    """Un cambio concreto en un destino, ya decidido pero todavía no hecho.

    El desplegador devuelve una lista de estos en vez de tocar el disco directamente. Así `--dry`
    sale gratis, la TUI puede pedir confirmación mostrando exactamente lo que va a pasar, y el
    planificador se puede probar sin sistema de archivos.
    """

    action: Action
    target: str
    slug: str
    path: Path
    reason: str
    source_path: Path | None = None

    def __str__(self) -> str:
        return f"[{self.target}] {self.action.value} {self.path.name} — {self.reason}"


@dataclass
class Plan:
    """El conjunto de cambios de un `deploy`, con lo que se decidió no tocar y por qué."""

    steps: list[Step] = field(default_factory=list)
    untracked: dict[str, list[str]] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)

    @property
    def changes(self) -> list[Step]:
        return [s for s in self.steps if s.action is not Action.SKIP]

    def __bool__(self) -> bool:
        return bool(self.changes)
