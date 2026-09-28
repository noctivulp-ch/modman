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

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


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

    def wants(self, side: Side) -> bool:
        return any(side.reaches(a) for a in self.accepts)


@dataclass
class Scan:
    """Lo que se dedujo de mirar dentro del jar. Sugerencia, nunca decisión."""

    modids: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    suggested_side: Side | None = None
    signals: list[str] = field(default_factory=list)


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
        """Cómo se llama el archivo en el destino: activo o `.disabled`."""
        art = self.artifact
        if not art:
            return ""
        return art if self.state.deployed_enabled else art + ".disabled"


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
