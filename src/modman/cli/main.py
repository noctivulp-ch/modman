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
from ..core.model import Action, Kind, Level, Requirement, Side, State
from ..core.sources.base import SourceError
from . import spec
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
    faltan = m.requirements()
    formatos = m.pack_formats()
    incompat = m.incompatibilities()
    todos_menus = [i for i in m.choices() if not i.ok]
    # Lo que solo está sin indexar no es un fallo del pack: es un hueco del índice.
    menus = [i for i in todos_menus if not i.unverified]
    sin_verificar = [i for i in todos_menus if i.unverified]
    # Un requisito parcial no rompe nada: se cuenta aparte y no decide el código de salida, o
    # `doctor` en CI fallaría por un paquete de texturas que se ve a medias.
    graves = [r for r in faltan if r.blocking]
    avisos = [r for r in faltan if not r.blocking]
    if out.as_json:
        out.json({
            "sides": [{"side": p.side.value, "slug": p.slug, "missing": p.missing} for p in problemas],
            "declared": [{"slug": r.slug, "needs": r.needs, "kind": r.kind.value,
                          "wanted": r.wanted_version, "problem": r.problem, "reason": r.reason,
                          "level": r.level.value, "blocking": r.blocking}
                         for r in faltan],
            "pack_formats": [{"slug": f.slug, "kind": f.kind.value, "declared": f.declared,
                              "expected": f.expected, "mc_version": f.mc_version,
                              "made_for": f.made_for} for f in formatos],
            "incompatible": incompat,
            "choices": [{"slug": i.slug, "chosen": i.chosen, "unverified": i.unverified,
                         "closest": [{"id": e.option.id, "missing": e.missing,
                                      "maybe": e.maybe} for e in i.applicable[:3]]}
                        for i in todos_menus],
        })
        return 1 if (problemas or graves or menus or incompat) else 0
    if not problemas and not faltan and not formatos and not todos_menus and not incompat:
        out.ok("cada mod activo tiene sus dependencias en su lado, y las declaradas se cumplen")
        return 0
    for x in incompat:
        out.warn(x)
    for r in graves:
        out.warn(str(r))
    for p in problemas:
        out.warn(str(p))
    for r in avisos:
        out.note(str(r))
    for f in formatos:
        out.note(str(f))
    for i in menus:
        out.warn(str(i))
    for i in sin_verificar:
        out.note(str(i))
    if todos_menus:
        out.note("`modman requires <slug> --options` enseña los stacks ordenados por lo que falta")
    out.line()
    out.note(
        f"{len(problemas)} de lado + {len(graves)} declaradas + {len(avisos)} parciales + "
        f"{len(formatos)} de pack_format. Un mod cuya dependencia solo está en el otro lado "
        f"aborta el arranque; una declarada que no se cumple suele fallar en silencio dentro del "
        f"juego; las parciales y los pack_format cargan igual y solo se ven peor."
    )
    return 1 if (problemas or graves or menus) else 0


def cmd_shaders(a, out: Out) -> int:
    m = _open(a)
    info = m.shader_features()
    if out.as_json:
        out.json(info)
        return 0 if all(p["ok"] for p in info["packs"]) else 1
    if not info["loaders"]:
        out.warn("no hay ningún cargador de shaders (Iris u Oculus) en los destinos de mods: "
                 "los shaderpacks no se pueden ni seleccionar")
    for jar, feats in info["loaders"].items():
        out.line(f"cargador: {jar}")
        out.note("implementa: " + ", ".join(feats))
    out.line()
    if not info["packs"]:
        out.note("ningún .zip en los destinos de shaders "
                 "(¿falta un destino con kinds = [\"shader\"]?)")
        return 0
    filas = []
    for p in info["packs"]:
        if not p["required"]:
            estado, detalle = "—", "no exige capacidades concretas"
        elif p["ok"]:
            estado, detalle = "✓", ", ".join(p["required"])
        else:
            estado, detalle = "✗", "FALTAN: " + ", ".join(p["missing"])
        filas.append([estado, p["file"], detalle])
    out.table(["", "shaderpack", "capacidades exigidas"], filas)
    malos = [p for p in info["packs"] if not p["ok"]]
    out.line()
    if malos:
        out.warn(f"{len(malos)} no cargarán con este cargador; hace falta uno más nuevo")
        return 1
    out.ok(f"los {len(info['packs'])} shaderpacks encajan con el cargador instalado")
    return 0


