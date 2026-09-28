"""Documentos generados desde el índice, y las casillas que vuelven de ellos.

El catálogo no es solo una salida bonita: es **la interfaz para decidir**. Los documentos de
duplicados y de caídos llevan casillas de tarea que se marcan en Obsidian o en GitHub, y al
regenerar se leen y se guardan en el índice. Por eso hay que leer antes de escribir; si no, cada
regeneración borraría lo que se acababa de marcar.

Lo que en el script original era texto fijo del pack —menciones a un servidor concreto, a su lista
de origen, a las decisiones de su usuario— sale ahora de `[docs]` en el `modman.toml`. Los valores
por defecto son genéricos y sirven a cualquier pack; un repo que quiera su propia introducción la
escribe en su configuración.
"""

from __future__ import annotations

import datetime as _dt
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .model import Mod, Side, State

_ESTADO_LARGO = {State.ON: "✅ activo", State.OFF: "⛔ off", State.HOLD: "⏸ duplicado"}
_ESTADO_CORTO = {State.ON: "activo", State.OFF: "off", State.HOLD: "espera"}
_LADO = {Side.SERVER: "servidor", Side.CLIENT: "cliente", Side.BOTH: "ambos"}

INTRO_CATALOGO = (
    "No editar a mano: se regenera desde el índice con `modman catalog`. Para cambiar algo, "
    "edita el índice (`modman set <mod> side=… state=… note=…`) y vuelve a generar."
)
INTRO_DUPLICADOS = (
    "Cada grupo reúne mods que cubren la misma función y están desplegados como `.disabled` hasta "
    "decidir. **Marca *prefiero* o *descarte*** en cada uno: son casillas de tarea, clicables en "
    "Obsidian y en GitHub, y editar el archivo a mano también vale. Las marcas se guardan en el "
    "índice la próxima vez que se regenere. Después, `modman apply-picks` y `modman deploy`."
)
INTRO_INCOMPATIBLES = (
    "Mods con `state = off` por un fallo, no por decisión. **Marca alta / media / baja** según "
    "cuánto interese recuperarlos; las marcas se guardan en el índice al regenerar."
)


def load_descriptions(path: Path | None) -> dict[str, str]:
    """Descripciones propias por slug, si el pack las tiene.

    Son datos, no código: un TOML de `slug = "qué es"`. En el script original vivían en un módulo
    de Python, lo que obligaba a importar el pack para generar su catálogo.
    """
    if not path or not path.is_file():
        return {}
    try:
        datos = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    plano = datos.get("descriptions", datos)
    return {str(k): str(v) for k, v in plano.items() if isinstance(v, str)}


# --------------------------------------------------------------- casillas --
_RE_ENTRADA = re.compile(r"- `([a-z0-9_.-]+)`")
_RE_CASILLA = re.compile(r"\s+- \[( |x|X)\] (\w+)")


def read_checkboxes(path: Path, options: dict[str, str]) -> dict[str, str]:
    """Lee las casillas marcadas de un documento generado: `slug → valor`.

    Solo cuenta la primera marcada de cada entrada. Marcar dos es una contradicción, y elegir una
    al azar sería peor que quedarse con la primera, que al menos es predecible.
    """
    if not path.exists():
        return {}
    salida: dict[str, str] = {}
    actual: str | None = None
    for linea in path.read_text(encoding="utf-8").splitlines():
        entrada = _RE_ENTRADA.match(linea)
        if entrada:
            actual = entrada.group(1)
            continue
        casilla = _RE_CASILLA.match(linea)
        if actual and casilla and casilla.group(1).lower() == "x":
            etiqueta = casilla.group(2)
            if etiqueta in options and actual not in salida:
                salida[actual] = options[etiqueta]
    return salida


