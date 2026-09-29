"""El grafo de dependencias del pack: quién provee qué y quién necesita a quién.

Dos preguntas se responden aquí y las dos rompen arranques cuando se responden mal:

- **`recalculate_libraries`**: una librería no tiene lado propio; tiene el de quien la usa. Si se
  deja en un solo lado y un mod del otro la necesita, el arranque falla con un error que nombra a
  la librería y no a quien la arrastró.
- **`diagnose`**: un mod activo cuya dependencia solo está en el otro lado. Pasó de verdad con
  `jei ← justenoughrecipesharing`.

Es lógica pura sobre el índice: ni red, ni disco, ni jars. Por eso está separada del gestor y se
puede probar con tres mods de mentira.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Kind, Level, Mod, Side, State, version_tuple

# Un estado «gana» a otro cuando la librería la usan mods en estados distintos: si alguien la usa
# activa, la librería tiene que estar activa.
_RANGO_ESTADO = {State.ON: 2, State.HOLD: 1, State.OFF: 0}


@dataclass
class LibraryDecision:
    """Lo que hay que cambiarle a una librería, y por qué."""

    slug: str
    side: Side
    state: State
    users: list[str] = field(default_factory=list)
    note: str = ""
    warning: str = ""


@dataclass
class Problem:
    """Una dependencia que falta en un lado concreto."""

    side: Side
    slug: str
    missing: dict[str, str]

    def __str__(self) -> str:
        detalle = ", ".join(f"{d} (→ {donde})" for d, donde in self.missing.items())
        return f"[{self.side.value}] {self.slug} requiere {detalle}"


@dataclass
class MissingRequirement:
    """Una dependencia declarada a mano que no se cumple."""

    slug: str
    needs: str
    kind: Kind
    wanted_version: str
    problem: str
    reason: str = ""
    level: Level = Level.REQUIRED

    @property
    def blocking(self) -> bool:
        """Si no lo es, el pack arranca y funciona: es un aviso, no un error."""
        return self.level.blocking

    def __str__(self) -> str:
        v = f" {self.wanted_version}" if self.wanted_version else ""
        porque = f" — {self.reason}" if self.reason else ""
        marca = "" if self.blocking else " [parcial]"
        return f"{self.slug} necesita {self.kind.value} {self.needs}{v}: {self.problem}{porque}{marca}"


@dataclass
class PackFormatIssue:
    """Un paquete de recursos que el juego listará bajo «Incompatible».

    No impide jugar —se carga igual y casi siempre se ve bien—, pero es la explicación de la
    mitad de los «se me ve raro una textura» y no hay forma de verlo sin abrir el juego.
    """

    slug: str
    kind: Kind
    declared: int
    expected: int
    mc_version: str
    made_for: list = field(default_factory=list)

    def __str__(self) -> str:
        para = f" (es de {', '.join(self.made_for)})" if self.made_for else ""
        return (
            f"{self.slug}: pack_format {self.declared}{para} y {self.mc_version} espera "
            f"{self.expected} → el juego lo marcará como incompatible"
        )


class Graph:
    """Relaciones entre los mods del índice."""

    def __init__(self, mods: dict[str, Mod]) -> None:
        self.mods = mods
        self.providers: dict[str, str] = {}
        for slug, m in mods.items():
            for modid in m.scan.modids:
                self.providers[modid] = slug
        self.by_project: dict[str, str] = {m.project_id: s for s, m in mods.items() if m.project_id}

    # ------------------------------------------------------------ dependientes --
    def dependents(self) -> dict[str, set[str]]:
        """`slug → quiénes lo necesitan`, juntando las dos fuentes de verdad.

        El jar declara modids y Modrinth declara ids de proyecto. Ninguna de las dos está completa
        por sí sola: hay jars que no declaran lo que traen dentro (jar-in-jar) y hay proyectos que
        no listan dependencias que el bytecode sí usa.
        """
        deps: dict[str, set[str]] = {}
        for slug, m in self.mods.items():
            for modid in m.scan.requires:
                proveedor = self.providers.get(modid)
                if proveedor and proveedor != slug:
                    deps.setdefault(proveedor, set()).add(slug)
            for pid in (m.raw.get("version") or {}).get("deps_required", []):
                proveedor = self.by_project.get(pid)
                if proveedor and proveedor != slug:
                    deps.setdefault(proveedor, set()).add(slug)
        self._propagate(deps)
        return deps

    def _propagate(self, deps: dict[str, set[str]]) -> None:
        """Una librería que usa otra librería le pasa sus propios usuarios.

        Sin esto, una librería de segundo nivel parece que no la usa nadie y se apaga, tumbando a
        la de primer nivel que sí hacía falta.
        """
        cambió = True
        while cambió:
            cambió = False
            for lib, usuarios in list(deps.items()):
                for u in list(usuarios):
                    if self.mods.get(u) and self.mods[u].lib and u in deps:
                        nuevos = deps[u] - usuarios - {lib}
                        if nuevos:
                            usuarios |= nuevos
                            cambió = True

    # -------------------------------------------------------------- librerías --
    def recalculate_libraries(self) -> list[LibraryDecision]:
        """Lado y estado de cada librería, deducidos de quien la usa. No escribe nada."""
        deps = self.dependents()
        salida: list[LibraryDecision] = []
        for slug, m in sorted(self.mods.items()):
            if not m.lib:
                continue
            usuarios = sorted(u for u in deps.get(slug, set()) if not self.mods[u].lib)
            if not usuarios:
                salida.append(
                    LibraryDecision(slug, m.side, State.OFF, [], "librería requerida por: nadie del índice")
                )
                continue

            lados = {self._side_aportado(m, self.mods[u]) for u in usuarios}
            side = Side.BOTH if len(lados) > 1 or Side.BOTH in lados else next(iter(lados))

            # Si el bytecode de la propia librería la sitúa claramente en un lado, se respeta: no
            # tiene sentido desplegar en el servidor una librería que solo referencia el cliente.
            propio = m.scan.suggested_side
            if propio in (Side.CLIENT, Side.SERVER) and side is Side.BOTH:
                side = propio
            # Un canal de red obligatorio manda sobre todo lo demás: sin la librería en el cliente,
            # el cliente no conecta.
            if (m.raw.get("scan") or {}).get("net_channel") == "required":
                side = Side.BOTH

            state = max((self.mods[u].state for u in usuarios), key=lambda s: _RANGO_ESTADO[s])
            aviso = ""
            if m.raw.get("broken"):
                # Una librería que tumba el arranque no se reactiva por mucho que la pidan.
                state = State.OFF
                heridos = [
                    u
                    for u in usuarios
                    if self.mods[u].state is State.ON and self.mods[u].side in (Side.SERVER, Side.BOTH)
                ]
                if heridos:
                    aviso = (
                        f"{slug} está marcada como rota y la necesitan mods activos: "
                        f"{', '.join(heridos)} → apágalos o parchea la librería"
                    )
            salida.append(
                LibraryDecision(
                    slug, side, state, usuarios, "librería requerida por: " + ", ".join(usuarios), aviso
                )
            )
        return salida

    def _side_aportado(self, lib: Mod, user: Mod) -> Side:
        """El lado que un dependiente le exige a la librería.

        Se recorta si el dependiente declara que esa dependencia es solo de un lado: hay mods de
        `both` que solo usan la librería en su parte de cliente.
        """
        mios = set(lib.scan.modids)
        scan = user.raw.get("scan") or {}
        if mios & set(scan.get("required_client_only", [])):
            return Side.CLIENT if user.side is not Side.SERVER else user.side
        if mios & set(scan.get("required_server_only", [])):
            return Side.SERVER if user.side is not Side.CLIENT else user.side
        return user.side

    # -------------------------------------------- dependencias declaradas --
    def check_requirements(self) -> list[MissingRequirement]:
        """Comprueba las dependencias que ningún metadato declara.

        Es lo que ningún launcher hace, y cubre las cuatro formas que tienen en la práctica:

        - **versión exacta**: `euphoria_patcher` parchea *una* versión de Complementary. Si el
          shader instalado es otro, el juego no da ningún error: el shader no se aplica y ya.
        - **versión mínima**: un shaderpack moderno pide Iris de tal versión en adelante.
        - **cualquiera de varios** (`any_of`): Iris **u** Oculus valen igual; se cumple con uno.
        - **parcial** (`level`): un paquete de texturas con texturas conectadas carga sin
          Continuity, solo que sin ellas. Sale como aviso y no como error, o dejaría de leerse.
        """
        fallos: list[MissingRequirement] = []
        for slug, m in sorted(self.mods.items()):
            if m.state is not State.ON:
                continue
            for req in m.requires:
                fallo = self._check_one(slug, req)
                if fallo is not None:
                    fallos.append(fallo)
        return fallos

    def _check_one(self, slug: str, req) -> MissingRequirement | None:
        """Comprueba un requisito contra el índice. `None` si se cumple con algún candidato.

        Con `any_of`, el requisito se da por bueno en cuanto uno encaja; solo si **ninguno**
        encaja se informa, y el problema que se cuenta es el del candidato que más cerca estuvo
        —estar instalado en otra versión es más accionable que no estar—.
        """
        problemas: list[str] = []
        for cand in req.candidates:
            objetivo = self.mods.get(cand)
            if objetivo is None:
                problemas.append(f"{cand}: no está en el índice")
                continue
            if objetivo.state is not State.ON:
                problemas.insert(0, f"{cand}: está en estado {objetivo.state.value}")
                continue
            instalada = f"{objetivo.filename} {objetivo.version_number}".strip()
            if req.version:
                # Los shaders no siguen semver y su versión suele estar solo en el nombre del
                # archivo, así que se busca como subcadena en los dos sitios donde puede estar.
                if req.version.lower() in instalada.lower():
                    return None
                problemas.insert(0, f"{cand}: instalada {objetivo.version_number or objetivo.filename}")
                continue
            if req.min_version:
                tengo = version_tuple(objetivo.version_number or objetivo.filename)
                if tengo >= version_tuple(req.min_version):
                    return None
                problemas.insert(0, f"{cand}: instalada {objetivo.version_number or objetivo.filename}")
                continue
            return None
        return MissingRequirement(
            slug=slug,
            needs=" o ".join(req.candidates),
            kind=req.kind,
            wanted_version=req.version or (f">= {req.min_version}" if req.min_version else ""),
            problem="; ".join(problemas) or "no está en el índice",
            reason=req.reason,
            level=req.level,
        )

    def check_pack_formats(self, mc_version: str) -> list[PackFormatIssue]:
        """Paquetes de recursos y datapacks cuyo `pack_format` no es el de esta versión.

        El dato sale del `pack.mcmeta`, que guarda `packscan` al escanear. Si no hay tabla para
        la versión del juego —la tabla se actualiza a mano y siempre va por detrás— no se emite
        veredicto: es preferible callar a marcar como incompatible algo que no lo es.
        """
        from . import packmeta

        fuera: list[PackFormatIssue] = []
        for slug, m in sorted(self.mods.items()):
            if m.kind not in (Kind.RESOURCEPACK, Kind.DATAPACK) or m.state is not State.ON:
                continue
            meta = (m.raw.get("pack") or {})
            if not meta.get("format"):
                continue
            esperado = packmeta.expected_format(mc_version, data=m.kind is Kind.DATAPACK)
            if esperado is None or packmeta.compatible(meta, esperado):
                continue
            fuera.append(
                PackFormatIssue(
                    slug=slug,
                    kind=m.kind,
                    declared=meta["format"],
                    expected=esperado,
                    mc_version=mc_version,
                    made_for=packmeta.versions_for(meta["format"], data=m.kind is Kind.DATAPACK),
                )
            )
        return fuera

    # ----------------------------------------------------------- diagnóstico --
    def diagnose(self, sides: tuple[Side, ...] = (Side.SERVER, Side.CLIENT)) -> list[Problem]:
        """Dependencias que faltan, lado a lado, entre los mods activos."""
        problemas: list[Problem] = []
        for side in sides:
            activos = {
                s: m for s, m in self.mods.items() if m.state is State.ON and m.side in (side, Side.BOTH)
            }
            provistos = {mid for m in activos.values() for mid in m.scan.modids}
            clave = "required_client_only" if side is Side.SERVER else "required_server_only"
            for slug, m in sorted(activos.items()):
                scan = m.raw.get("scan") or {}
                del_otro_lado = set(scan.get(clave, []))
                propios = set(m.scan.modids)  # incluye los de sus jars internos: esos no faltan
                faltan = [
                    d
                    for d in m.scan.requires
                    if d not in provistos and d not in del_otro_lado and d not in propios
                ]
                if faltan:
                    problemas.append(
                        Problem(
                            side=side,
                            slug=slug,
                            missing={d: self.providers.get(d, "no está en el índice") for d in faltan},
                        )
                    )
        return problemas