def cmd_requires(a, out: Out) -> int:
    """Listar, proponer, declarar y quitar las dependencias que se escriben a mano."""
    m = _open(a)

    if a.choose:
        mod = m.choose_option(a.slug or "", a.choose)
        out.ok(f"{mod.slug}: usa la opción {a.choose}")
        opcion = mod.choice.option(a.choose)
        pendientes = [n.label for n in opcion.required_needs]
        out.note("instala lo que falte: " + ", ".join(pendientes))
        return 0

    if a.discover:
        if not a.slug:
            out.warn("--discover necesita un slug")
            return 2
        opciones = m.discover_options(a.slug, save=a.save)
        if out.as_json:
            out.json([{"id": o.id, "loaders": list(o.loaders), "mc": list(o.mc),
                       "recommended": o.recommended, "note": o.note,
                       "needs": [{"slugs": list(n.slugs), "min_version": n.min_version,
                                  "optional": n.optional, "note": n.note} for n in o.needs]}
                      for o in opciones])
            return 0
        if not opciones:
            out.note("la descripción no declara opciones con la forma que sé leer "
                     "(«### Option N (versiones)» bajo «<loader> Requirements»)")
            return 0
        for o in opciones:
            marca = "" if o.recommended else "  [el autor lo desaconseja]"
            versiones = ", ".join(o.mc) or "sin versiones declaradas"
            out.line(f"  {o.id:14} {'/'.join(o.loaders) or 'cualquier loader':18} {versiones}{marca}")
            for n in o.needs:
                aviso = f"   ← {n.note}" if n.note.startswith("[revisar") else ""
                out.line(f"      - {n.label}{aviso}")
        out.line()
        if a.save:
            out.ok(f"{len(opciones)} opciones guardadas; elige una con --choose <id>")
        else:
            out.note("es un parser de prosa y se equivoca: revísalo y guárdalo con --save")
        return 0

    if a.show_options:
        informes = m.choices()
        if a.slug:
            informes = [i for i in informes if a.slug in i.slug]
        if out.as_json:
            out.json([{"slug": i.slug, "chosen": i.chosen, "ok": i.ok,
                       "options": [{"id": e.option.id, "applies": e.applies,
                                    "satisfied": e.satisfied, "missing": e.missing,
                                    "satisfied_by": e.satisfied_by,
                                    "missing_optional": e.missing_optional,
                                    "recommended": e.option.recommended} for e in i.statuses]}
                      for i in informes])
            return 0 if all(i.ok for i in informes) else 1
        if not informes:
            out.note("ningún mod del índice tiene stacks alternativos registrados "
                     "(ver `modman requires <slug> --discover`)")
            return 0
        for i in informes:
            out.line(f"{i.slug} — {i.name or 'opciones'}"
                     + (f"  [elegida: {i.chosen}]" if i.chosen else ""))
            for e in i.statuses:
                if e.satisfied:
                    marca = "✓"
                elif not e.applies:
                    marca = "·"
                else:
                    marca = " "
                elegida = " ←" if e.option.id == i.chosen else ""
                out.line(f"  {marca} {str(e):<70}{elegida}")
            out.line()
        out.note("✓ cubierta · = no aplica a este pack. El orden va de menos a más por instalar.")
        return 0 if all(i.ok for i in informes) else 1

    if a.suggest:
        if not a.slug:
            out.warn("--suggest necesita un slug")
            return 2
        props = m.suggest_requirements(a.slug)
        if out.as_json:
            out.json([{"slug": r.slug, "any_of": list(r.any_of), "kind": r.kind.value,
                       "level": r.level.value, "reason": r.reason} for r in props])
            return 0
        if not props:
            out.ok("nada que proponer: no se ve ninguna dependencia que declarar")
            return 0
        for r in props:
            out.line(f"  {r.label:38} [{r.level.value}] {r.reason}")
        out.line()
        out.note("son propuestas; escríbelas con --add cuando las confirmes")
        return 0

    if a.drop:
        if not a.slug:
            out.warn("--drop necesita el slug de quien tiene la dependencia")
            return 2
        mod = m.drop_requirement(a.slug, a.drop)
        out.ok(f"{mod.slug}: quitada la dependencia de {a.drop}")
        return 0

    if a.add:
        if not a.slug:
            out.warn("--add necesita el slug de quien tiene la dependencia")
            return 2
        if a.version and a.min_version:
            out.warn("--version (exacta) y --min-version (mínima) son excluyentes")
            return 2
        req = Requirement(
            slug=a.add,
            kind=Kind(a.kind) if a.kind else Kind.MOD,
            version=a.version or "",
            min_version=a.min_version or "",
            any_of=tuple(a.alternatives or ()),
            level=Level(a.level) if a.level else Level.REQUIRED,
            reason=a.reason or "",
        )
        mod = m.add_requirement(a.slug, req)
        out.ok(f"{mod.slug} → necesita {req.label} [{req.level.value}]")
        out.note("compruébalo con `modman doctor`")
        return 0

    mods = m.index.all()
    filas = []
    for slug in sorted(mods):
        for r in mods[slug].requires:
            filas.append([slug, r.kind.value, r.label, r.level.value, r.reason])
    if out.as_json:
        out.json([{"slug": f[0], "kind": f[1], "needs": f[2], "level": f[3], "reason": f[4]} for f in filas])
        return 0
    if not filas:
        out.note("ninguna dependencia declarada a mano todavía (ver `modman requires --help`)")
        return 0
    if a.slug:
        filas = [f for f in filas if a.slug in f[0]]
    out.table(["quién", "tipo", "necesita", "nivel", "motivo"], filas)
    return 0


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
        kind=Kind(a.kind) if getattr(a, "kind", None) else Kind.MOD,
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


