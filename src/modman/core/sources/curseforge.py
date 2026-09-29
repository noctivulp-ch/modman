"""Fuente CurseForge.

Existe porque media biblioteca de mods no está en Modrinth. En una reparación real, cinco de los
bloqueos —Ice and Fire, DragonSurvival, perfectparity, simplytooltips, hapi— eran mods que solo
viven en CurseForge: sin esta fuente no se puede ni saber si hay una versión que arregle el fallo,
que es justo la primera pregunta del método.

**Hace falta una API key.** La API responde 403 sin ella. Se consigue gratis en
<https://console.curseforge.com> ("API Keys"), y modman la busca en este orden:

1. el parámetro `api_key` del constructor,
2. la variable de entorno `CURSEFORGE_API_KEY`,
3. `~/.config/modman/curseforge.key` (una línea con la key),
4. la del launcher instalado —solo si se pide explícitamente con `allow_launcher_key=True`.

El paso 4 va desactivado por defecto a propósito. Esa key está emitida a nombre de otra aplicación
y sus términos la asignan a ella, así que usarla desde aquí es decisión de quien ejecuta y no algo
que la herramienta deba hacer por su cuenta.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .base import Dependency, RemoteFile, RemoteProject, RemoteVersion, SourceError

API = "https://api.curseforge.com/v1/"
GAME_MINECRAFT = 432
CLASS_MODS = 6

#: `modLoaderType` de la API.
LOADERS = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}

#: `hashes[].algo` de la API.
SHA1, MD5 = 1, 2

#: `dependencies[].relationType` de la API.
REQUIRED, OPTIONAL = 3, 2

KEY_FILE = Path.home() / ".config" / "modman" / "curseforge.key"

#: Dónde guardan su key los launchers, por si se autoriza usarla.
LAUNCHER_BINARIES = ("/usr/bin/prismlauncher", "/usr/bin/multimc", "/usr/local/bin/prismlauncher")


def find_api_key(*, allow_launcher_key: bool = False) -> str | None:
    """La key, buscada en los sitios habituales. Devuelve None si no hay ninguna."""
    if (env := os.environ.get("CURSEFORGE_API_KEY")):
        return env.strip()
    if KEY_FILE.is_file():
        texto = KEY_FILE.read_text(encoding="utf-8").strip()
        if texto:
            return texto
    if allow_launcher_key:
        return _launcher_key()
    return None


def _launcher_key() -> str | None:
    """La key embebida en el binario de un launcher instalado.

    Se extrae porque a veces es la única disponible en una máquina de escritorio, pero **no** se
    usa sin que alguien lo pida: pertenece a esa aplicación.
    """
    import re
    import subprocess

    patron = re.compile(rb"\$2[aby]\$\d{2}\$[A-Za-z0-9./]{40,}")
    for binario in LAUNCHER_BINARIES:
        if not Path(binario).is_file():
            continue
        try:
            datos = subprocess.run(["strings", binario], capture_output=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if (m := patron.search(datos)):
            return m.group(0).decode()
    return None


class CurseForgeSource:
    """Cliente de la API v1 de CurseForge, con la misma interfaz que el resto de fuentes."""

    name = "curseforge"

    def __init__(
        self,
        user_agent: str,
        *,
        api_key: str | None = None,
        game_version: str = "",
        loader: str = "",
        allow_launcher_key: bool = False,
        timeout: int = 40,
        retries: int = 3,
    ) -> None:
        key = api_key or find_api_key(allow_launcher_key=allow_launcher_key)
        if not key:
            raise SourceError(
                "CurseForge necesita una API key y no hay ninguna configurada.\n"
                "Consigue una gratis en https://console.curseforge.com (sección «API Keys») y ponla en\n"
                f"  {KEY_FILE}\n"
                "o en la variable de entorno CURSEFORGE_API_KEY."
            )
        self._headers = {"x-api-key": key, "Accept": "application/json", "User-Agent": user_agent}
        self._timeout = timeout
        self._retries = retries
        self.game_version = game_version
        self.loader = loader
        self._ids: dict[str, int] = {}

    # ---------------------------------------------------------------- red --
    def _get(self, path: str, params: dict | None = None):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        ultimo: Exception | None = None
        for _ in range(self._retries):
            try:
                req = urllib.request.Request(url, headers=self._headers)
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code in (401, 403):
                    raise SourceError(
                        "CurseForge rechaza la API key (HTTP %d). Comprueba que es válida y que no "
                        "ha caducado en https://console.curseforge.com" % e.code
                    ) from e
                if e.code == 429:
                    time.sleep(10)
                    ultimo = e
                    continue
                raise SourceError(f"CurseForge respondió {e.code} para {path}") from e
            except Exception as e:
                ultimo = e
                time.sleep(3)
        raise SourceError(f"CurseForge no responde para {path} ({ultimo})")

    # ------------------------------------------------------------ interno --
    def _mod_id(self, slug: str) -> int:
        """`slug` → id numérico. Acepta también que ya venga el id."""
        if slug.isdigit():
            return int(slug)
        if slug in self._ids:
            return self._ids[slug]
        datos = self._get("mods/search", {"gameId": GAME_MINECRAFT, "slug": slug, "classId": CLASS_MODS})
        hits = (datos or {}).get("data") or []
        exacto = next((m for m in hits if m.get("slug") == slug), None) or (hits[0] if hits else None)
        if not exacto:
            raise SourceError(f"CurseForge no conoce ningún mod con slug {slug!r}")
        self._ids[slug] = exacto["id"]
        return exacto["id"]

    @staticmethod
    def _file(data: dict, primary: bool) -> RemoteFile:
        hashes = {h.get("algo"): h.get("value", "") for h in data.get("hashes", [])}
        return RemoteFile(
            filename=data.get("fileName", ""),
            # downloadUrl es null cuando el autor prohíbe la descarga por terceros: se deja
            # vacío y quien lo use decide (bajarlo a mano es la única vía en ese caso).
            url=data.get("downloadUrl") or "",
            sha1=hashes.get(SHA1, ""),
            size=data.get("fileLength", 0),
            primary=primary,
        )

    def _version(self, data: dict) -> RemoteVersion:
        # gameVersions mezcla versiones del juego y nombres de loader en la misma lista.
        juegos, loaders = [], []
        for v in data.get("gameVersions", []):
            (loaders if v.lower() in LOADERS else juegos).append(v.lower() if v.lower() in LOADERS else v)
        deps = tuple(
            Dependency(slug=str(d.get("modId")), required=d.get("relationType") == REQUIRED)
            for d in data.get("dependencies", [])
            if d.get("modId")
        )
        canal = {1: "release", 2: "beta", 3: "alpha"}.get(data.get("releaseType", 1), "release")
        return RemoteVersion(
            id=str(data.get("id", "")),
            number=data.get("displayName", data.get("fileName", "")),
            name=data.get("displayName", ""),
            channel=canal,
            published=(data.get("fileDate") or "")[:10],
            game_versions=tuple(juegos),
            loaders=tuple(loaders),
            files=(self._file(data, True),),
            dependencies=deps,
        )

    # ------------------------------------------------------------ interfaz --
    def project(self, slug: str) -> RemoteProject:
        datos = self._get(f"mods/{self._mod_id(slug)}")
        d = (datos or {}).get("data")
        if not d:
            raise SourceError(f"CurseForge no devuelve datos del mod {slug!r}")
        return RemoteProject(
            slug=d.get("slug", slug),
            name=d.get("name", slug),
            summary=d.get("summary", ""),
            # CurseForge no declara lado: lo decide el escaneo del jar y la persona.
            declared_side="both",
        )

    def versions(self, slug: str) -> list[RemoteVersion]:
        params: dict = {"pageSize": 50}
        if self.game_version:
            params["gameVersion"] = self.game_version
        if self.loader and self.loader in LOADERS:
            params["modLoaderType"] = LOADERS[self.loader]
        datos = self._get(f"mods/{self._mod_id(slug)}/files", params)
        archivos = (datos or {}).get("data") or []
        archivos.sort(key=lambda f: f.get("fileDate", ""), reverse=True)
        return [self._version(f) for f in archivos]

    def download(self, file: RemoteFile, dest: Path) -> Path:
        if not file.url:
            raise SourceError(
                f"{file.filename}: el autor no permite la descarga automática desde CurseForge. "
                f"Hay que bajarlo a mano desde la web del proyecto y dejarlo en {dest.parent}."
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(file.url, headers={"User-Agent": self._headers["User-Agent"]})
            with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as out:
                while (chunk := r.read(1 << 16)):
                    out.write(chunk)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise SourceError(f"no se pudo descargar {file.filename}: {e}") from e
        tmp.replace(dest)
        return dest
