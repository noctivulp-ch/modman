"""TUI de modman: mirar el pack con calma y actuar sin memorizar slugs.

Existe porque con doscientos mods la CLI contesta bien a preguntas concretas y mal a la pregunta
que se hace de verdad: «¿qué hay aquí y qué está raro?». Un `status` de 191 filas se lee peor que
una tabla que se puede filtrar y ordenar.

No tiene lógica propia: todo pasa por `core.manager.Modman`, el mismo que usa la CLI. Si esta
pantalla decidiera algo por su cuenta, acabaría discrepando de la CLI y el pack tendría dos
verdades.

Dos cosas deliberadas, de las reglas de UX de terminal:

- **Confirmar el plan antes de desplegar.** `deploy` toca carpetas de mods de verdad, y aquí es
  barato enseñar exactamente qué va a pasar. Es la ventaja de que el núcleo planifique aparte.
- **Los atajos siempre visibles** abajo, y `q`/`Esc` hacen lo que todo el mundo espera.
"""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Static

from ..core.manager import Modman
from ..core.model import Action, Plan, Side, State


class ConfirmPlan(ModalScreen[bool]):
    """Enseña el plan de despliegue y pide confirmación.

    Un modal y no un atajo directo: el resto de la interfaz es de lectura, y esta es la única
    acción que escribe en carpetas que el juego va a leer.
    """

    BINDINGS = [
        Binding("escape,n", "dismiss(False)", "cancelar"),
        Binding("y,enter", "dismiss(True)", "aplicar"),
    ]

    def __init__(self, plan: Plan) -> None:
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Se van a aplicar {len(self.plan.changes)} cambios", id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                for s in self.plan.changes[:200]:
                    simbolo = {Action.COPY: "+", Action.REMOVE: "-"}.get(s.action, "~")
                    yield Static(f"{simbolo} [{s.target}] {s.path.name}  ({s.reason})", markup=False)
                for c in self.plan.conflicts:
                    yield Static(f"! {c}", classes="warn", markup=False)
            yield Label("enter/y aplicar · esc/n cancelar", id="dialog-help")


