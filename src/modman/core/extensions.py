"""Superficie de extensión: cómo se le añaden capacidades a modman sin tocar el núcleo.

El escaneo de jars, la detección de lado y la generación de documentos eran funciones dentro del
script. Cada mejora significaba editarlo, y cada regla propia de un pack quedaba hardcodeada para
todos. Aquí se convierten en **cuatro puntos de enganche** con contrato explícito:

| Enganche      | Qué aporta                                    | Ejemplo actual      | Ejemplo futuro                        |
|---------------|-----------------------------------------------|---------------------|---------------------------------------|
| `Analyzer`    | hechos observados de un jar                   | `jarscan`           | leer mixins, medir tiempo de carga    |
| `SideAdvisor` | una opinión sobre el lado, con su motivo      | reglas del pack     | aprender del log de un arranque real  |
| `Reporter`    | documentos generados desde el índice          | catálogo, duplicados| tabla para la wiki, informe de tamaño |
| `Source`      | de dónde salen los jars (en `sources/`)       | Modrinth            | CurseForge, carpeta local, GitHub     |

Dos decisiones que hacen que esto aguante módulos que todavía no existen:

1. **Los analizadores aportan, no pisan.** Cada uno escribe bajo su propia clave en `Facts`, así
   que añadir uno nuevo no puede romper a los demás ni cambiar lo que ve el resto. Un módulo que
   quiera datos de otro los pide por nombre y se declara dependiente de él.
2. **Los asesores de lado votan.** Devuelven sugerencia + motivo + confianza, y quien decide es el
   usuario mirando los motivos. Un detector nuevo entra como un voto más; no tiene que ganarle a
   la lógica anterior ni sustituirla, que es lo que obligaba a reescribir `scan`.

Un paquete externo se engancha declarando entry points, sin que modman lo conozca:

    [project.entry-points."modman.analyzers"]
    mixins = "mi_paquete.analizadores:MixinAnalyzer"
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .model import Mod, Side


# --------------------------------------------------------------------- hechos --
@dataclass
class Facts:
    """Lo observado de un jar, por analizador.

    No es un dict plano a propósito: `facts.of("jarscan")["provides"]` deja claro quién afirmó
    qué. Cuando dos módulos discrepan —pasa con la detección de lado— hace falta saber de quién
    viene cada dato para poder creer a uno y no al otro.
    """

    jar: Path
    by_analyzer: dict[str, dict[str, Any]] = field(default_factory=dict)

    def put(self, analyzer: str, data: dict[str, Any]) -> None:
        self.by_analyzer[analyzer] = data

    def of(self, analyzer: str) -> dict[str, Any]:
        return self.by_analyzer.get(analyzer, {})

    def get(self, key: str, default: Any = None, *, analyzer: str | None = None) -> Any:
        """Busca una clave en un analizador concreto, o en todos por orden de registro."""
        if analyzer is not None:
            return self.of(analyzer).get(key, default)
        for data in self.by_analyzer.values():
            if key in data:
                return data[key]
        return default

    @property
    def analyzers(self) -> list[str]:
        return list(self.by_analyzer)


@dataclass(frozen=True)
class SideOpinion:
    """Una sugerencia de lado con su justificación.

    `reason` no es decorativo: es lo que el usuario lee para decidir, y lo que queda escrito en el
    índice. Un asesor que no sepa explicarse no sirve de nada aquí.
    """

    side: Side
    reason: str
    confidence: float = 0.5
    advisor: str = ""


# ---------------------------------------------------------------- protocolos --
@runtime_checkable
class Analyzer(Protocol):
    """Mira un jar y cuenta lo que ve. No decide nada."""

    name: str

    def analyze(self, jar: Path) -> dict[str, Any]:
        ...


@runtime_checkable
class SideAdvisor(Protocol):
    """Opina sobre en qué lado hace falta un mod, a partir de los hechos."""

    name: str

    def advise(self, mod: Mod, facts: Facts) -> SideOpinion | None:
        ...


@runtime_checkable
class Reporter(Protocol):
    """Genera un documento a partir del índice.

    Devuelve rutas y contenido en vez de escribir, por lo mismo que el desplegador devuelve un
    plan: así se puede previsualizar, probar sin disco y dejar que la TUI lo muestre.
    """

    name: str

    def report(self, mods: Iterable[Mod], context: dict[str, Any]) -> dict[Path, str]:
        ...


# ------------------------------------------------------------------- registro --
class Extensions:
    """Todo lo enganchado, en un sitio.

    El núcleo recibe esta instancia y pregunta; no importa de dónde salió cada pieza. Eso es lo
    que permite que un pack cargue sus propias reglas y otro no, con el mismo modman instalado.
    """

    GROUPS = {
        "analyzers": "modman.analyzers",
        "advisors": "modman.advisors",
        "reporters": "modman.reporters",
        "sources": "modman.sources",
    }

    def __init__(self) -> None:
        self.analyzers: dict[str, Analyzer] = {}
        self.advisors: dict[str, SideAdvisor] = {}
        self.reporters: dict[str, Reporter] = {}
        self.sources: dict[str, Any] = {}
        # Los fallos de un módulo se guardan, no se tragan: al escribir esta clase un `except`
        # silencioso ocultó un bug propio (slicing de un set) y el síntoma fue «ningún asesor
        # opina», que no apunta a nada. Aislar un plugin roto sí; esconderlo no.
        self.errors: list[str] = []

    # -- registro explícito --
    def add_analyzer(self, a: Analyzer) -> None:
        self.analyzers[a.name] = a

    def add_advisor(self, a: SideAdvisor) -> None:
        self.advisors[a.name] = a

    def add_reporter(self, r: Reporter) -> None:
        self.reporters[r.name] = r

    def add_source(self, s) -> None:
        self.sources[s.name] = s

    # -- uso --
    def analyze(self, jar: Path, only: Iterable[str] | None = None) -> Facts:
        """Pasa el jar por los analizadores. Uno que falle no tumba a los demás.

        Un jar corrupto o con un zip raro es un dato, no una excepción que deba abortar un
        escaneo de doscientos mods: se anota el error bajo ese analizador y se sigue.
        """
        facts = Facts(jar=jar)
        elegidos = list(only) if only is not None else list(self.analyzers)
        for nombre in elegidos:
            a = self.analyzers.get(nombre)
            if a is None:
                continue
            try:
                facts.put(nombre, a.analyze(jar))
            except Exception as e:
                detalle = f"analizador {nombre} falló en {jar.name}: {type(e).__name__}: {e}"
                facts.put(nombre, {"error": detalle})
                self.errors.append(detalle)
        return facts

    def opinions(self, mod: Mod, facts: Facts) -> list[SideOpinion]:
        """Todas las opiniones sobre el lado, de más a menos segura."""
        out: list[SideOpinion] = []
        for nombre, adv in self.advisors.items():
            try:
                op = adv.advise(mod, facts)
            except Exception as e:
                self.errors.append(f"asesor {nombre} falló con {mod.slug}: {type(e).__name__}: {e}")
                continue
            if op is not None:
                out.append(op if op.advisor else SideOpinion(op.side, op.reason, op.confidence, nombre))
        return sorted(out, key=lambda o: o.confidence, reverse=True)

    def consensus(self, mod: Mod, facts: Facts) -> SideOpinion | None:
        """La opinión más fuerte, o `both` si hay empate entre lados distintos.

        El empate se resuelve hacia `both` porque los dos errores no cuestan igual: un mod de más
        en un lado se queda ahí sin hacer nada, y uno de menos rompe el registro o la partida.
        """
        ops = self.opinions(mod, facts)
        if not ops:
            return None
        mejor = ops[0]
        empatados = [o for o in ops if o.confidence == mejor.confidence]
        if len({o.side for o in empatados}) > 1:
            motivos = "; ".join(f"{o.advisor or '?'}: {o.reason}" for o in empatados)
            return SideOpinion(Side.BOTH, f"empate resuelto a both ({motivos})", mejor.confidence, "consenso")
        return mejor

    def build_reports(self, mods: Iterable[Mod], context: dict[str, Any], only: Iterable[str] | None = None):
        mods = list(mods)
        salida: dict[Path, str] = {}
        for nombre in (list(only) if only is not None else list(self.reporters)):
            r = self.reporters.get(nombre)
            if r is None:
                continue
            salida.update(r.report(mods, context))
        return salida

    # -- descubrimiento --
    def load_entry_points(self) -> list[str]:
        """Carga lo que otros paquetes hayan declarado. Devuelve qué cargó.

        Un plugin roto no debe impedir que la herramienta arranque: se ignora y se informa. Si
        abortara, instalar un experimento dejaría el pack sin gestionar.
        """
        from importlib.metadata import entry_points

        cargados: list[str] = []
        añadir = {
            "analyzers": self.add_analyzer,
            "advisors": self.add_advisor,
            "reporters": self.add_reporter,
            "sources": self.add_source,
        }
        for clave, grupo in self.GROUPS.items():
            try:
                puntos = entry_points(group=grupo)
            except Exception:
                continue
            for ep in puntos:
                try:
                    obj = ep.load()
                    añadir[clave](obj() if isinstance(obj, type) else obj)
                    cargados.append(f"{grupo}:{ep.name}")
                except Exception:
                    continue
        return cargados
