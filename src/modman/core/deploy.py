"""Decidir qué jar va a cada destino, y aplicarlo.

La versión anterior decidía e imprimía y copiaba en el mismo bucle. Eso tenía tres consecuencias:
`--dry` era un `if` repetido en cada rama, no se podía probar sin un pack de verdad en el disco, y
una TUI no podía enseñar lo que iba a pasar antes de que pasara.

Aquí `plan()` no toca nada: devuelve un `Plan` de pasos. `apply()` ejecuta un plan ya calculado.
El escáner de jars y la función de hash entran por el constructor, así que los tests pueden
sustituirlos y no hace falta ni red ni jars reales.

Las dos protecciones del original se conservan enteras, porque las dos evitan un arranque roto:
un jar que modman no gestiona **nunca** se toca, y si uno de esos declara el mismo modid que un
mod gestionado, ese mod no se despliega —dos jars con el mismo modid tumban el juego—.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from .config import Config
from .index import Index
from .model import Action, Mod, Plan, Step, Target


def sha512_of(path: Path) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


class Deployer:
    """Sincroniza los destinos con el índice.

    `scanner` recibe la ruta de un jar y devuelve los modids que provee; se usa solo con jars que
    no están en el índice, para detectar el choque de modid. `fetcher` resuelve de dónde sale el
    archivo a copiar (caché, jar parcheado o descarga) y solo se llama en `apply`.
    """

    def __init__(
        self,
        config: Config,
        index: Index,
        *,
        scanner: Callable[[Path], Iterable[str]] | None = None,
        hasher: Callable[[Path], str] = sha512_of,
    ) -> None:
        self.cfg = config
        self.index = index
        self._scan = scanner or (lambda _p: ())
        self._hash = hasher

    # ------------------------------------------------------------ planificar --
    def variants(self, mod: Mod) -> set[str]:
        """Todos los nombres con los que ese mod pudo quedar en un destino.

        Incluye las versiones anteriores (`previous` del índice) porque si no, un `update` dejaría
        el jar viejo al lado del nuevo y el juego cargaría dos.
        """
        names: set[str] = set()
        for base in (mod.filename, mod.artifact, *mod.raw.get("previous", [])):
            if base:
                names |= {base, base + ".disabled"}
        return names

    def managed_names(self) -> set[str]:
        names: set[str] = set()
        for mod in self.index.all().values():
            names |= self.variants(mod)
        return names

    def foreign_modids(self, targets: Sequence[Target]) -> dict[tuple[str, str], str]:
        """`(destino, modid) → jar` para los jars presentes que el índice no gestiona."""
        managed = self.managed_names()
        found: dict[tuple[str, str], str] = {}
        for t in targets:
            if not t.path.is_dir():
                continue
            for jar in sorted(t.path.glob("*.jar*")):
                if jar.name in managed:
                    continue
                for modid in self._scan(jar):
                    found.setdefault((t.name, modid), jar.name)
        return found

    def untracked(self, targets: Sequence[Target]) -> dict[str, list[str]]:
        """Jars presentes que no están en el índice. Se informan; nunca se tocan."""
        managed = self.managed_names()
        out: dict[str, list[str]] = {}
        for t in targets:
            if not t.path.is_dir():
                continue
            extra = sorted(j.name for j in t.path.glob("*.jar*") if j.name not in managed)
            if extra:
                out[t.name] = extra
        return out

    def plan(self, target_names: Sequence[str] | None = None) -> Plan:
        """Qué habría que cambiar para que los destinos reflejen el índice. No toca nada."""
        targets = (
            [self.cfg.target(n) for n in target_names]
            if target_names
            else list(self.cfg.active_targets)
        )
        plan = Plan(untracked=self.untracked(targets))
        foreign = self.foreign_modids(targets)

        for slug, mod in sorted(self.index.all().items()):
            choques = [
                (t.name, foreign[(t.name, mid)])
                for t in targets
                for mid in mod.scan.modids
                if (t.name, mid) in foreign
            ]
            if choques:
                donde = ", ".join(f"{t}: {j}" for t, j in choques)
                plan.conflicts.append(
                    f"{slug}: su modid ya lo provee un jar que no gestiono ({donde}). "
                    f"No lo despliego; quita ese jar a mano."
                )
                continue

            for t in targets:
                quiere = mod.deployed_name if t.wants(mod.side) else None
                presentes = [t.path / v for v in self.variants(mod) if (t.path / v).exists()]

                if quiere is None:
                    for p in presentes:
                        plan.steps.append(
                            Step(Action.REMOVE, t.name, slug, p, f"{mod.side.value} no va en este destino")
                        )
                    continue

                destino = t.path / quiere
                for p in presentes:
                    if p != destino:
                        # Mismo jar con otro estado: es un renombrado, no un borrado y una copia.
                        razon = (
                            f"pasa a {mod.state.value}"
                            if p.name.rstrip(".disabled") == destino.name.rstrip(".disabled")
                            else "versión o nombre anterior"
                        )
                        plan.steps.append(Step(Action.REMOVE, t.name, slug, p, razon))
                if destino.exists() and self._al_dia(destino, mod):
                    plan.steps.append(Step(Action.SKIP, t.name, slug, destino, "al día"))
                else:
                    plan.steps.append(
                        Step(Action.COPY, t.name, slug, destino, f"{mod.state.value} · {mod.version_number}")
                    )
        return plan

    def _al_dia(self, path: Path, mod: Mod) -> bool:
        """Un jar parcheado no tiene el hash del original: ahí basta que el nombre coincida."""
        if mod.patched:
            return True
        if not mod.sha512:
            return True
        try:
            return self._hash(path) == mod.sha512
        except OSError:
            return False

    # --------------------------------------------------------------- aplicar --
    def apply(self, plan: Plan, fetcher: Callable[[Mod], Path]) -> int:
        """Ejecuta el plan. Devuelve cuántos cambios hizo.

        Las copias son atómicas (`.tmp` + `replace`): un jar copiado a medias en una carpeta de
        mods es un arranque fallido con un error que no apunta a la causa.
        """
        hechos = 0
        mods = self.index.all()
        for step in plan.changes:
            if step.action is Action.REMOVE:
                step.path.unlink(missing_ok=True)
                hechos += 1
            elif step.action is Action.COPY:
                origen = step.source_path or fetcher(mods[step.slug])
                step.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = step.path.with_name("." + step.path.name + ".tmp")
                shutil.copy2(origen, tmp)
                tmp.replace(step.path)
                hechos += 1
        return hechos
