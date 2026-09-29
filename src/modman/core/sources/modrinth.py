"""Fuente Modrinth.

Portada del script original sin cambiar su comportamiento de red —los reintentos y la espera de 15
segundos ante un 429 están medidos contra la API real—, con dos diferencias que venían de tenerlo
todo en un archivo:

- **No llama a `sys.exit`.** Un fallo de red lanza `SourceError` y decide el frontend: la CLI
  imprime y sale, la TUI lo enseña en un panel y sigue viva. Matar el proceso desde una capa
  interna hacía imposible la segunda.
- **No conoce rutas del pack.** Descarga a donde le digan.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .base import Dependency, RemoteFile, RemoteProject, RemoteVersion, SourceError

API = "https://api.modrinth.com/v2/"


class ModrinthSource:
    """Cliente mínimo de la API v2 de Modrinth."""

    name = "modrinth"

    def __init__(self, user_agent: str, *, timeout: int = 40, retries: int = 4) -> None:
        self._headers = {"User-Agent": user_agent}
        self._timeout = timeout
        self._retries = retries

    # ---------------------------------------------------------------- red --
    def _api(self, path: str, params: dict | None = None):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: json.dumps(v) if isinstance(v, list) else v for k, v in params.items()}
            )
        last: Exception | None = None
        for _ in range(self._retries):
            try:
                req = urllib.request.Request(url, headers=self._headers)
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code == 429:
                    # Límite de peticiones: la API pide esperar, no reintentar en bucle.
                    time.sleep(15)
                    last = e
                    continue
                raise SourceError(f"Modrinth respondió {e.code} para {path}") from e
            except Exception as e:  # red intermitente
                last = e
                time.sleep(3)
        raise SourceError(f"Modrinth no responde para {path} tras {self._retries} intentos ({last})")

    # ------------------------------------------------------------ interfaz --
    def project(self, slug: str) -> RemoteProject:
        data = self._api(f"project/{slug}")
        if not data:
            raise SourceError(f"Modrinth no conoce el proyecto {slug!r}")
        # `server_side`/`client_side` son "required"/"optional"/"unsupported": solo una semilla,
        # el escaneo del jar contrasta después.
        server = data.get("server_side", "")
        client = data.get("client_side", "")
        if server == "required" and client == "required":
            declared = "both"
        elif client == "unsupported":
            declared = "server"
        elif server == "unsupported":
            declared = "client"
        else:
            declared = "both"
        return RemoteProject(
            slug=data.get("slug", slug),
            name=data.get("title", slug),
            summary=data.get("description", ""),
            body=data.get("body", "") or "",
            loaders=tuple(data.get("loaders") or ()),
            declared_side=declared,
        )

    def versions(self, slug: str) -> list[RemoteVersion]:
        raw = self._api(f"project/{slug}/version") or []
        raw.sort(key=lambda v: v.get("date_published", ""), reverse=True)
        return [self._version(v) for v in raw]

    def download(self, file: RemoteFile, dest: Path) -> Path:
        """Descarga atómica: a `.part` y luego renombra.

        Si se corta a medias, un archivo truncado en la caché se vería como un jar válido y el
        fallo aparecería mucho después, al arrancar el servidor.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(file.url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=self._timeout) as r, open(tmp, "wb") as out:
                while chunk := r.read(1 << 16):
                    out.write(chunk)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise SourceError(f"no se pudo descargar {file.filename}: {e}") from e
        tmp.replace(dest)
        return dest

    # ------------------------------------------------------------ interno --
    @staticmethod
    def _version(v: dict) -> RemoteVersion:
        files = tuple(
            RemoteFile(
                filename=f["filename"],
                url=f["url"],
                sha512=f.get("hashes", {}).get("sha512", ""),
                size=f.get("size", 0),
                primary=bool(f.get("primary")),
            )
            for f in v.get("files", [])
        )
        deps = tuple(
            Dependency(slug=d["project_id"], required=d.get("dependency_type") == "required")
            for d in v.get("dependencies", [])
            if d.get("project_id")
        )
        return RemoteVersion(
            id=v["id"],
            number=v.get("version_number", ""),
            name=v.get("name", ""),
            channel=v.get("version_type", "release"),
            published=v.get("date_published", "")[:10],
            game_versions=tuple(v.get("game_versions", [])),
            loaders=tuple(v.get("loaders", [])),
            files=files,
            dependencies=deps,
        )


def pick_file(version: RemoteVersion, want: str | None = None) -> RemoteFile:
    """Elige archivo dentro de una versión.

    Muchas versiones traen variantes (`-slim`, `-server`, otro motor). Sin `want` va el `primary`;
    con `want`, coincidencia exacta y si no por subcadena. Una coincidencia ambigua es un error y
    no una elección al azar: desplegar la variante equivocada se nota tarde y mal.
    """
    if not version.files:
        raise SourceError(f"la versión {version.number} no trae ningún archivo")
    if not want:
        primary = version.primary_file
        if primary is None:
            raise SourceError(f"la versión {version.number} no tiene archivo primario")
        return primary
    w = want.lower()
    hits = [f for f in version.files if f.filename.lower() == w]
    if not hits:
        hits = [f for f in version.files if w in f.filename.lower()]
    disponibles = ", ".join(f.filename for f in version.files)
    if not hits:
        raise SourceError(f"ningún archivo de {version.number} contiene {want!r}. Hay: {disponibles}")
    if len(hits) > 1:
        ambiguos = ", ".join(f.filename for f in hits)
        raise SourceError(f"{want!r} es ambiguo en {version.number}: {ambiguos}")
    return hits[0]
