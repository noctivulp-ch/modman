#!/usr/bin/env python3
"""Regenera las capturas del README en docs/capturas/ (como mucho tres).

    tools/capturas/capturas.sh

Monta en un directorio temporal el pack de `pack_ejemplo.py` (mods inventados, ningún pack real)
y saca tres imágenes:

- `tui-oscuro.png`: la TUI en tema oscuro, con un mod seleccionado.
- `tui-desplegar.png`: corte diagonal en tema claro entre la confirmación del despliegue y la tabla.
- `cli.png`: una sesión real de la CLI (`status`, `doctor`, `deploy --dry`) dibujada como terminal.

Textual y rich exportan SVG; Chromium (sin interfaz) lo pasa a PNG y `corte_diagonal.py` une dos
estados en una sola imagen. Necesita uv y Chromium.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from rich.console import Console
from rich.terminal_theme import MONOKAI
from rich.text import Text

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
SALIDA = RAIZ / "docs" / "capturas"
sys.path.insert(0, str(AQUI))

from corte_diagonal import componer  # noqa: E402
from pack_ejemplo import montar  # noqa: E402

from modman.tui.app import ConfirmPlan, ModmanApp  # noqa: E402

TAMANO = (110, 24)
#: DejaVu Sans Mono trae ✓, · y —; con otra, Chromium los saca de una fuente de respaldo de otro
#: ancho y las columnas se descuadran.
FUENTE = "DejaVu Sans Mono"
OSCURO, CLARO = "tokyo-night", "catppuccin-latte"
COMANDOS = ["status", "doctor", "deploy --dry"]


def a_png(svg: Path, png: Path) -> None:
    """Chromium a escala 2. La fuente web de la exportación se cambia por una local."""
    texto = re.sub(r'font-family: *"?Fira Code"?', f'font-family: "{FUENTE}"', svg.read_text())
    texto = re.sub(r"@font-face \{.*?\}", "", texto, flags=re.S)
    ancho, alto = (float(v) for v in re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', texto).groups())
    html = svg.with_suffix(".html")
    html.write_text(f"<html><body style='margin:0'>{texto}</body></html>")
    navegador = shutil.which("chromium") or shutil.which("google-chrome-stable") or "chromium"
    subprocess.run([
        navegador, "--headless", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
        "--default-background-color=00000000", f"--window-size={int(ancho)},{int(alto)}",
        f"--screenshot={png}", html.as_uri(),
    ], check=True, capture_output=True)


async def capturar_tui(config: Path, tema: str, tmp: Path, con_plan: bool) -> dict[str, Path]:
    svgs = {}
    app = ModmanApp(config)
    async with app.run_test(size=TAMANO) as pilot:
        app.theme = tema
        app.title = "modman"
        app._select("rutas-de-hierro")  # enseña una dependencia que nadie del índice cubre
        await pilot.pause(0.3)
        svgs[tema] = Path(app.save_screenshot(f"{tema}.svg", path=str(tmp)))
        if con_plan:
            await pilot.press("d")
            await pilot.pause(0.3)
            assert isinstance(app.screen, ConfirmPlan), "deploy debería pedir confirmación"
            svgs[f"{tema}-plan"] = Path(app.save_screenshot(f"{tema}-plan.svg", path=str(tmp)))
    return svgs


def capturar_cli(config: Path, png: Path) -> None:
    """Ejecuta la CLI de verdad sobre el pack de ejemplo y la dibuja como una terminal."""
    sesion = Text()
    entorno = dict(os.environ, COLUMNS="104")
    for c in COMANDOS:
        r = subprocess.run([sys.executable, "-m", "modman.cli.main", *c.split()], cwd=config.parent,
                           capture_output=True, text=True, env=entorno)
        sesion.append(f"$ modman {c}\n", "bold green")
        sesion.append(Text.from_ansi(r.stdout.rstrip() + "\n\n"))
    consola = Console(record=True, width=108, file=open(os.devnull, "w"))
    consola.print(sesion, crop=True, highlight=False, end="")
    svg = png.with_suffix(".svg")
    consola.save_svg(str(svg), title="modman · pack-de-ejemplo", theme=MONOKAI)
    a_png(svg, png)


async def principal() -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        config = montar(tmp / "pack-de-ejemplo")
        svgs = await capturar_tui(config, OSCURO, tmp, con_plan=False)
        svgs |= await capturar_tui(config, CLARO, tmp, con_plan=True)
        pngs = {k: tmp / f"{k}.png" for k in svgs}
        for k, svg in svgs.items():
            a_png(svg, pngs[k])
        shutil.copyfile(pngs[OSCURO], SALIDA / "tui-oscuro.png")
        # El plan arriba: el título y los cambios del diálogo están en su mitad superior.
        componer(Image.open(pngs[f"{CLARO}-plan"]), Image.open(pngs[CLARO]), 0.035, 3).save(
            SALIDA / "tui-desplegar.png", optimize=True)
        capturar_cli(config, tmp / "cli.png")
        shutil.copyfile(tmp / "cli.png", SALIDA / "cli.png")
    for f in sorted(SALIDA.glob("*.png")):
        print(f.relative_to(RAIZ))


if __name__ == "__main__":
    asyncio.run(principal())
