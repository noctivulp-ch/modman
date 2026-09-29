"""Configuración por repositorio: `modman.toml`.

Aquí está lo que antes eran constantes en la cabecera del script —dos rutas de destino, la versión
de Minecraft y el loader— y lo que impedía usar la herramienta en otro pack. Ahora cada repositorio
declara lo suyo en un `modman.toml` en su raíz, y el núcleo no conoce ninguna ruta concreta.

El archivo se busca hacia arriba desde el directorio actual, como hace git con `.git`: así
`modman status` funciona desde cualquier subdirectorio del pack.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .model import Side

CONFIG_NAME = "modman.toml"


class ConfigError(Exception):
    """Configuración ausente o inválida. El mensaje dice qué falta y cómo arreglarlo."""


@dataclass(frozen=True)
class Config:
    """Todo lo que cambia de un pack a otro, resuelto a rutas absolutas."""

    root: Path
    pack_dir: Path
    targets: tuple = ()
    mc_version: str = ""
    loader: str = ""
    docs_dir: Path | None = None
    descriptions_file: Path | None = None
    doc_texts: dict = field(default_factory=dict)
    user_agent: str = "modman (https://github.com/noctivulp-ch/modman)"
    extra: dict = field(default_factory=dict)

    # -- rutas derivadas; un solo sitio que sepa cómo se llama cada cosa dentro de pack/ --
    @property
    def index_dir(self) -> Path:
        return self.pack_dir / "index"

    @property
    def cache_dir(self) -> Path:
        return self.pack_dir / "cache"

    @property
    def patched_dir(self) -> Path:
        return self.pack_dir / "patched"

    @property
    def log_dir(self) -> Path:
        return self.pack_dir / "log"

    @property
    def snapshot_dir(self) -> Path:
        return self.log_dir / "pre-update"

    @property
    def ledger(self) -> Path:
        return self.log_dir / "update-ledger.jsonl"

    def target(self, name: str):
        for t in self.targets:
            if t.name == name:
                return t
        conocidos = ", ".join(t.name for t in self.targets) or "ninguno"
        raise ConfigError(f"no hay ningún destino llamado {name!r}; los declarados son: {conocidos}")

    @property
    def active_targets(self) -> tuple:
        return tuple(t for t in self.targets if t.enabled)


def find_config(start: Path | None = None) -> Path:
    """Busca `modman.toml` desde `start` hacia arriba. Falla diciendo cómo crearlo."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        candidate = d / CONFIG_NAME
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"no encuentro {CONFIG_NAME} en {here} ni en sus directorios padre.\n"
        f"Crea uno en la raíz del pack con `modman init`, o indica otro con --config."
    )


