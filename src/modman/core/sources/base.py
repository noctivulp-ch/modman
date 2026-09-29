"""De dónde salen los jars: la interfaz que cumple cualquier fuente.

La versión anterior hablaba con la API de Modrinth desde el mismo sitio donde decidía qué
desplegar. Eso hacía imposible dos cosas que hacen falta: añadir otra fuente (CurseForge, un
directorio local, un servidor propio) sin tocar el núcleo, y probar el planificador sin red.

La interfaz es deliberadamente pequeña —resolver versiones y traerse un archivo—. Todo lo demás
(qué versión conviene, dónde se guarda, qué se despliega) es decisión del núcleo, no de la fuente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RemoteFile:
    """Un archivo descargable de una versión. Una versión puede traer varios (variantes)."""

    filename: str
    url: str
    sha512: str = ""
    #: CurseForge no publica SHA-512; da SHA-1. Se guardan los dos para poder verificar la
    #: descarga con el que la fuente ofrezca, en vez de renunciar a comprobarla.
    sha1: str = ""
    size: int = 0
    primary: bool = True

    @property
    def checksum(self) -> tuple[str, str] | None:
        """`(algoritmo, valor)` del hash disponible, o None si la fuente no da ninguno."""
        if self.sha512:
            return ("sha512", self.sha512)
        if self.sha1:
            return ("sha1", self.sha1)
        return None


@dataclass(frozen=True)
class Dependency:
    slug: str
    required: bool = True


@dataclass(frozen=True)
class RemoteVersion:
    """Una versión publicada, ya normalizada: cada fuente traduce su formato a esto."""

    id: str
    number: str
    name: str = ""
    channel: str = "release"
    published: str = ""
    game_versions: tuple[str, ...] = ()
    loaders: tuple[str, ...] = ()
    files: tuple[RemoteFile, ...] = ()
    dependencies: tuple[Dependency, ...] = ()

    @property
    def primary_file(self) -> RemoteFile | None:
        for f in self.files:
            if f.primary:
                return f
        return self.files[0] if self.files else None

    def supports(self, mc: str, loader: str) -> bool:
        """Sin filtro declarado se acepta: hay fuentes que no publican esos campos."""
        ok_mc = not mc or not self.game_versions or mc in self.game_versions
        ok_loader = not loader or not self.loaders or loader in self.loaders
        return ok_mc and ok_loader


@dataclass(frozen=True)
class RemoteProject:
    slug: str
    name: str = ""
    summary: str = ""
    declared_side: str = ""
    #: La descripción larga, tal cual. Ahí es donde viven los requisitos que ningún campo
    #: estructurado recoge: los stacks alternativos de un paquete de texturas emisivas, por
    #: ejemplo, solo existen como prosa en la página.
    body: str = ""
    #: Los loaders que el proyecto declara. En un shaderpack son `iris`/`optifine`/`canvas`, y
    #: son la única pista fiable de qué cargador necesita.
    loaders: tuple = ()


class SourceError(Exception):
    """Fallo al hablar con la fuente. El mensaje debe decir qué slug y qué pasó."""


@runtime_checkable
class Source(Protocol):
    """Lo mínimo que el núcleo necesita de una fuente de mods."""

    name: str

    def project(self, slug: str) -> RemoteProject:
        """Metadatos del proyecto. Lanza `SourceError` si no existe."""
        ...

    def versions(self, slug: str) -> list[RemoteVersion]:
        """Todas las versiones publicadas, de más nueva a más vieja."""
        ...

    def download(self, file: RemoteFile, dest: Path) -> Path:
        """Trae el archivo a `dest`. Debe ser atómico: nada de destinos a medio escribir."""
        ...


class Registry:
    """Las fuentes disponibles, por nombre.

    Existe para que añadir una fuente sea registrarla y no editar un `if` en media docena de
    sitios. El índice guarda en cada mod de qué fuente vino, así que un pack puede mezclarlas.
    """

    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}

    def register(self, source: Source) -> None:
        self._sources[source.name] = source

    def get(self, name: str) -> Source:
        if name not in self._sources:
            conocidas = ", ".join(sorted(self._sources)) or "ninguna"
            raise SourceError(f"fuente desconocida: {name!r}. Registradas: {conocidas}")
        return self._sources[name]

    def __contains__(self, name: str) -> bool:
        return name in self._sources

    @property
    def names(self) -> list[str]:
        return sorted(self._sources)