# ------------------------------------------------------------- generación --
class CatalogReporter:
    """Genera el catálogo, la hoja de duplicados y la de caídos.

    Devuelve `{ruta: contenido}` y no escribe: así la CLI puede enseñar un `--dry`, la TUI
    previsualizarlo y los tests comprobarlo sin tocar el disco.
    """

    name = "catalog"

    FILES = ("mods-catalogo.md", "mods-duplicados.md", "mods-incompatibles.md")

    def report(self, mods: Iterable[Mod], context: dict[str, Any]) -> dict[Path, str]:
        mods = sorted(mods, key=lambda m: m.slug)
        destino: Path = context.get("docs_dir") or Path(".")
        desc: dict[str, str] = context.get("descriptions") or {}
        textos: dict[str, str] = context.get("texts") or {}
        hoy = context.get("today") or _dt.date.today().isoformat()
        return {
            destino / "mods-catalogo.md": self._catalogo(mods, desc, textos, hoy),
            destino / "mods-duplicados.md": self._duplicados(mods, desc, textos, hoy),
            destino / "mods-incompatibles.md": self._incompatibles(mods, desc, textos, hoy),
        }

    # -- catálogo --
    def _catalogo(self, mods: list[Mod], desc: dict, textos: dict, hoy: str) -> str:
        def fila(m: Mod) -> str:
            sugerido = m.scan.suggested_side
            aviso = f" · el escaneo dice **{sugerido.value}**" if sugerido and sugerido is not m.side else ""
            qué = desc.get(m.slug) or (m.raw.get("summary") or "")
            return (
                f"| `{m.slug}` | {qué} | {_LADO[m.side]}{aviso} | {_ESTADO_LARGO[m.state]} "
                f"| {m.version_number} | {m.note} |"
            )

        cabecera = "| mod | qué es | lado | estado | versión | notas |\n|---|---|---|---|---|---|"
        out = [
            f"# {textos.get('catalog_title', 'Catálogo de mods del pack')}",
            "",
            f"Generado el {hoy} por `modman catalog`.",
            textos.get("catalog_intro", INTRO_CATALOGO),
            "",
            "**Lado**: se decide a mano. El escaneo del jar solo sugiere, y cuando discrepa de lo "
            "decidido se indica en la columna.",
            "**Estado**: `activo` desplegado · `off` instalado como `.disabled` · `duplicado` "
            "solapa función con otro y espera decisión en [mods-duplicados.md](mods-duplicados.md).",
            "",
        ]
        grupos = (
            ("Servidor (lógica de juego; el cliente no necesita nada)", lambda m: m.side is Side.SERVER and not m.lib),
            ("Ambos lados (contenido nuevo o sincronización)", lambda m: m.side is Side.BOTH and not m.lib),
            ("Cliente (render, HUD, calidad de vida)", lambda m: m.side is Side.CLIENT and not m.lib),
            ("Librerías (arrastradas como dependencia; lado y estado los calcula `modman libs`)", lambda m: m.lib),
        )
        for titulo, pred in grupos:
            sel = [m for m in mods if pred(m)]
            out += [f"## {titulo} — {len(sel)}", "", cabecera] + [fila(m) for m in sel] + [""]

        activos = [m for m in mods if m.state is State.ON]
        out += [
            "## Resumen",
            "",
            f"- {len(mods)} mods en el índice: {len(activos)} activos, "
            f"{sum(1 for m in mods if m.state is State.HOLD)} en espera por duplicado, "
            f"{sum(1 for m in mods if m.state is State.OFF)} off.",
            f"- Servidor: {sum(1 for m in activos if m.side in (Side.SERVER, Side.BOTH))} jars activos · "
            f"Cliente: {sum(1 for m in activos if m.side in (Side.CLIENT, Side.BOTH))} jars activos.",
            "",
        ]
        return "\n".join(out)

    # -- duplicados --
    def _entrada(self, m: Mod, desc: dict, casillas: list[tuple[str, str]], valor: str) -> list[str]:
        qué = desc.get(m.slug) or (m.raw.get("summary") or "")
        cabeza = f"- `{m.slug}` — {qué} · *{_LADO[m.side]}* · {_ESTADO_CORTO[m.state]}"
        if m.note:
            cabeza += f" · {m.note}"
        return [cabeza] + [f"  - [{'x' if valor == v else ' '}] {etiqueta}" for etiqueta, v in casillas]

    def _duplicados(self, mods: list[Mod], desc: dict, textos: dict, hoy: str) -> str:
        por_grupo: dict[str, list[Mod]] = {}
        for m in mods:
            if m.group:
                por_grupo.setdefault(m.group, []).append(m)
        out = [
            f"# {textos.get('duplicates_title', 'Mods duplicados de función — decisión pendiente')}",
            "",
            f"Generado el {hoy} por `modman catalog`.",
            textos.get("duplicates_intro", INTRO_DUPLICADOS),
            "",
        ]
        for grupo, items in sorted(por_grupo.items()):
            out += [f"## {grupo}", ""]
            for m in items:
                out += self._entrada(m, desc, [("prefiero", "prefer"), ("descarte", "discard")], m.raw.get("pick", ""))
            out.append("")
        if not por_grupo:
            out += ["*(ningún mod tiene grupo de duplicados asignado; usa `modman set <mod> group=…`)*", ""]
        return "\n".join(out)

    # -- caídos --
    def _incompatibles(self, mods: list[Mod], desc: dict, textos: dict, hoy: str) -> str:
        caidos = [m for m in mods if m.state is State.OFF and not m.lib and m.raw.get("pick") != "discard"]
        out = [
            f"# {textos.get('broken_title', 'Mods caídos — prioridad de recuperación')}",
            "",
            f"Generado el {hoy} por `modman catalog`.",
            textos.get("broken_intro", INTRO_INCOMPATIBLES),
            "",
        ]
        for m in caidos:
            bloque = self._entrada(m, desc, [("alta", "alta"), ("media", "media"), ("baja", "baja")], m.raw.get("priority", ""))
            licencia = m.raw.get("license") or "?"
            fuente = m.raw.get("source") or ""
            # La licencia importa aquí y solo aquí: es lo que decide si un parche se puede
            # compartir o se queda en local.
            bloque.insert(1, f"  - licencia: {licencia}" + (f" · {fuente}" if fuente else ""))
            out += bloque
        if not caidos:
            out += ["*(ningún mod está `off` por fallo)*", ""]
        return "\n".join(out) + "\n"
