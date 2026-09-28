"""Salida de la CLI: tablas alineadas, color opcional y JSON.

Sin dependencias a propósito. La CLI se usa en repos ajenos, en CI y desde agentes, y ahí cada
paquete extra es una razón más para que no arranque; una tabla alineada y unos códigos ANSI no
justifican traer Rich.

Degradación, que es la mitad del trabajo de una CLI decente:

- Si `stdout` no es un terminal —una tubería, un log de CI, un agente leyendo— no se emite ni un
  código de color, porque acabarían dentro del texto que alguien va a parsear.
- Se respetan `NO_COLOR` (cualquier valor), `FORCE_COLOR` y `TERM=dumb`.
- El ancho sale de `COLUMNS` o del terminal, con 80 como suelo: es la anchura con la que hay que
  diseñar y lo que se ve por SSH en una ventana pequeña.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any

_ROLES = {
    "acento": "\033[36m",
    "ok": "\033[32m",
    "aviso": "\033[33m",
    "error": "\033[31m",
    "suave": "\033[2m",
    "fuerte": "\033[1m",
}
_RESET = "\033[0m"


def color_enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def width() -> int:
    """Ancho útil en columnas.

    `COLUMNS` se respeta tal cual: quien lo fija sabe lo que tiene, y hay ventanas de verdad más
    estrechas que 80 —un panel partido en tmux, una terminal en el móvil—. Los 80 son el valor de
    reserva cuando no se puede averiguar nada, no un suelo que pise al usuario; eso era lo que
    hacía la primera versión y dejaba la tabla desbordada sin colapsar ni una columna.
    """
    try:
        return max(int(os.environ["COLUMNS"]), 20)
    except (KeyError, ValueError):
        pass
    return max(shutil.get_terminal_size((80, 24)).columns, 20)


class Out:
    """Escribe en stdout con o sin color, y en JSON si se pide."""

    def __init__(self, *, as_json: bool = False, stream=None) -> None:
        self.stream = stream or sys.stdout
        self.as_json = as_json
        self.color = color_enabled(self.stream) and not as_json

    # -- primitivas --
    def paint(self, text: str, role: str) -> str:
        if not self.color or role not in _ROLES:
            return text
        return f"{_ROLES[role]}{text}{_RESET}"

    def line(self, text: str = "") -> None:
        if self.as_json:
            return
        print(text, file=self.stream)

    def note(self, text: str) -> None:
        self.line(self.paint(text, "suave"))

    def warn(self, text: str) -> None:
        # Símbolo además de color: el color nunca es el único portador de significado.
        self.line(self.paint(f"! {text}", "aviso"))

    def ok(self, text: str) -> None:
        self.line(self.paint(f"✓ {text}", "ok"))

    def json(self, payload: Any) -> None:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), file=self.stream)

    # -- tabla --
    def table(
        self,
        headers: list[str],
        rows: list[list[str]],
        *,
        aligns: str = "",
        optional: list[int] | None = None,
    ) -> None:
        """Tabla de ancho mínimo que **colapsa columnas** en vez de partir líneas.

        Sin bordes: en terminal una caja cuesta filas y columnas, y la jerarquía se consigue mejor
        con la cabecera en negrita y las columnas alineadas.

        `optional` son los índices que se pueden sacrificar, en orden de menos importante a más,
        cuando no cabe. Recortar la línea a lo bruto deja la última columna a medias, y en una
        tabla de estado la última columna suele ser justo el dato que se buscaba.
        """
        if self.as_json or not rows:
            return
        headers, rows, aligns = self._collapse(headers, rows, aligns, optional or [])
        anchos = [len(h) for h in headers]
        for fila in rows:
            for i, celda in enumerate(fila):
                anchos[i] = max(anchos[i], len(_plain(str(celda))))
        limite = width()
        anchos = self._shrink(anchos, limite)
        rows = [
            [_ellipsis(str(c), anchos[i]) for i, c in enumerate(fila)] for fila in rows
        ]
        headers = [_ellipsis(h, anchos[i]) for i, h in enumerate(headers)]
        cabecera = []
        for i, h in enumerate(headers):
            cabecera.append(h.ljust(anchos[i]) if _left(aligns, i) else h.rjust(anchos[i]))
        self.line(self.paint(_trim("  ".join(cabecera), limite), "fuerte"))
        for fila in rows:
            partes = []
            for i, celda in enumerate(fila):
                texto = str(celda)
                relleno = anchos[i] - len(_plain(texto))
                partes.append(texto + " " * relleno if _left(aligns, i) else " " * relleno + texto)
            self.line(_trim("  ".join(partes), limite))


    @staticmethod
    def _shrink(anchos: list[int], limite: int) -> list[int]:
        """Encoge la columna más ancha hasta que la tabla quepa.

        Es lo que hay que hacer en vez de cortar la fila: así se pierde el final de un slug largo,
        que sigue siendo reconocible, en lugar de la última columna entera —que en `status` es
        justo el destino que se estaba comprobando—. Nunca baja de 6 caracteres: por debajo de eso
        la columna deja de decir nada y más vale que la tabla se desborde y se vea.
        """
        anchos = list(anchos)
        while sum(anchos) + 2 * (len(anchos) - 1) > limite:
            i = anchos.index(max(anchos))
            if anchos[i] <= 6:
                break
            anchos[i] -= 1
        return anchos

    def _collapse(self, headers, rows, aligns, optional):
        """Quita columnas prescindibles mientras la tabla no quepa."""
        def ancho_total(hs, rs):
            cols = [len(h) for h in hs]
            for fila in rs:
                for i, celda in enumerate(fila):
                    cols[i] = max(cols[i], len(_plain(str(celda))))
            return sum(cols) + 2 * (len(cols) - 1)

        limite = width()
        quitables = list(optional)
        while quitables and ancho_total(headers, rows) > limite:
            idx = quitables.pop(0)
            headers = [h for i, h in enumerate(headers) if i != idx]
            rows = [[c for i, c in enumerate(f) if i != idx] for f in rows]
            aligns = "".join(a for i, a in enumerate(aligns) if i != idx)
            quitables = [q - 1 if q > idx else q for q in quitables]
        return headers, rows, aligns


def _left(aligns: str, i: int) -> bool:
    return aligns[i] != "r" if i < len(aligns) else True


def _plain(text: str) -> str:
    """Longitud visible, sin los códigos de color."""
    out, dentro = [], False
    for ch in text:
        if ch == "\033":
            dentro = True
        elif dentro and ch == "m":
            dentro = False
        elif not dentro:
            out.append(ch)
    return "".join(out)


def _ellipsis(text: str, limit: int) -> str:
    """Recorta una celda con puntos suspensivos, dejando los códigos de color en paz."""
    if "\033" in text or len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)] + "…"


def _trim(text: str, limit: int) -> str:
    if len(_plain(text)) <= limit:
        return text
    # Recorte tosco pero seguro: si la línea lleva color, no se corta a media secuencia.
    if "\033" in text:
        return text
    return text[: limit - 1] + "…"