class ModmanApp(App[None]):
    """Pantalla principal: tabla de mods a la izquierda, detalle a la derecha."""

    CSS = """
    Screen { layout: vertical; }
    #filtro { dock: top; height: 3; }
    #cuerpo { height: 1fr; }
    #tabla { width: 2fr; }
    #detalle { width: 1fr; border-left: solid $panel; padding: 0 1; }
    #aviso { dock: bottom; height: auto; color: $warning; }
    .warn { color: $warning; }
    #dialog { width: 80%; height: 80%; border: round $accent; background: $surface; padding: 1; }
    #dialog-title { text-style: bold; }
    #dialog-help { color: $text-muted; }
    """

    BINDINGS = [
        Binding("q,escape", "quit", "salir"),
        Binding("slash", "focus_filter", "filtrar"),
        Binding("r", "reload", "recargar"),
        Binding("d", "deploy", "desplegar"),
        Binding("o", "doctor", "revisar deps"),
        Binding("s", "toggle_state", "on/off"),
        Binding("f", "cycle_side", "cambiar lado"),
    ]

    def __init__(self, config: Path | None = None) -> None:
        super().__init__()
        self.manager = Modman.open(config)
        self._filtro = ""
        # El texto del panel se guarda además de pintarse: así se puede comprobar sin hurgar en
        # la API interna del widget, que cambia entre versiones de Textual.
        self.detail_text = ""

    # ------------------------------------------------------------- montaje --
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Input(placeholder="filtrar por slug, nombre o jar…", id="filtro")
        with Horizontal(id="cuerpo"):
            yield DataTable(id="tabla", cursor_type="row", zebra_stripes=True)
            yield VerticalScroll(Static("", id="detalle-texto"), id="detalle")
        yield Static("", id="aviso")
        yield Footer()

    def on_mount(self) -> None:
        tabla = self.query_one("#tabla", DataTable)
        tabla.add_columns("slug", "lado", "estado", *[t.name for t in self.manager.cfg.active_targets])
        self.refresh_rows()
        tabla.focus()

    # --------------------------------------------------------------- datos --
    def refresh_rows(self) -> None:
        tabla = self.query_one("#tabla", DataTable)
        seleccionado = self._current_slug()
        tabla.clear()
        st = self.manager.status()
        f = self._filtro.lower()
        visibles = 0
        for fila in st.rows:
            m = fila.mod
            if f and f not in m.slug.lower() and f not in m.name.lower() and f not in m.filename.lower():
                continue
            marcas = []
            for t in st.targets:
                v = fila.present[t]
                marcas.append({"on": "✓", "falta": "·", "—": "—"}.get(v, v))
            tabla.add_row(m.slug, m.side.value, m.state.value, *marcas, key=m.slug)
            visibles += 1
        self.sub_title = f"{visibles} de {len(st.rows)} mods · {self.manager.cfg.root.name}"
        sueltos = sum(len(v) for v in st.untracked.values())
        self.query_one("#aviso", Static).update(
            f"{sueltos} jars sin gestionar en los destinos (no se tocan)" if sueltos else ""
        )
        if seleccionado:
            self._select(seleccionado)
        self._update_detail()

    def _current_slug(self) -> str | None:
        tabla = self.query_one("#tabla", DataTable)
        if tabla.row_count == 0:
            return None
        try:
            fila = tabla.coordinate_to_cell_key(tabla.cursor_coordinate)
            return str(fila.row_key.value)
        except Exception:
            return None

    def _select(self, slug: str) -> None:
        tabla = self.query_one("#tabla", DataTable)
        try:
            tabla.move_cursor(row=tabla.get_row_index(slug))
        except Exception:
            pass

    def _update_detail(self) -> None:
        slug = self._current_slug()
        panel = self.query_one("#detalle-texto", Static)
        if not slug or slug not in self.manager.index:
            self.detail_text = ""
            panel.update("")
            return
        m = self.manager.index.get(slug)
        lineas = [
            f"[b]{m.name or m.slug}[/b]",
            f"{m.slug}",
            "",
            f"lado    {m.side.value}",
            f"estado  {m.state.value}",
            f"versión {m.version_number}",
            f"jar     {m.filename}",
        ]
        if m.lib:
            lineas.append("librería (su lado lo fija `libs`)")
        if m.group:
            lineas.append(f"grupo   {m.group}")
        if m.note:
            lineas += ["", f"[i]{m.note}[/i]"]
        if m.scan.requires:
            proveedores = self.manager.graph().providers
            lineas += ["", "[b]necesita[/b]"]
            for d in sorted(m.scan.requires)[:12]:
                quien = proveedores.get(d)
                lineas.append(f"  {d} → {quien or '[red]nadie del índice[/red]'}")
        self.detail_text = "\n".join(lineas)
        panel.update(self.detail_text)

    # -------------------------------------------------------------- eventos --
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filtro":
            self._filtro = event.value
            self.refresh_rows()

    def on_input_submitted(self) -> None:
        self.query_one("#tabla", DataTable).focus()

    def on_data_table_row_highlighted(self) -> None:
        self._update_detail()

    # -------------------------------------------------------------- acciones --
    def action_focus_filter(self) -> None:
        self.query_one("#filtro", Input).focus()

    def action_reload(self) -> None:
        self.manager.index.reload()
        self.refresh_rows()
        self.notify("índice recargado del disco")

    def action_doctor(self) -> None:
        problemas = self.manager.doctor()
        if not problemas:
            self.notify("cada mod activo tiene sus dependencias en su lado", severity="information")
            return
        for p in problemas[:6]:
            self.notify(str(p), severity="warning", timeout=10)
        if len(problemas) > 6:
            self.notify(f"y {len(problemas) - 6} más; usa `modman doctor`", severity="warning")

    def action_toggle_state(self) -> None:
        """Alterna `on`/`off`. Es la edición más frecuente con diferencia."""
        slug = self._current_slug()
        if not slug:
            return
        m = self.manager.index.get(slug)
        nuevo = State.OFF if m.state is State.ON else State.ON
        self.manager.edit(slug, state=nuevo)
        self.refresh_rows()
        self.notify(f"{slug}: {nuevo.value} (aún no desplegado; pulsa d)")

    def action_cycle_side(self) -> None:
        slug = self._current_slug()
        if not slug:
            return
        m = self.manager.index.get(slug)
        orden = [Side.SERVER, Side.CLIENT, Side.BOTH]
        nuevo = orden[(orden.index(m.side) + 1) % len(orden)]
        self.manager.edit(slug, side=nuevo)
        self.refresh_rows()
        self.notify(f"{slug}: lado {nuevo.value} (aún no desplegado; pulsa d)")

    @work(exclusive=True, thread=True)
    def _apply(self, plan: Plan) -> None:
        """Aplicar en un hilo: copiar doscientos jars bloquearía la interfaz.

        El resultado se devuelve al hilo de la interfaz con `call_from_thread`; escribir en los
        widgets desde aquí es justo lo que no se debe hacer.
        """
        try:
            hechos = self.manager.apply_deploy(plan)
        except Exception as e:  # el fallo se enseña, no tumba la app
            self.call_from_thread(self.notify, f"error al desplegar: {e}", severity="error", timeout=15)
            return
        self.call_from_thread(self.notify, f"{hechos} cambios aplicados", severity="information")
        self.call_from_thread(self.refresh_rows)

    def action_deploy(self) -> None:
        plan = self.manager.plan_deploy()
        if not plan.changes:
            self.notify("los destinos ya reflejan el índice")
            for c in plan.conflicts:
                self.notify(c, severity="warning", timeout=12)
            return

        def responder(confirmado: bool | None) -> None:
            if confirmado:
                self._apply(plan)

        self.push_screen(ConfirmPlan(plan), responder)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="modman-tui",
        description="Interfaz de pantalla completa de modman. Para scripts y agentes, usa `modman`.",
    )
    p.add_argument("--config", help="ruta de un modman.toml concreto")
    a = p.parse_args(argv)
    ModmanApp(Path(a.config) if a.config else None).run()
    return 0
