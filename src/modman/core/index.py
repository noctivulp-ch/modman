"""El índice: un TOML por mod, en git, que es la fuente de verdad de las decisiones.

Separado del resto porque es la única pieza que sabe cómo se ve el índice en disco. El
planificador, las fuentes y los frontends trabajan con objetos `Mod` y no con diccionarios, así
que un cambio de formato se arregla aquí y en ningún otro sitio.

Se escribe el TOML a mano porque la biblioteca estándar solo trae lector (`tomllib`), y añadir una
dependencia para volcar veinte claves no compensa en una herramienta que quiere instalarse sin
fricción.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from .model import Mod, Scan, Side, State


class IndexError_(Exception):
    """Problema leyendo o resolviendo una entrada del índice."""


# ------------------------------------------------------------------ serializar --
def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def dumps(d: dict) -> str:
    """Vuelca un dict a TOML: claves sueltas primero, tablas después.

    Mantiene el orden de inserción, que es lo que hace que un `git diff` del índice se lea: si las
    claves bailaran en cada guardado, cada cambio de una nota parecería una reescritura entera.
    """
    plain, tables = [], []
    for k, v in d.items():
        (tables if isinstance(v, dict) else plain).append((k, v))
    lines = [f"{k} = {_toml_value(v)}" for k, v in plain if v is not None]
    for k, v in tables:
        lines += ["", f"[{k}]"] + [f"{kk} = {_toml_value(vv)}" for kk, vv in v.items() if vv is not None]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ mapeo --
def to_mod(raw: dict, slug: str) -> Mod:
    """Diccionario del TOML → `Mod`, tolerando lo que falte.

    Un índice escrito a mano, o por una versión anterior, puede no traer todos los campos. Falta
    de dato no es motivo para reventar: el valor por defecto es correcto y `scan` lo corrige.
    """
    version = raw.get("version") or {}
    download = raw.get("download") or {}
    patched = raw.get("patched") or {}
    scan_raw = raw.get("scan") or {}

    def side_of(value: str, fallback: Side) -> Side:
        try:
            return Side(str(value).rstrip("?"))
        except ValueError:
            return fallback

    return Mod(
        slug=raw.get("slug", slug),
        name=raw.get("name", ""),
        side=side_of(raw.get("side", ""), Side.BOTH),
        state=State(raw["state"]) if raw.get("state") in {s.value for s in State} else State.ON,
        group=raw.get("group", "") or "",
        note=raw.get("note", "") or "",
        lib=bool(raw.get("lib", False)),
        provider=raw.get("provider", "modrinth"),
        project_id=raw.get("project_id", ""),
        version_id=version.get("id", ""),
        version_number=version.get("number", ""),
        filename=download.get("filename", ""),
        url=download.get("url", ""),
        sha512=download.get("sha512", ""),
        patched=patched.get("filename", ""),
        scan=Scan(
            modids=list(scan_raw.get("modids", [])),
            requires=list(scan_raw.get("required", [])),
            optional=list(scan_raw.get("optional", [])),
            # El escaneo marca con "?" que es una sugerencia: no se convierte en decisión sola.
            suggested_side=side_of(scan_raw.get("side", ""), None) if scan_raw.get("side") else None,
            signals=list(scan_raw.get("flags", [])),
        ),
        raw=raw,
    )


def to_raw(mod: Mod) -> dict:
    """`Mod` → diccionario, **partiendo del original**.

    Solo se pisan los campos que este núcleo gestiona. Todo lo demás —resumen, categorías,
    licencia, el escaneo detallado— viaja intacto desde lo que se leyó.

    Las tablas `[version]`, `[download]` y `[patched]` se **actualizan clave a clave** en vez de
    reconstruirse: llevan campos que aquí no se modelan (`date`, `type`, `loaders`,
    `game_versions`, `deps_required`) y rehacerlas los borraría.

    Y se escriben aunque el `Mod` venga de código y no del disco. La primera versión solo tocaba
    los campos sueltos, así que un `Mod` construido a mano y guardado perdía el nombre del jar;
    el `deploy` siguiente calculaba `destino / ""`, que es el propio directorio, y lo daba por
    «al día» sin copiar nada. Lo encontró un test del planificador.
    """
    raw = dict(mod.raw)
    raw["name"] = mod.name
    raw["slug"] = mod.slug
    if mod.project_id:
        raw["project_id"] = mod.project_id
    raw["side"] = mod.side.value
    raw["state"] = mod.state.value
    raw["group"] = mod.group
    raw["lib"] = mod.lib
    raw["note"] = mod.note
    if mod.provider and mod.provider != "modrinth":
        raw["provider"] = mod.provider

    def actualizar(tabla: str, valores: dict) -> None:
        datos = {k: v for k, v in valores.items() if v}
        if not datos and tabla not in raw:
            return
        actual = dict(raw.get(tabla) or {})
        actual.update(datos)
        if actual:
            raw[tabla] = actual

    actualizar("version", {"id": mod.version_id, "number": mod.version_number})
    actualizar("download", {"filename": mod.filename, "url": mod.url, "sha512": mod.sha512})
    actualizar("patched", {"filename": mod.patched})
    return raw


# ------------------------------------------------------------------ repositorio --
class Index:
    """Acceso al índice. Una instancia por pack.

    Cachea en memoria durante la vida del objeto: `status` y `deploy` recorren los 191 archivos
    varias veces y releerlos cada vez se nota. Quien escriba debe llamar a `save`, que actualiza
    la caché y el disco a la vez.
    """

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self._cache: dict[str, Mod] | None = None

    # -- lectura --
    def all(self) -> dict[str, Mod]:
        if self._cache is None:
            self._cache = {}
            if self.dir.is_dir():
                for p in sorted(self.dir.glob("*.toml")):
                    try:
                        raw = tomllib.loads(p.read_text(encoding="utf-8"))
                    except tomllib.TOMLDecodeError as e:
                        raise IndexError_(f"{p} no es TOML válido: {e}") from e
                    self._cache[p.stem] = to_mod(raw, p.stem)
        return self._cache

    def get(self, slug: str) -> Mod:
        mods = self.all()
        if slug not in mods:
            raise IndexError_(f"{slug!r} no está en el índice")
        return mods[slug]

    def __contains__(self, slug: str) -> bool:
        return slug in self.all()

    def resolve(self, pattern: str) -> str:
        """Slug exacto, o búsqueda por slug/nombre/jar. Lo ambiguo es error, no una apuesta."""
        mods = self.all()
        p = pattern.lower()
        if p in mods:
            return p
        hits = [
            s
            for s, m in mods.items()
            if p in s or p in m.name.lower() or p in m.filename.lower()
        ]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise IndexError_(f"{pattern!r} no coincide con ningún mod del índice")
        raise IndexError_(f"{pattern!r} es ambiguo: {', '.join(sorted(hits)[:10])}")

    def search(self, pattern: str) -> list[Mod]:
        p = pattern.lower()
        return [
            m
            for m in self.all().values()
            if p in m.slug or p in m.name.lower() or p in m.filename.lower()
        ]

    # -- escritura --
    def save(self, mod: Mod) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{mod.slug}.toml"
        path.write_text(dumps(to_raw(mod)), encoding="utf-8")
        if self._cache is not None:
            self._cache[mod.slug] = mod
        return path

    def save_raw(self, slug: str, raw: dict) -> Path:
        """Para quien construye la entrada entera (p. ej. `add`), sin pasar por `Mod`."""
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{slug}.toml"
        path.write_text(dumps(raw), encoding="utf-8")
        self._cache = None
        return path

    def delete(self, slug: str) -> None:
        (self.dir / f"{slug}.toml").unlink(missing_ok=True)
        if self._cache is not None:
            self._cache.pop(slug, None)

    def reload(self) -> None:
        self._cache = None