def load(path: Path | None = None, start: Path | None = None) -> Config:
    """Lee el `modman.toml` y lo convierte en rutas absolutas ya resueltas."""
    cfg_path = path.resolve() if path else find_config(start)
    if not cfg_path.is_file():
        raise ConfigError(f"no existe el archivo de configuración {cfg_path}")
    try:
        raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{cfg_path} no es TOML válido: {e}") from e

    root = cfg_path.parent

    def resolve(value: str) -> Path:
        """Relativa al repo, salvo que sea absoluta o empiece por `~`."""
        p = Path(value).expanduser()
        return p if p.is_absolute() else (root / p).resolve()

    pack = raw.get("pack", {})
    game = raw.get("game", {})
    docs = raw.get("docs", {})

    targets = []
    declared = raw.get("targets", [])
    if not isinstance(declared, list) or not declared:
        raise ConfigError(
            f"{cfg_path} no declara ningún destino.\n"
            "Añade al menos un bloque [[targets]] con name, path y accepts. Por ejemplo:\n\n"
            '  [[targets]]\n  name = "cliente"\n'
            '  path = "~/.local/share/PrismLauncher/instances/MiPack/minecraft/mods"\n'
            '  accepts = ["client"]'
        )
    vistos = set()
    for i, t in enumerate(declared):
        name = t.get("name")
        if not name:
            raise ConfigError(f"el destino [[targets]] número {i + 1} de {cfg_path} no tiene `name`")
        if name in vistos:
            raise ConfigError(f"hay dos destinos llamados {name!r} en {cfg_path}; los nombres deben ser únicos")
        vistos.add(name)
        if not t.get("path"):
            raise ConfigError(f"el destino {name!r} de {cfg_path} no tiene `path`")
        accepts_raw = t.get("accepts") or ["both"]
        try:
            accepts = frozenset(Side(a) for a in accepts_raw)
        except ValueError as e:
            validos = ", ".join(s.value for s in Side)
            raise ConfigError(
                f"el destino {name!r} declara un lado que no existe ({e}). Válidos: {validos}"
            ) from e
        # Import local: model no debe depender de config, y así no hay ciclo.
        from .model import Target

        targets.append(
            Target(
                name=name,
                path=resolve(t["path"]),
                accepts=accepts,
                enabled=bool(t.get("enabled", True)),
            )
        )

    return Config(
        root=root,
        pack_dir=resolve(pack.get("dir", "pack")),
        targets=tuple(targets),
        mc_version=str(game.get("version", "")),
        loader=str(game.get("loader", "")),
        docs_dir=resolve(docs["dir"]) if docs.get("dir") else None,
        descriptions_file=resolve(docs["descriptions"]) if docs.get("descriptions") else None,
        # Los títulos e introducciones de los documentos generados: lo que en el script original
        # era texto fijo de un pack concreto.
        doc_texts={k: v for k, v in docs.items() if isinstance(v, str) and k not in {"dir", "descriptions"}},
        user_agent=str(raw.get("user_agent", Config.user_agent)),
        extra=raw,
    )


TEMPLATE = """\
# modman.toml — configuración del pack para modman.
# Las rutas relativas se resuelven desde este archivo; `~` es tu home.

[pack]
dir = "pack"            # índice (en git), caché de jars, logs y snapshots

[game]
version = "{mc}"
loader = "{loader}"     # neoforge · forge · fabric · quilt

# Un bloque por sitio donde acaban los jars. Puede haber uno solo (pack sin
# servidor) o varios (servidor + cliente, o varias instancias).
[[targets]]
name = "{t1_name}"
path = "{t1_path}"
accepts = ["{t1_accepts}"]   # qué lados aterrizan aquí: server · client · both

# [[targets]]
# name = "cliente"
# path = "~/.local/share/PrismLauncher/instances/MiPack/minecraft/mods"
# accepts = ["client"]

# Opcional: CurseForge. Hace falta una API key gratuita de
# https://console.curseforge.com, en ~/.config/modman/curseforge.key o en la
# variable CURSEFORGE_API_KEY. `use_launcher_key` reutiliza la del launcher
# instalado: está emitida a nombre de esa aplicación, así que actívalo solo si
# sabes lo que implica.
# [curseforge]
# use_launcher_key = false

# Opcional: dónde escribe `modman catalog` los documentos generados, y qué texto
# llevan. `descriptions` es un TOML de `slug = "qué es este mod"`.
# [docs]
# dir = "docs"
# descriptions = "docs/descripciones.toml"
# catalog_title = "Catálogo de mods del pack"
# catalog_intro = "Generado desde el índice; no editar a mano."
"""


def template(mc: str = "1.20.1", loader: str = "neoforge", *, server: bool = True) -> str:
    """Contenido de un `modman.toml` de partida, ya adaptado a si hay servidor o no."""
    if server:
        return TEMPLATE.format(
            mc=mc, loader=loader, t1_name="servidor", t1_path="server/mods", t1_accepts="server"
        )
    return TEMPLATE.format(
        mc=mc,
        loader=loader,
        t1_name="cliente",
        t1_path="~/.local/share/PrismLauncher/instances/MiPack/minecraft/mods",
        t1_accepts="client",
    )
