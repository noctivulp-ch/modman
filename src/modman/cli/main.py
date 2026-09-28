"""CLI de modman.

Es el frontend principal, no el de repuesto: la salida la consumen agentes y scripts, así que todo
comando de consulta acepta `--json` y ninguno depende de que haya un terminal.

Los comandos no llevan lógica. Cada uno traduce argumentos, llama al gestor y formatea; si alguno
empezara a decidir algo, la TUI tendría que repetirlo y las dos se irían separando.

`argparse` en vez de Typer o cyclopts por la misma razón que el núcleo no tiene dependencias: esto
se instala en repos ajenos y en CI. Lo que se pierde es ayuda generada, no corrección.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import __version__
from ..core import config as _config
from ..core.config import ConfigError
from ..core.index import IndexError_
from ..core.ledger import LedgerError
from ..core.manager import ManagerError, Modman
from ..core.model import Action, Side, State
from ..core.sources.base import SourceError
from .render import Out

ERRORES = (ConfigError, ManagerError, IndexError_, LedgerError, SourceError)


# ------------------------------------------------------------------ comandos --
def cmd_init(a, out: Out) -> int:
    """Escribe un `modman.toml` de partida. No sobreescribe."""
    destino = Path(a.dir or ".").resolve() / _config.CONFIG_NAME
    if destino.exists() and not a.force:
        out.warn(f"{destino} ya existe; usa --force para reescribirlo")
        return 1
    destino.write_text(
        _config.template(a.mc or "1.20.1", a.loader or "neoforge", server=not a.client_only),
        encoding="utf-8",
    )
    out.ok(f"escrito {destino}")
    out.note("Revisa las rutas de los destinos y luego: modman status")
    return 0


def cmd_status(a, out: Out) -> int:
    m = _open(a)
    st = m.status()
    if out.as_json:
        out.json(
            {
                "targets": st.targets,
                "mods": [
                    {
                        "slug": r.mod.slug,
                        "name": r.mod.name,
                        "side": r.mod.side.value,
                        "state": r.mod.state.value,
                        "group": r.mod.group,
                        "lib": r.mod.lib,
                        "version": r.mod.version_number,
                        "present": r.present,
                    }
                    for r in st.rows
                ],
                "untracked": st.untracked,
            }
        )
        return 0
    cabecera = ["slug", "lado", "estado", "grupo", *st.targets, "versión"]
    filas = []
    for r in st.rows:
        presencia = [_marca(r.present[t], out) for t in st.targets]
        filas.append(
            [
                r.mod.slug,
                r.mod.side.value,
                r.mod.state.value,
                r.mod.group or "-",
                *presencia,
                r.mod.version_number,
            ]
        )
    # A 80 columnas se sacrifican primero el grupo y la versión: lo que se busca en `status` es
    # el lado, el estado y si está puesto en cada destino.
    out.table(cabecera, filas, optional=[3, len(cabecera) - 1])
    faltan = sum(1 for r in st.rows for v in r.present.values() if v == "falta")
    out.line()
    out.note(f"{len(st.rows)} mods · {len(st.targets)} destinos" + (f" · {faltan} por desplegar" if faltan else ""))
    for destino, jars in st.untracked.items():
        out.note(f"{destino}: {len(jars)} jars sin gestionar (no se tocan): {', '.join(jars)}")
    return 0


def _marca(valor: str, out: Out) -> str:
    if valor == "on":
        return out.paint("✓", "ok")
    if valor == "falta":
        return out.paint("·", "aviso")
    if valor == "—":
        return out.paint("—", "suave")
    return valor


def cmd_find(a, out: Out) -> int:
    m = _open(a)
    hits = m.find(a.pattern)
    if out.as_json:
        out.json([{"slug": x.slug, "name": x.name, "side": x.side.value, "state": x.state.value} for x in hits])
        return 0
    if not hits:
        out.note(f"nada coincide con {a.pattern!r}")
        return 1
    out.table(
        ["slug", "nombre", "lado", "estado", "jar"],
        [[x.slug, x.name[:32], x.side.value, x.state.value, x.filename] for x in hits],
        optional=[4, 1],
    )
    return 0


def cmd_versions(a, out: Out) -> int:
    m = _open(a)
    slug = m.index.resolve(a.slug)
    actual = m.index.get(slug).version_id
    vs = m.versions(slug, only_compatible=not a.all)[: a.n if a.n else None]
    if out.as_json:
        out.json(
            [
                {
                    "id": v.id,
                    "number": v.number,
                    "channel": v.channel,
                    "published": v.published,
                    "game_versions": list(v.game_versions),
                    "loaders": list(v.loaders),
                    "installed": v.id == actual,
                    "files": [{"filename": f.filename, "size": f.size, "primary": f.primary} for f in v.files],
                }
                for v in vs
            ]
        )
        return 0
    filas = []
    for v in vs:
        marca = "← instalada" if v.id == actual else ""
        filas.append([v.number, v.channel, v.published, ",".join(v.loaders), len(v.files), marca])
    out.table(["versión", "canal", "fecha", "loaders", "archivos", ""], filas, aligns="lllrl")
    if a.files or any(len(v.files) > 1 for v in vs):
        out.line()
        out.note("versiones con varias variantes (elige con --file):")
        for v in vs:
            if len(v.files) > 1 or a.files:
                for f in v.files:
                    out.note(f"  {v.number}: {f.filename}{' (primary)' if f.primary else ''}")
    return 0


def cmd_deps(a, out: Out) -> int:
    m = _open(a)
    datos = m.dependencies(a.slug)
    if out.as_json:
        out.json([{"kind": k, "modid": d, "provider": p} for k, d, p in datos])
        return 0
    if not datos:
        out.note("no declara dependencias")
        return 0
    out.table(["tipo", "modid", "lo provee"], [[k, d, p] for k, d, p in datos])
    return 0


def cmd_doctor(a, out: Out) -> int:
    m = _open(a)
    problemas = m.doctor()
    if out.as_json:
        out.json([{"side": p.side.value, "slug": p.slug, "missing": p.missing} for p in problemas])
        return 1 if problemas else 0
    if not problemas:
        out.ok("cada mod activo tiene sus dependencias en su mismo lado")
        return 0
    for p in problemas:
        out.warn(str(p))
    out.line()
    out.note(f"{len(problemas)} problemas. Un mod cuya dependencia solo está en el otro lado aborta el arranque.")
    return 1


def cmd_check(a, out: Out) -> int:
    m = _open(a)
    slugs = None if a.all or not a.slugs else a.slugs
    res = m.check(slugs)
    if out.as_json:
        out.json([{"slug": r.slug, "current": r.current, "latest": r.latest, "outdated": r.outdated} for r in res])
        return 0
    filas = [[r.slug, r.current, r.latest, "← update" if r.outdated else ""] for r in res]
    out.table(["slug", "instalada", "última", ""], filas)
    pendientes = sum(1 for r in res if r.outdated)
    out.line()
    out.note(f"{pendientes} con versión más nueva de {len(res)} comprobados")
    return 0


def cmd_set(a, out: Out) -> int:
    m = _open(a)
    campos = {}
    for par in a.assignments:
        if "=" not in par:
            out.warn(f"{par!r} no es `clave=valor`")
            return 2
        k, v = par.split("=", 1)
        campos[k.strip()] = v.strip()
    mod = m.edit(a.slug, **campos)
    out.ok(f"{mod.slug}: lado={mod.side.value} estado={mod.state.value} grupo={mod.group or '-'}")
    if mod.note:
        out.note(f"nota: {mod.note}")
    return 0


def cmd_scan(a, out: Out) -> int:
    m = _open(a)
    res = m.scan(a.slugs or None)
    if out.as_json:
        out.json(
            [
                {
                    "slug": r.slug,
                    "decided": r.decided.value,
                    "suggested": r.suggested.value if r.suggested else None,
                    "opinions": [
                        {"advisor": o.advisor, "side": o.side.value, "reason": o.reason, "confidence": o.confidence}
                        for o in r.opinions
                    ],
                }
                for r in res
            ]
        )
        return 0
    discrepan = [r for r in res if r.disagrees]
    out.note(f"escaneados {len(res)} mods · el escaneo solo sugiere, el lado lo decides tú")
    if discrepan:
        out.line()
        out.line("donde la heurística opina distinto de lo decidido:")
        for r in discrepan:
            motivo = r.opinions[0].reason if r.opinions else ""
            out.warn(f"{r.slug}: índice={r.decided.value} sugerido={r.suggested.value} — {motivo}")
    for e in m.ext.errors:
        out.warn(e)
    return 0


def cmd_libs(a, out: Out) -> int:
    m = _open(a)
    decisiones = m.recalculate_libraries(apply=not a.dry)
    if out.as_json:
        out.json(
            [
                {"slug": d.slug, "side": d.side.value, "state": d.state.value, "users": d.users, "warning": d.warning}
                for d in decisiones
            ]
        )
        return 0
    out.table(
        ["librería", "lado", "estado", "la usan"],
        [[d.slug, d.side.value, d.state.value, ", ".join(d.users) or "nadie"] for d in decisiones],
    )
    for d in decisiones:
        if d.warning:
            out.warn(d.warning)
    out.line()
    out.note(f"{len(decisiones)} librerías" + (" (simulado, no se ha escrito)" if a.dry else ""))
    return 0


def cmd_deploy(a, out: Out) -> int:
    m = _open(a)
    plan = m.plan_deploy(a.target or None)
    if out.as_json:
        out.json(
            {
                "changes": [
                    {"action": s.action.value, "target": s.target, "slug": s.slug, "path": str(s.path), "reason": s.reason}
                    for s in plan.changes
                ],
                "conflicts": plan.conflicts,
                "untracked": plan.untracked,
                "applied": 0,
            }
        )
        return 0
    for c in plan.conflicts:
        out.warn(c)
    if not plan.changes:
        out.ok("los destinos ya reflejan el índice")
        return 0
    for s in plan.changes:
        simbolo = {Action.COPY: "+", Action.REMOVE: "-", Action.RENAME: "~"}.get(s.action, " ")
        out.line(f"  {simbolo} [{s.target}] {s.path.name}  {out.paint(s.reason, 'suave')}")
    if a.dry:
        out.line()
        out.note(f"(simulado) {len(plan.changes)} cambios; sin --dry se aplicarían")
        return 0
    hechos = m.apply_deploy(plan)
    out.line()
    out.ok(f"{hechos} cambios aplicados")
    return cmd_doctor(a, out)


def cmd_add(a, out: Out) -> int:
    m = _open(a)
    añadidos = m.add(
        a.slugs,
        side=Side(a.side) if a.side else None,
        state=State(a.state),
        group=a.group or "",
        note=a.note or "",
        version=a.version,
        file=a.file,
        with_deps=not a.no_deps,
    )
    if out.as_json:
        out.json([{"slug": x.slug, "side": x.side.value, "version": x.version_number, "lib": x.lib} for x in añadidos])
        return 0
    for x in añadidos:
        etiqueta = "dependencia" if x.lib else "pedido"
        out.line(f"  + {x.slug:36} {x.version_number:26} lado={x.side.value:6} ({etiqueta})")
    if any(x.lib for x in añadidos):
        out.note("las dependencias entran como librerías: ejecuta `modman libs` para fijarles lado y estado")
    return 0


def cmd_update(a, out: Out) -> int:
    m = _open(a)
    if a.version and len(a.slugs) != 1:
        out.warn("--version solo vale con un mod")
        return 2
    cambiados = 0
    for s in a.slugs:
        try:
            viejo, nuevo = m.update(s, version=a.version, file=a.file)
        except ManagerError as e:
            out.note(str(e))
            continue
        out.line(f"  {s}: {viejo} → {nuevo}")
        cambiados += 1
    if cambiados and not a.no_deploy:
        return cmd_deploy(argparse.Namespace(**{**vars(a), "dry": False, "target": None}), out)
    return 0


def cmd_rollback(a, out: Out) -> int:
    m = _open(a)
    viejo, nuevo = m.rollback(a.slug)
    out.ok(f"{a.slug}: {viejo} → {nuevo}")
    if not a.no_deploy:
        return cmd_deploy(argparse.Namespace(**{**vars(a), "dry": False, "target": None}), out)
    return 0


def cmd_ledger(a, out: Out) -> int:
    m = _open(a)
    entradas = m.ledger.entries()[-a.n :]
    if out.as_json:
        out.json([e.to_json() for e in entradas])
        return 0
    if not entradas:
        out.note("sin historial todavía")
        return 0
    out.table(
        ["fecha", "acción", "mod", "de", "a"],
        [[e.ts, e.action, e.slug, e.frm, e.to] for e in entradas],
    )
    return 0


def cmd_targets(a, out: Out) -> int:
    """Los destinos configurados y si existen. Es lo primero que se mira cuando algo no aparece."""
    cfg = _config.load(Path(a.config) if a.config else None)
    if out.as_json:
        out.json(
            [
                {
                    "name": t.name,
                    "path": str(t.path),
                    "accepts": sorted(s.value for s in t.accepts),
                    "enabled": t.enabled,
                    "exists": t.path.is_dir(),
                }
                for t in cfg.targets
            ]
        )
        return 0
    out.table(
        ["destino", "acepta", "existe", "ruta"],
        [
            [
                t.name + ("" if t.enabled else " (desactivado)"),
                ",".join(sorted(s.value for s in t.accepts)),
                "sí" if t.path.is_dir() else out.paint("NO", "error"),
                str(t.path),
            ]
            for t in cfg.targets
        ],
    )
    out.line()
    out.note(f"pack: {cfg.root} · MC {cfg.mc_version or '?'} · {cfg.loader or '?'}")
    return 0


# ------------------------------------------------------------------- parser --
def _open(a) -> Modman:
    return Modman.open(Path(a.config) if a.config else None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="modman",
        description="Gestión de los mods de un modpack: un índice en git, N destinos.",
        epilog="Cada comando de consulta acepta --json. La configuración del pack va en modman.toml.",
    )
    p.add_argument("--version", action="version", version=f"modman {__version__}")
    p.add_argument("--config", help=f"ruta de un {_config.CONFIG_NAME} concreto")
    p.add_argument("--json", action="store_true", help="salida en JSON, para scripts y agentes")
    sub = p.add_subparsers(dest="cmd", metavar="comando")

    def add(nombre, func, ayuda):
        sp = sub.add_parser(nombre, help=ayuda)
        sp.set_defaults(func=func)
        return sp

    sp = add("init", cmd_init, "crear un modman.toml de partida")
    sp.add_argument("dir", nargs="?", help="directorio del pack (por defecto, el actual)")
    sp.add_argument("--mc", help="versión de Minecraft")
    sp.add_argument("--loader", help="neoforge · forge · fabric · quilt")
    sp.add_argument("--client-only", action="store_true", help="pack sin servidor")
    sp.add_argument("--force", action="store_true", help="reescribir si ya existe")

    add("status", cmd_status, "tabla de mods y su presencia en cada destino")
    add("targets", cmd_targets, "los destinos configurados y si existen")

    sp = add("find", cmd_find, "buscar por slug, nombre o jar")
    sp.add_argument("pattern")

    sp = add("versions", cmd_versions, "versiones publicadas de un mod")
    sp.add_argument("slug")
    sp.add_argument("-n", type=int, default=15, help="cuántas mostrar")
    sp.add_argument("--all", action="store_true", help="sin filtrar por MC/loader")
    sp.add_argument("--files", action="store_true", help="listar los archivos de cada versión")

    sp = add("deps", cmd_deps, "dependencias de un mod y quién las provee")
    sp.add_argument("slug")

    add("doctor", cmd_doctor, "¿algún mod activo le falta una dependencia en su lado?")

    sp = add("check", cmd_check, "¿hay versiones más nuevas?")
    sp.add_argument("slugs", nargs="*")
    sp.add_argument("--all", action="store_true", help="comprobar todo el índice")

    sp = add("set", cmd_set, "editar decisiones: side=, state=, group=, note=")
    sp.add_argument("slug")
    sp.add_argument("assignments", nargs="+", metavar="clave=valor")

    sp = add("scan", cmd_scan, "re-escanear jars y ver qué opina la heurística")
    sp.add_argument("slugs", nargs="*")

    sp = add("libs", cmd_libs, "recalcular lado y estado de las librerías")
    sp.add_argument("--dry", action="store_true", help="mostrar sin escribir")

    sp = add("deploy", cmd_deploy, "sincronizar los destinos con el índice")
    sp.add_argument("--dry", action="store_true", help="mostrar el plan sin tocar nada")
    sp.add_argument("--target", action="append", help="limitar a un destino (repetible)")

    sp = add("add", cmd_add, "registrar mods nuevos (y sus dependencias)")
    sp.add_argument("slugs", nargs="+")
    sp.add_argument("--side", choices=[s.value for s in Side])
    sp.add_argument("--state", choices=[s.value for s in State], default=State.ON.value)
    sp.add_argument("--group", help="grupo de duplicados")
    sp.add_argument("--note", help="por qué está aquí")
    sp.add_argument("--version", dest="version", help="fijar una versión concreta")
    sp.add_argument("--file", help="elegir una variante del archivo")
    sp.add_argument("--no-deps", action="store_true", help="no arrastrar dependencias")

    sp = add("update", cmd_update, "actualizar mods (con copia previa) y redesplegar")
    sp.add_argument("slugs", nargs="+")
    sp.add_argument("--version", dest="version", help="fijar una versión concreta")
    sp.add_argument("--file", help="elegir una variante del archivo")
    sp.add_argument("--no-deploy", action="store_true", help="no desplegar después")

    sp = add("rollback", cmd_rollback, "volver a la versión anterior")
    sp.add_argument("slug")
    sp.add_argument("--no-deploy", action="store_true")

    sp = add("ledger", cmd_ledger, "historial de cambios de versión")
    sp.add_argument("-n", type=int, default=20)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    if not getattr(a, "cmd", None):
        parser.print_help()
        return 0
    out = Out(as_json=a.json)
    try:
        return a.func(a, out) or 0
    except ERRORES as e:
        # El núcleo lanza; aquí se decide que eso es una línea en stderr y un código de salida.
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    except BrokenPipeError:
        # `modman status | head` no es un fallo.
        return 0


if __name__ == "__main__":
    sys.exit(main())