def cmd_catalog(a, out: Out) -> int:
    m = _open(a)
    documentos, recogidas = m.catalog(write=not a.dry, force=a.force)
    for slug, marca in sorted(recogidas.items()):
        out.line(f"  marcado en el documento → índice: {slug} {marca}")
    for ruta, contenido in documentos.items():
        lineas = contenido.count("\n")
        estado = "se escribiría" if a.dry else "escrito"
        out.line(f"  {estado}: {ruta}  ({lineas} líneas)")
    for ruta in getattr(m, "unmarked_docs", []):
        out.warn(
            f"{ruta.name} existe sin la marca de modman: si tiene texto a mano al final, "
            f"se perdería. Pon la marca antes de lo que quieras conservar, o usa --force."
        )
    if a.dry:
        out.note("(simulado) nada se ha escrito")
    return 0


def cmd_apply_picks(a, out: Out) -> int:
    m = _open(a)
    mensajes = m.apply_picks()
    if not mensajes:
        out.note("nada que aplicar: no hay marcas nuevas en el índice")
        return 0
    for linea in mensajes:
        (out.warn if linea.startswith("!") else out.line)(linea.lstrip("! "))
    out.line()
    out.note(f"{len(mensajes)} cambios de estado · ahora `modman deploy`")
    return 0


def cmd_patched(a, out: Out) -> int:
    m = _open(a)
    mod = m.register_patched(a.slug, Path(a.jar), note=a.note or "", enable=a.on)
    out.ok(f"{mod.slug}: se desplegará {mod.patched} en vez de {mod.filename}")
    if mod.note:
        out.note(f"nota: {mod.note}")
    out.note("el hash queda guardado: si el jar falta o cambia, el despliegue se negará")
    return 0


