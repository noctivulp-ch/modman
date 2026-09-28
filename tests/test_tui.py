"""Pruebas de la TUI.

**Por qué existen:** una TUI se prueba a mano en un terminal, donde siempre parece funcionar, y se
rompe en los sitios donde nadie mira: al arrancar sin terminal, con el índice vacío, o cuando una
acción toca un widget desde otro hilo. Estas comprueban lo que de verdad se puede comprobar sin
ojos: que monta, que filtra, que la confirmación de despliegue existe y que ninguna acción escribe
en los destinos sin pasar por ella.

Usan el harness de Textual (`run_test`), que corre la app sin terminal real.
"""

import pytest

from modman.core.index import Index
from modman.core.model import Mod, Side, State
from modman.tui.app import ConfirmPlan, ModmanApp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def pack(tmp_path):
    (tmp_path / "modman.toml").write_text(
        """
[pack]
dir = "pack"
[game]
version = "1.20.1"
loader = "neoforge"
[[targets]]
name = "servidor"
path = "destino"
accepts = ["server"]
""",
        encoding="utf-8",
    )
    (tmp_path / "destino").mkdir()
    idx = Index(tmp_path / "pack" / "index")
    idx.save(Mod(slug="alfa", name="Alfa", side=Side.SERVER, state=State.ON, filename="alfa.jar"))
    idx.save(Mod(slug="beta", name="Beta", side=Side.CLIENT, state=State.OFF, filename="beta.jar"))
    return tmp_path / "modman.toml"


async def test_arranca_y_lista_los_mods(pack):
    app = ModmanApp(pack)
    async with app.run_test() as pilot:
        tabla = app.query_one("#tabla")
        assert tabla.row_count == 2
        assert "2 de 2 mods" in app.sub_title
        await pilot.pause()


async def test_el_filtro_reduce_las_filas(pack):
    app = ModmanApp(pack)
    async with app.run_test() as pilot:
        app.query_one("#filtro").value = "alf"
        await pilot.pause()
        assert app.query_one("#tabla").row_count == 1


async def test_el_detalle_muestra_el_mod_seleccionado(pack):
    app = ModmanApp(pack)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "alfa" in app.detail_text


async def test_alternar_estado_escribe_el_indice_pero_no_los_destinos(pack):
    """Editar es inmediato; desplegar no. Si no, una tecla mal pulsada toca la carpeta del juego."""
    app = ModmanApp(pack)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert app.manager.index.get("alfa").state is State.OFF
        destino = app.manager.cfg.target("servidor").path
        assert not list(destino.iterdir()), "no debe haber tocado el destino"


async def test_desplegar_pide_confirmacion_antes_de_tocar_nada(pack):
    app = ModmanApp(pack)
    async with app.run_test() as pilot:
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmPlan), "debe salir el modal de confirmación"
        destino = app.manager.cfg.target("servidor").path
        assert not list(destino.iterdir())
        await pilot.press("escape")
        await pilot.pause()
        assert not list(destino.iterdir()), "cancelar no debe aplicar nada"


async def test_con_el_indice_vacio_no_revienta(tmp_path):
    (tmp_path / "modman.toml").write_text(
        '[[targets]]\nname = "srv"\npath = "d"\naccepts = ["server"]\n', encoding="utf-8"
    )
    (tmp_path / "d").mkdir()
    app = ModmanApp(tmp_path / "modman.toml")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#tabla").row_count == 0
        assert app.detail_text == ""
