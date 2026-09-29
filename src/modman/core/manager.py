"""El gestor: la única API que necesitan los frontends.

Todo lo que la CLI puede hacer, la TUI puede hacerlo llamando a lo mismo. Ese es el punto: si un
comando tuviera su lógica en `cli/`, la TUI tendría que reimplementarlo y las dos se irían
separando hasta que una tuviera un fallo que la otra no.

Nada de aquí imprime ni sale del proceso. Devuelve datos y lanza excepciones; el frontend decide
si eso es una línea en rojo, un panel o un código de salida.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config as _config
from .builtin import default_extensions
from .config import Config
from .deploy import Deployer, sha512_of
from .extensions import Extensions, SideOpinion
from .graph import Graph, LibraryDecision, MissingRequirement, PackFormatIssue, Problem
from .index import Index, dumps, to_raw
from .ledger import Ledger
from .model import Kind, Mod, Plan, Requirement, Side, State
from .reports import MARCA, CatalogReporter, load_descriptions, read_checkboxes
from .sources.base import Registry, RemoteVersion, Source, SourceError
from .sources.modrinth import ModrinthSource, pick_file


class ManagerError(Exception):
    """Algo que el usuario puede arreglar. El mensaje dice qué y cómo."""


@dataclass
class StatusRow:
    mod: Mod
    present: dict[str, str] = field(default_factory=dict)  # destino → "on" | "off" | "—"


@dataclass
class Status:
    rows: list[StatusRow]
    targets: list[str]
    untracked: dict[str, list[str]]


@dataclass
class CheckResult:
    slug: str
    current: str
    latest: str
    version: RemoteVersion | None = None

    @property
    def outdated(self) -> bool:
        return self.version is not None and self.latest != self.current


@dataclass
class ScanResult:
    slug: str
    suggested: Side | None
    opinions: list[SideOpinion]
    decided: Side

    @property
    def disagrees(self) -> bool:
        return self.suggested is not None and self.suggested is not self.decided


class Modman:
    """Fachada sobre el índice, las fuentes, el despliegue y las extensiones."""

    def __init__(
        self,
        config: Config,
        *,
        index: Index | None = None,
        extensions: Extensions | None = None,
        sources: Registry | None = None,
    ) -> None:
        self.cfg = config
        self.index = index or Index(config.index_dir)
        self.ext = extensions or default_extensions()
        self.sources = sources or Registry()
        if not self.sources.names:
            self.sources.register(ModrinthSource(config.user_agent))
            # CurseForge solo si hay key: sin ella la API devuelve 403 y registrar la fuente
            # solo serviría para fallar más tarde con un mensaje peor.
            try:
                from .sources.curseforge import CurseForgeSource

                self.sources.register(
                    CurseForgeSource(
                        config.user_agent,
                        game_version=config.mc_version,
                        loader=config.loader,
                        allow_launcher_key=bool(config.extra.get("curseforge", {}).get("use_launcher_key")),
                    )
                )
            except SourceError:
                pass
        for nombre, fuente in self.ext.sources.items():  # fuentes aportadas por plugins
            if nombre not in self.sources:
                self.sources.register(fuente)
        self.ledger = Ledger(config.ledger, config.snapshot_dir)
        self.deployer = Deployer(
            config, self.index, scanner=lambda p: self._modids_of(p), hasher=sha512_of
        )

    @classmethod
    def open(cls, config_path: Path | None = None, start: Path | None = None) -> "Modman":
        return cls(_config.load(config_path, start))

    # ---------------------------------------------------------------- infra --
    def source(self, mod_or_name: Mod | str) -> Source:
        nombre = mod_or_name.provider if isinstance(mod_or_name, Mod) else mod_or_name
        return self.sources.get(nombre or "modrinth")

    def _modids_of(self, jar: Path) -> list[str]:
        """Modids de un jar ajeno; escaneo barato, que es lo que necesita el desplegador."""
        facts = self.ext.analyze(jar, only=["jarscan"])
        datos = facts.of("jarscan")
        propios = datos.get("own") or datos.get("provides") or ()
        return sorted(str(p) for p in propios)

    def fetch(self, mod: Mod) -> Path:
        """La ruta local del jar que toca desplegar, descargándolo si falta.

        Un jar parcheado no se descarga nunca: si no está o no cuadra su hash, es un error y no un
        motivo para volver al original, porque eso desharía el parche en silencio.
        """
        if mod.patched:
            p = self.cfg.patched_dir / mod.patched
            esperado = (mod.raw.get("patched") or {}).get("sha512", "")
            if not p.exists() or (esperado and sha512_of(p) != esperado):
                raise ManagerError(
                    f"falta o no cuadra el jar parcheado {p}. Recompílalo "
                    f"(la receta debería estar en {self.cfg.pack_dir / 'patches' / (mod.slug + '.md')})."
                )
            return p
        if not mod.filename:
            raise ManagerError(f"{mod.slug}: el índice no dice qué archivo descargar")
        destino = self.cfg.cache_dir / mod.filename
        if destino.exists() and (not mod.sha512 or sha512_of(destino) == mod.sha512):
            return destino
        if not mod.url:
            raise ManagerError(f"{mod.slug}: no está en la caché y el índice no trae URL de descarga")
        from .sources.base import RemoteFile

        self.source(mod).download(RemoteFile(mod.filename, mod.url, mod.sha512), destino)
        if mod.sha512 and sha512_of(destino) != mod.sha512:
            destino.unlink(missing_ok=True)
            raise ManagerError(f"{mod.filename}: el hash no cuadra tras descargar; se ha borrado")
        return destino

    def graph(self) -> Graph:
        return Graph(self.index.all())

    # ------------------------------------------------------------ consultas --
    def status(self) -> Status:
        destinos = list(self.cfg.active_targets)
        filas = []
        for slug, mod in sorted(self.index.all().items()):
            presencia = {}
            for t in destinos:
                if not t.wants(mod.side, mod.kind):
                    presencia[t.name] = "—"
                elif mod.deployed_name and (t.path / mod.deployed_name).exists():
                    presencia[t.name] = "on" if mod.state is State.ON else mod.state.value
                else:
                    presencia[t.name] = "falta"
            filas.append(StatusRow(mod=mod, present=presencia))
        return Status(
            rows=filas,
            targets=[t.name for t in destinos],
            untracked=self.deployer.untracked(destinos),
        )

    def find(self, pattern: str) -> list[Mod]:
        return sorted(self.index.search(pattern), key=lambda m: m.slug)

    def versions(self, slug: str, *, only_compatible: bool = True) -> list[RemoteVersion]:
        mod = self.index.get(self.index.resolve(slug))
        todas = self.source(mod).versions(mod.project_id or mod.slug)
        if not only_compatible:
            return todas
        return [v for v in todas if v.supports(self.cfg.mc_version, self.cfg.loader)]

    def check(self, slugs: list[str] | None = None) -> list[CheckResult]:
        objetivos = slugs if slugs else list(self.index.all())
        salida = []
        for s in objetivos:
            slug = self.index.resolve(s)
            mod = self.index.get(slug)
            try:
                compatibles = self.versions(slug)
            except SourceError as e:
                salida.append(CheckResult(slug, mod.version_number, f"error: {e}"))
                continue
            if not compatibles:
                salida.append(CheckResult(slug, mod.version_number, "sin versión compatible"))
                continue
            nueva = compatibles[0]
            al_dia = nueva.id == mod.version_id
            salida.append(
                CheckResult(slug, mod.version_number, "al día" if al_dia else nueva.number, None if al_dia else nueva)
            )
        return salida

    def dependencies(self, slug: str) -> list[tuple[str, str, str]]:
        """`(tipo, modid, quién lo provee)` para un mod."""
        mod = self.index.get(self.index.resolve(slug))
        proveedores = self.graph().providers
        salida = []
        for tipo, lista in (("required", mod.scan.requires), ("optional", mod.scan.optional)):
            for modid in sorted(lista):
                salida.append((tipo, modid, proveedores.get(modid, "nadie del índice lo provee")))
        return salida

    def requirements(self) -> list[MissingRequirement]:
        """Dependencias declaradas a mano que no se cumplen (mod → shader, por ejemplo)."""
        return self.graph().check_requirements()

    def pack_formats(self) -> list[PackFormatIssue]:
        """Paquetes de recursos que esta versión del juego marcará como incompatibles."""
        return self.graph().check_pack_formats(self.cfg.mc_version)

    def suggest_requirements(self, slug: str) -> list[Requirement]:
        """Qué dependencias parece tener un `.zip`, mirándolo. No las escribe.

        Se separa de `scan` a propósito, igual que las opiniones de lado: lo que un analizador
        deduce se propone y lo confirma quien decide. Una propuesta escrita sola en el índice es
        indistinguible de un hecho comprobado en cuanto pasa una semana.
        """
        from .builtin import suggested_requirements

        mod = self.index.get(self.index.resolve(slug))
        datos = dict(mod.raw.get("packscan") or {})
        if not datos:
            facts = self.ext.analyze(self.fetch(mod), only=["packscan"])
            datos = facts.of("packscan")
        ya = {r.slug for r in mod.requires}
        return [r for r in suggested_requirements(datos) if r.slug not in ya]

    def add_requirement(self, slug: str, req: Requirement) -> Mod:
        """Declara una dependencia a mano. Sustituye la que hubiera para el mismo destino."""
        mod = self.index.get(self.index.resolve(slug))
        mod.requires = tuple(r for r in mod.requires if r.slug != req.slug) + (req,)
        self.index.save(mod)
        return mod

    def drop_requirement(self, slug: str, needs: str) -> Mod:
        mod = self.index.get(self.index.resolve(slug))
        mod.requires = tuple(r for r in mod.requires if r.slug != needs)
        self.index.save(mod)
        return mod

    def doctor(self) -> list[Problem]:
        lados = tuple({s for t in self.cfg.active_targets for s in t.accepts} - {Side.BOTH}) or (
            Side.SERVER,
            Side.CLIENT,
        )
        return self.graph().diagnose(lados)

    # ------------------------------------------------------------ mutaciones --
    def edit(self, slug: str, **campos) -> Mod:
        """Cambia las decisiones de un mod: `side`, `state`, `group`, `note`, `lib`."""
        mod = self.index.get(self.index.resolve(slug))
        for clave, valor in campos.items():
            if valor is None:
                continue
            if clave == "side":
                mod.side = valor if isinstance(valor, Side) else Side(valor)
            elif clave == "state":
                mod.state = valor if isinstance(valor, State) else State(valor)
            elif clave in {"group", "note"}:
                setattr(mod, clave, str(valor))
            elif clave == "lib":
                mod.lib = bool(valor)
            else:
                raise ManagerError(f"no se puede editar {clave!r}; se puede: side, state, group, note, lib")
        self.index.save(mod)
        return mod

    def scan(self, slugs: list[str] | None = None) -> list[ScanResult]:
        """Re-escanea jars y guarda lo observado. **No cambia el lado decidido**, solo opina."""
        objetivos = [self.index.resolve(s) for s in slugs] if slugs else sorted(self.index.all())
        salida = []
        for slug in objetivos:
            mod = self.index.get(slug)
            jar = self.fetch(mod)
            # Un shaderpack no es un jar: pasarle el escáner de mods solo produce un error
            # anotado y ruido en `ext.errors`. Cada tipo va por su analizador.
            cuales = ["jarscan"] if mod.kind is Kind.MOD else ["packscan"]
            facts = self.ext.analyze(jar, only=cuales)
            pack = facts.of("packscan")
            if pack and "error" not in pack:
                # Bajo su propia clave, como cualquier analizador: `raw["pack"]` es lo que lee
                # después `check_pack_formats` sin volver a abrir el zip.
                if pack.get("pack"):
                    mod.raw["pack"] = pack["pack"]
                for clave in ("shaderpack", "resourcepack", "optifine_features", "iris_only"):
                    if pack.get(clave):
                        mod.raw.setdefault("packscan", {})[clave] = pack[clave]
                self.index.save(mod)
            datos = facts.of("jarscan")
            if datos and "error" not in datos:
                mod.raw["scan"] = _scan_to_raw(datos)
                mod.scan.modids = sorted(str(x) for x in (datos.get("provides") or ()))
                mod.scan.own = sorted(str(x) for x in (datos.get("own") or ()))
                mod.scan.requires = sorted(str(x) for x in (datos.get("required") or ()))
                mod.scan.optional = sorted(str(x) for x in (datos.get("optional") or ()))
                self.index.save(mod)
            consenso = self.ext.consensus(mod, facts)
            salida.append(
                ScanResult(
                    slug=slug,
                    suggested=consenso.side if consenso else None,
                    opinions=self.ext.opinions(mod, facts),
                    decided=mod.side,
                )
            )
        return salida

    def add(
        self,
        slugs: list[str],
        *,
        side: Side | None = None,
        state: State = State.ON,
        group: str = "",
        note: str = "",
        version: str | None = None,
        file: str | None = None,
        with_deps: bool = True,
        provider: str = "modrinth",
        kind: Kind = Kind.MOD,
    ) -> list[Mod]:
        """Registra mods y, si hace falta, sus dependencias obligatorias.

        Lo pedido a mano y lo que entra arrastrado se tratan distinto: una dependencia se marca
        `lib = true` y su lado y estado los decide después `recalculate_libraries` a partir de
        quién la usa, no lo que declare su página.
        """
        fuente = self.source(provider)
        pedidos = set(slugs)
        cola = list(slugs)
        añadidos: list[Mod] = []
        while cola:
            actual = cola.pop(0)
            if actual in self.index:
                continue
            proyecto = fuente.project(actual)
            if proyecto.slug in self.index:
                continue
            es_pedido = actual in pedidos
            # Un shaderpack no declara `neoforge`: declara `iris` y `optifine`, que es a quién
            # le habla. Filtrar por el loader del pack lo descartaría entero, así que para lo que
            # no es un mod se filtra solo por versión del juego.
            loader_filtro = self.cfg.loader if kind is Kind.MOD else ""
            compatibles = [
                v
                for v in fuente.versions(proyecto.slug)
                if v.supports(self.cfg.mc_version, loader_filtro)
            ]
            if version and es_pedido:
                compatibles = [v for v in compatibles if version in (v.number, v.id)]
            if not compatibles:
                detalle = f" con número {version}" if version and es_pedido else ""
                raise ManagerError(
                    f"{proyecto.slug}: no hay versión para {self.cfg.mc_version}"
                    f"{'/' + self.cfg.loader if loader_filtro else ''}{detalle}"
                )
            elegida = compatibles[0]
            archivo = pick_file(elegida, file if es_pedido else None)

            raw = {
                "name": proyecto.name,
                "slug": proyecto.slug,
                "side": (side.value if side else proyecto.declared_side) if es_pedido else proyecto.declared_side,
                "state": state.value,
                "group": group if es_pedido else "",
                "lib": not es_pedido,
                "note": note if es_pedido else "",
                "declared": proyecto.declared_side,
                "summary": proyecto.summary,
                "version": {
                    "id": elegida.id,
                    "number": elegida.number,
                    "date": elegida.published,
                    "type": elegida.channel,
                    "loaders": list(elegida.loaders),
                    "game_versions": list(elegida.game_versions),
                    "deps_required": [d.slug for d in elegida.dependencies if d.required],
                },
                "download": {
                    "filename": archivo.filename,
                    "url": archivo.url,
                    "sha512": archivo.sha512,
                    "size": archivo.size,
                },
            }
            if kind is not Kind.MOD:
                raw["kind"] = kind.value
                # Un shaderpack o un paquete de texturas es cliente por definición: no hay
                # servidor que los lea.
                raw["side"] = (side or Side.CLIENT).value
            if provider != "modrinth":
                raw["provider"] = provider
            if es_pedido and file:
                raw["file_pref"] = file
            self.index.save_raw(proyecto.slug, raw)
            self.index.reload()
            mod = self.index.get(proyecto.slug)
            self.scan([proyecto.slug])
            añadidos.append(self.index.get(proyecto.slug))
            if with_deps:
                for dep in elegida.dependencies:
                    if not dep.required:
                        continue
                    try:
                        dslug = fuente.project(dep.slug).slug
                    except SourceError:
                        continue
                    if dslug not in self.index and dslug not in cola:
                        cola.append(dslug)
        return añadidos

    def recalculate_libraries(self, *, apply: bool = True) -> list[LibraryDecision]:
        decisiones = self.graph().recalculate_libraries()
        if apply:
            for d in decisiones:
                mod = self.index.get(d.slug)
                mod.side, mod.state, mod.note = d.side, d.state, d.note
                self.index.save(mod)
        return decisiones

    # ------------------------------------------------------------ documentos --
    def catalog(
        self, *, write: bool = True, collect: bool | None = None, force: bool = False
    ) -> tuple[dict[Path, str], dict[str, str]]:
        """Genera los documentos y, antes, recoge las casillas marcadas en ellos.

        El orden importa: si se regenerase primero, se borrarían las marcas que alguien acaba de
        poner. Devuelve `(documentos, marcas recogidas)`.

        `collect` sigue a `write` salvo que se diga otra cosa, y esa es la parte que no es obvia:
        un `--dry` que recogiera las casillas escribiría en el índice, que es justo lo que promete
        no hacer. Con `write=False` no se toca absolutamente nada.
        """
        if collect is None:
            collect = write
        if not self.cfg.docs_dir:
            raise ManagerError(
                "no hay dónde escribir los documentos. Añade a tu modman.toml:\n\n  [docs]\n  dir = \"docs\""
            )
        recogidas = self._collect_checkboxes() if collect else self._preview_checkboxes()
        contexto = {
            "docs_dir": self.cfg.docs_dir,
            "descriptions": load_descriptions(self.cfg.descriptions_file),
            "texts": self.cfg.doc_texts,
        }
        reporter = self.ext.reporters.get("catalog") or CatalogReporter()
        documentos = reporter.report(self.index.all().values(), contexto)
        self.unmarked_docs = list(getattr(reporter, "unmarked", []))
        if write:
            if self.unmarked_docs and not force:
                nombres = ", ".join(p.name for p in self.unmarked_docs)
                raise ManagerError(
                    f"estos documentos ya existen y no llevan la marca de modman: {nombres}.\n"
                    "Si tienen texto escrito a mano al final, se perdería al regenerar. Añade la línea\n\n"
                    f"  {MARCA}\n\n"
                    "justo antes de lo que quieras conservar, y vuelve a ejecutar. Si no hay nada que "
                    "conservar, usa --force."
                )
            self.cfg.docs_dir.mkdir(parents=True, exist_ok=True)
            for ruta, contenido in documentos.items():
                ruta.write_text(contenido, encoding="utf-8")
        return documentos, recogidas

    def _collect_checkboxes(self) -> dict[str, str]:
        """Lee las casillas de los documentos existentes y las guarda en el índice."""
        if not self.cfg.docs_dir:
            return {}
        fuentes = (
            ("mods-duplicados.md", "pick", {"prefiero": "prefer", "descarte": "discard"}),
            ("mods-incompatibles.md", "priority", {"alta": "alta", "media": "media", "baja": "baja"}),
        )
        recogidas: dict[str, str] = {}
        for archivo, campo, opciones in fuentes:
            marcas = read_checkboxes(self.cfg.docs_dir / archivo, opciones)
            for slug, valor in marcas.items():
                if slug not in self.index:
                    continue
                mod = self.index.get(slug)
                if mod.raw.get(campo, "") != valor:
                    mod.raw[campo] = valor
                    self.index.save(mod)
                    recogidas[slug] = f"{campo}={valor}"
        return recogidas

    def _preview_checkboxes(self) -> dict[str, str]:
        """Las marcas que se recogerían, sin guardarlas. Para `--dry`."""
        if not self.cfg.docs_dir:
            return {}
        salida: dict[str, str] = {}
        for archivo, campo, opciones in (
            ("mods-duplicados.md", "pick", {"prefiero": "prefer", "descarte": "discard"}),
            ("mods-incompatibles.md", "priority", {"alta": "alta", "media": "media", "baja": "baja"}),
        ):
            for slug, valor in read_checkboxes(self.cfg.docs_dir / archivo, opciones).items():
                if slug in self.index and self.index.get(slug).raw.get(campo, "") != valor:
                    salida[slug] = f"{campo}={valor}"
        return salida

    def apply_picks(self) -> list[str]:
        """Aplica las marcas de duplicados: `prefer` → on, `discard` → off.

        Con dos guardas que vienen de haberse quemado: un mod marcado como roto no se enciende
        aunque lo prefieran, y tampoco uno que dependa de una librería rota. Encenderlo solo
        conseguiría que el servidor no arrancara y que costara ver por qué.
        """
        mensajes: list[str] = []
        proveedores = self.graph().providers
        for slug, mod in sorted(self.index.all().items()):
            eleccion = mod.raw.get("pick", "")
            if mod.raw.get("broken") and mod.state is State.ON:
                self.edit(slug, state=State.OFF)
                mensajes.append(f"off {slug}: está marcado como roto, no puede quedar activo")
                continue
            if eleccion == "prefer" and mod.state is not State.ON:
                if mod.raw.get("broken"):
                    mensajes.append(f"! {slug}: preferido pero está roto → sigue off")
                    continue
                rotas = sorted(
                    {
                        proveedores[mid]
                        for mid in mod.scan.requires
                        if mid in proveedores and self.index.get(proveedores[mid]).raw.get("broken")
                    }
                )
                if rotas:
                    mensajes.append(f"! {slug}: preferido pero necesita librerías rotas ({', '.join(rotas)}) → sigue off")
                    continue
                self.edit(slug, state=State.ON)
                mensajes.append(f"on  {slug}")
            elif eleccion == "discard" and mod.state is not State.OFF:
                nota = ("descartado por el usuario (duplicado de función). " + mod.note).strip()
                self.edit(slug, state=State.OFF, note=nota)
                mensajes.append(f"off {slug}")
        self.recalculate_libraries()
        return mensajes

    def register_patched(self, slug: str, jar: Path, *, note: str = "", enable: bool = False) -> Mod:
        """Anota un jar reconstruido con un parche propio.

        A partir de aquí ese jar le gana al original en cada despliegue. Se copia a `pack/patched/`
        y se guarda su hash: si alguien recompila y olvida copiarlo, `fetch` se niega en vez de
        desplegar el original y deshacer el parche sin que se note.
        """
        slug = self.index.resolve(slug)
        mod = self.index.get(slug)
        if not jar.is_file():
            raise ManagerError(f"no existe el jar {jar}")
        self.cfg.patched_dir.mkdir(parents=True, exist_ok=True)
        destino = self.cfg.patched_dir / jar.name
        if jar.resolve() != destino.resolve():
            import shutil

            shutil.copy2(jar, destino)
        mod.patched = destino.name
        mod.raw["patched"] = {"filename": destino.name, "sha512": sha512_of(destino)}
        if note:
            mod.note = note
        if enable:
            mod.state = State.ON
        self.index.save(mod)
        return mod

    def plan_deploy(self, targets: list[str] | None = None) -> Plan:
        return self.deployer.plan(targets)

    def apply_deploy(self, plan: Plan) -> int:
        return self.deployer.apply(plan, fetcher=self.fetch)

    def update(self, slug: str, *, version: str | None = None, file: str | None = None) -> tuple[str, str]:
        """Cambia un mod a otra versión, dejando copia previa. Devuelve `(jar viejo, jar nuevo)`."""
        slug = self.index.resolve(slug)
        mod = self.index.get(slug)
        compatibles = self.versions(slug)
        if version:
            compatibles = [v for v in compatibles if version in (v.number, v.id)]
        if not compatibles:
            detalle = f" con número {version}" if version else ""
            raise ManagerError(f"{slug}: no hay versión para {self.cfg.mc_version}/{self.cfg.loader}{detalle}")
        nueva = compatibles[0]
        if nueva.id == mod.version_id:
            raise ManagerError(f"{slug}: ya está en {mod.version_number}")
        return self._apply_version(mod, nueva, "update", file)

    def rollback(self, slug: str) -> tuple[str, str]:
        """Vuelve a la versión anterior según el historial."""
        slug = self.index.resolve(slug)
        mod = self.index.get(slug)
        entrada = self.ledger.last_update(slug)
        previo = self.ledger.restore_snapshot(entrada)
        id_previo = (previo.get("version") or {}).get("id", "")
        candidatas = [v for v in self.source(mod).versions(mod.project_id or slug) if v.id == id_previo]
        if not candidatas:
            raise ManagerError(
                f"{slug}: la versión anterior ({id_previo or 'desconocida'}) ya no está publicada; "
                f"la copia del índice sigue en {self.cfg.snapshot_dir / entrada.snapshot}"
            )
        return self._apply_version(mod, candidatas[0], "rollback", None)

    def _apply_version(
        self, mod: Mod, nueva: RemoteVersion, action: str, file: str | None
    ) -> tuple[str, str]:
        anterior = mod.filename
        self.ledger.snapshot(mod.slug, dumps(to_raw(mod)))
        preferido = file if file is not None else (mod.raw.get("file_pref") or None)
        archivo = pick_file(nueva, preferido)
        if file is not None:
            mod.raw["file_pref"] = file or ""
        # El jar viejo queda anotado para que `deploy` lo retire del destino; si no, el juego
        # cargaría las dos versiones.
        if anterior:
            mod.raw["previous"] = sorted(set(mod.raw.get("previous", [])) | {anterior})
        mod.raw["version"] = {
            "id": nueva.id,
            "number": nueva.number,
            "date": nueva.published,
            "type": nueva.channel,
            "loaders": list(nueva.loaders),
            "game_versions": list(nueva.game_versions),
            "deps_required": [d.slug for d in nueva.dependencies if d.required],
        }
        mod.raw["download"] = {
            "filename": archivo.filename,
            "url": archivo.url,
            "sha512": archivo.sha512,
            "size": archivo.size,
        }
        mod.version_id, mod.version_number = nueva.id, nueva.number
        mod.filename, mod.url, mod.sha512 = archivo.filename, archivo.url, archivo.sha512
        self.index.save(mod)
        self.fetch(mod)
        self.scan([mod.slug])
        snap = self.ledger.snapshots.glob(f"{mod.slug}.*.toml")
        ultima = sorted(snap)[-1].name if (snap := list(snap)) else ""
        self.ledger.record(mod.slug, action, anterior, archivo.filename, ultima)
        return anterior, archivo.filename


def _scan_to_raw(datos: dict) -> dict:
    """Lo que devuelve el analizador, con los nombres que espera el índice.

    Las dos capas usan nombres distintos (`data_dirs` frente a `data`) y esta traducción existía
    implícita en el script. Dejarla explícita evita que un renombrado en el analizador cambie en
    silencio el formato del índice, que está en git.
    """
    lado = datos.get("side_scan") or ""
    orden = lambda v: sorted(str(x) for x in (v or ()))  # noqa: E731
    return {
        "side": lado,
        "modids": orden(datos.get("provides")),
        "own": orden(datos.get("own")),
        "required": orden(datos.get("required")),
        "required_client_only": orden(
            k for k, v in (datos.get("required_side") or {}).items() if v == "CLIENT"
        ),
        "required_server_only": orden(
            k for k, v in (datos.get("required_side") or {}).items() if v == "SERVER"
        ),
        "optional": orden(datos.get("optional")),
        "client_only_declared": bool(datos.get("declared_client_only")),
        "display_test": datos.get("display_test") or "",
        "data": orden(datos.get("data_dirs")),
        "assets": orden(datos.get("asset_dirs")),
        "client_refs": datos.get("client_ref_outside", 0),
        "classes": datos.get("classes", 0),
        "flags": orden(datos.get("flags")),
        "mixins": datos.get("mixin_configs", 0),
        "net_channel": datos.get("net_channel") or "",
        "content": orden(datos.get("content")),
        "ui_physics": orden(datos.get("ui_physics")),
    }
