"""Pruebas de la CLI.

**Por qué existen:** esta CLI la llaman agentes y scripts, no solo personas. Si un día imprime un
código de color dentro de una tubería, o `--json` devuelve algo que no parsea, lo que se rompe está
aguas abajo y el fallo no apunta aquí. Además comprueban la degradación sin terminal, que es
justamente lo que nadie prueba a mano porque en el terminal siempre funciona.
"""

import io
import json

import pytest

from modman.cli import main as cli
from modman.cli.render import Out, color_enabled


def pack(tmp_path, *, con_servidor=True):
    (tmp_path / "modman.toml").write_text(
        f"""
[pack]
dir = "pack"
[game]
version = "1.20.1"
loader = "neoforge"
[[targets]]
name = "{'servidor' if con_servidor else 'cliente'}"
path = "destino"
accepts = ["{'server' if con_servidor else 'client'}"]
""",
        encoding="utf-8",
    )
    (tmp_path / "destino").mkdir()
    return tmp_path


def correr(argv, tmp_path=None, capsys=None):
    if tmp_path is not None:
        argv = ["--config", str(tmp_path / "modman.toml")] + argv
    code = cli.main(argv)
    salida = capsys.readouterr() if capsys else None
    return code, salida


# ------------------------------------------------------------------- básicos --
def test_sin_argumentos_muestra_ayuda_y_no_falla(capsys):
    assert cli.main([]) == 0
    assert "modman" in capsys.readouterr().out


def test_init_escribe_config_y_no_la_pisa(tmp_path, capsys):
    code, _ = correr(["init", str(tmp_path)], capsys=capsys)
    assert code == 0 and (tmp_path / "modman.toml").exists()
    code, out = correr(["init", str(tmp_path)], capsys=capsys)
    assert code == 1 and "ya existe" in out.out


def test_init_sin_servidor_declara_un_destino_de_cliente(tmp_path):
    cli.main(["init", str(tmp_path), "--client-only"])
    texto = (tmp_path / "modman.toml").read_text()
    assert 'accepts = ["client"]' in texto


def test_falta_de_config_da_error_util_y_codigo_1(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = cli.main(["status"])
    err = capsys.readouterr().err
    assert code == 1
    assert "modman.toml" in err and "modman init" in err


def test_targets_avisa_si_una_ruta_no_existe(tmp_path, capsys):
    p = pack(tmp_path)
    (p / "destino").rmdir()
    code, out = correr(["targets"], p, capsys)
    assert code == 0 and "NO" in out.out


# --------------------------------------------------------------------- json --
def test_json_es_json_valido(tmp_path, capsys):
    p = pack(tmp_path)
    code, out = correr(["--json", "targets"], p, capsys)
    assert code == 0
    datos = json.loads(out.out)
    assert datos[0]["name"] == "servidor"
    assert datos[0]["accepts"] == ["server"]


def test_json_no_lleva_codigos_de_color(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    p = pack(tmp_path)
    _, out = correr(["--json", "targets"], p, capsys)
    assert "\033" not in out.out


def test_status_en_pack_vacio_no_revienta(tmp_path, capsys):
    p = pack(tmp_path)
    code, out = correr(["status"], p, capsys)
    assert code == 0
    assert "0 mods" in out.out


# ------------------------------------------------------------- degradación --
def test_sin_terminal_no_hay_color():
    assert color_enabled(io.StringIO()) is False


def test_no_color_manda_sobre_force_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert color_enabled() is False


def test_term_dumb_desactiva_el_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert color_enabled() is False


# ------------------------------------------------------------------- tabla --
def test_la_tabla_colapsa_columnas_en_vez_de_desbordar(monkeypatch):
    monkeypatch.setenv("COLUMNS", "40")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    buf = io.StringIO()
    out = Out(stream=buf)
    out.table(
        ["slug", "prescindible", "importante"],
        [["un-slug-bastante-largo", "relleno relleno", "DATO"]],
        optional=[1],
    )
    texto = buf.getvalue()
    assert "DATO" in texto, "la columna importante no debe perderse"
    assert "prescindible" not in texto, "la columna marcada como opcional debe desaparecer primero"
    assert all(len(l) <= 80 for l in texto.splitlines())


def test_una_tabla_sin_filas_no_imprime_cabecera():
    buf = io.StringIO()
    Out(stream=buf).table(["a", "b"], [])
    assert buf.getvalue() == ""