def cmd_help(a, out: Out) -> int:
    """Referencia completa, o el detalle de un comando.

    Con `--json` entrega la especificación entera. Es lo que permite a un agente saber qué puede
    lanzar sin tocar el pack (`read_only`) y qué le va a escribir (`writes`), sin tener que parsear
    el texto de `--help`.
    """
    if a.command:
        cmd = spec.BY_NAME.get(a.command)
        if cmd is None:
            out.warn(f"no hay ningún comando {a.command!r}. Están: {', '.join(spec.BY_NAME)}")
            return 2
        if out.as_json:
            out.json(cmd.to_json())
            return 0
        out.line(out.paint(f"modman {cmd.name}", "fuerte") + f" — {cmd.help}")
        out.line()
        out.line(cmd.description)
        out.line()
        out.line(cmd.epilog())
        return 0

    if out.as_json:
        out.json(
            {
                "version": __version__,
                "config_file": _config.CONFIG_NAME,
                "commands": [c.to_json() for c in spec.COMMANDS],
            }
        )
        return 0
    out.line(out.paint(f"modman {__version__}", "fuerte") + " — gestión de los mods de un modpack")
    out.line()
    out.line("Solo lectura (seguros de lanzar en cualquier momento):")
    out.table(
        ["comando", "qué hace"],
        [[c.name, c.help] for c in spec.COMMANDS if c.read_only],
    )
    out.line()
    out.line("Escriben algo:")
    out.table(
        ["comando", "qué hace", "escribe"],
        [[c.name, c.help, c.mutates] for c in spec.COMMANDS if not c.read_only],
        optional=[1],
    )
    out.line()
    out.note("modman help <comando> para el detalle · modman --json help para la especificación")
    return 0


# ------------------------------------------------------------------- parser --
def _open(a) -> Modman:
    return Modman.open(Path(a.config) if a.config else None)


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser a partir de `spec.COMMANDS`.

    Una sola fuente de verdad para el parser y para `modman help --json`: así una opción nueva
    aparece en la ayuda sin que haya que acordarse de escribirla dos veces.
    """
    p = argparse.ArgumentParser(
        prog="modman",
        description="Gestión de los mods de un modpack: un índice en git, N destinos.",
        epilog=(
            "Los comandos de consulta aceptan --json. `modman help` da la referencia completa y "
            "`modman --json help` la especificación de cada comando, con qué escribe cada uno."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"modman {__version__}")
    p.add_argument("--config", help=f"ruta de un {_config.CONFIG_NAME} concreto")
    p.add_argument("--json", action="store_true", help="salida en JSON, para scripts y agentes")
    sub = p.add_subparsers(dest="cmd", metavar="comando")

    funciones = {
        "init": cmd_init,
        "status": cmd_status,
        "targets": cmd_targets,
        "find": cmd_find,
        "versions": cmd_versions,
        "deps": cmd_deps,
        "doctor": cmd_doctor,
        "shaders": cmd_shaders,
        "requires": cmd_requires,
        "check": cmd_check,
        "set": cmd_set,
        "scan": cmd_scan,
        "libs": cmd_libs,
        "deploy": cmd_deploy,
        "add": cmd_add,
        "update": cmd_update,
        "rollback": cmd_rollback,
        "ledger": cmd_ledger,
        "catalog": cmd_catalog,
        "apply-picks": cmd_apply_picks,
        "patched": cmd_patched,
        "help": cmd_help,
    }

    for cmd in spec.COMMANDS:
        # La marca de escritura va en la ayuda corta: es lo primero que necesita saber quien
        # automatiza, y en el listado de `--help` no hay sitio para más.
        etiqueta = cmd.help if cmd.read_only else f"{cmd.help} [escribe]"
        sp = sub.add_parser(
            cmd.name,
            help=etiqueta,
            description=cmd.description,
            epilog=cmd.epilog(),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        for arg in cmd.args:
            sp.add_argument(arg.name, nargs=arg.nargs, help=arg.help) if arg.nargs else sp.add_argument(
                arg.name, help=arg.help
            )
        for f in cmd.flags:
            kwargs: dict = {"help": f.help}
            if f.kind == "store_true":
                kwargs["action"] = "store_true"
            elif f.kind == "append":
                kwargs["action"] = "append"
            elif f.kind == "int":
                kwargs["type"] = int
                kwargs["default"] = f.default
            else:
                kwargs["default"] = f.default
            if f.choices:
                kwargs["choices"] = list(f.choices)
            if f.dest:
                kwargs["dest"] = f.dest
            sp.add_argument(f.name, **kwargs)
        sp.set_defaults(func=funciones[cmd.name])

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
