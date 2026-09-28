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

from .model import Mod, Side, State

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
