"""Historial de cambios de versión, con copia previa para poder volver.

Un pack en desarrollo se rompe por una actualización cada dos por tres, y lo que hace falta
entonces no es el jar viejo —se puede volver a descargar— sino **saber qué había antes**. Por eso
se guarda el TOML completo de la entrada antes de tocarla: es lo único que no se puede reconstruir,
porque contiene el lado y el estado que alguien decidió a mano.

Se escriben dos archivos a propósito: `.jsonl` para leerlo con herramientas y `.md` para leerlo en
git sin herramientas.
"""

from __future__ import annotations

import datetime as _dt
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Entry:
    ts: str
    action: str
    slug: str
    frm: str
    to: str
    snapshot: str

    @classmethod
    def from_json(cls, data: dict) -> "Entry":
        return cls(
            ts=data.get("ts", ""),
            action=data.get("action", ""),
            slug=data.get("slug", ""),
            frm=data.get("from", ""),
            to=data.get("to", ""),
            snapshot=data.get("snapshot", ""),
        )

    def to_json(self) -> dict:
        return {
            "ts": self.ts,
            "action": self.action,
            "slug": self.slug,
            "from": self.frm,
            "to": self.to,
            "snapshot": self.snapshot,
        }


class LedgerError(Exception):
    pass


class Ledger:
    """El registro de updates y rollbacks de un pack."""

    def __init__(self, path: Path, snapshot_dir: Path) -> None:
        self.path = path
        self.snapshots = snapshot_dir

    # ------------------------------------------------------------- escribir --
    def snapshot(self, slug: str, toml_text: str, *, now: _dt.datetime | None = None) -> Path:
        """Guarda el TOML de la entrada antes de cambiarla y devuelve dónde quedó."""
        self.snapshots.mkdir(parents=True, exist_ok=True)
        marca = (now or _dt.datetime.now()).strftime("%Y%m%dT%H%M%S")
        destino = self.snapshots / f"{slug}.{marca}.toml"
        destino.write_text(toml_text, encoding="utf-8")
        return destino

    def record(
        self, slug: str, action: str, frm: str, to: str, snapshot: str, *, now: _dt.datetime | None = None
    ) -> Entry:
        entry = Entry(
            ts=(now or _dt.datetime.now()).replace(microsecond=0).isoformat(),
            action=action,
            slug=slug,
            frm=frm,
            to=to,
            snapshot=snapshot,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_json(), ensure_ascii=False) + "\n")
        self._append_markdown(entry)
        return entry

    def _append_markdown(self, entry: Entry) -> None:
        md = self.path.with_suffix(".md")
        if not md.exists():
            md.write_text("| fecha | acción | mod | de | a |\n|---|---|---|---|---|\n", encoding="utf-8")
        with open(md, "a", encoding="utf-8") as f:
            f.write(f"| {entry.ts} | {entry.action} | {entry.slug} | {entry.frm} | {entry.to} |\n")

    # --------------------------------------------------------------- leer --
    def entries(self, *, slug: str | None = None, action: str | None = None) -> list[Entry]:
        if not self.path.exists():
            return []
        out = []
        for linea in self.path.read_text(encoding="utf-8").splitlines():
            if not linea.strip():
                continue
            try:
                e = Entry.from_json(json.loads(linea))
            except json.JSONDecodeError:
                # Una línea corrupta no debe cegar el resto del historial.
                continue
            if slug and e.slug != slug:
                continue
            if action and e.action != action:
                continue
            out.append(e)
        return out

    def last_update(self, slug: str) -> Entry:
        entradas = self.entries(slug=slug, action="update")
        if not entradas:
            raise LedgerError(f"{slug}: no hay ningún update en el historial, no hay a dónde volver")
        return entradas[-1]

    def restore_snapshot(self, entry: Entry) -> dict:
        """Lee el TOML guardado de una entrada del historial."""
        path = self.snapshots / entry.snapshot
        if not path.exists():
            raise LedgerError(f"falta la copia {path}; no se puede volver atrás de forma fiable")
        return tomllib.loads(path.read_text(encoding="utf-8"))
