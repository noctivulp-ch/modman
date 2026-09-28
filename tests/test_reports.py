"""Pruebas del catálogo, las marcas y los jars parcheados.

**Por qué existen:** estos tres comandos son el ciclo de decisión del pack. El catálogo escribe
documentos que la gente marca, y esas marcas vuelven al índice; si se regenerara antes de leer, se
perderían y nadie se daría cuenta hasta que los estados no cuadraran. Y `patched` decide qué jar
se despliega: si el parcheado dejara de ganar al original, un `deploy` desharía el parche en
silencio, que es el tipo de fallo que se descubre cuando el servidor ya no arranca.
"""

from pathlib import Path

import pytest

from modman.core import config as _config
from modman.core.index import Index
from modman.core.manager import ManagerError, Modman
from modman.core.model import Mod, Side, State
from modman.core.reports import read_checkboxes


def pack(tmp_path, mods):
    (tmp_path / "modman.toml").write_text(
        """
[pack]
dir = "pack"
[[targets]]
name = "srv"
path = "destino"
accepts = ["server"]
[docs]
dir = "docs"
""",
        encoding="utf-8",
    )
    (tmp_path / "destino").mkdir()
    idx = Index(tmp_path / "pack" / "index")
    for m in mods:
        idx.save(m)
    return Modman(_config.load(tmp_path / "modman.toml"))


# ------------------------------------------------------------- casillas --
def test_lee_la_casilla_marcada(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text(
        "- `alfa` — algo\n  - [x] prefiero\n  - [ ] descarte\n"
        "- `beta` — otra\n  - [ ] prefiero\n  - [x] descarte\n",
        encoding="utf-8",
    )
    marcas = read_checkboxes(doc, {"prefiero": "prefer", "descarte": "discard"})
    assert marcas == {"alfa": "prefer", "beta": "discard"}


def test_si_hay_dos_marcadas_gana_la_primera(tmp_path):
    """Marcar las dos es una contradicción; quedarse con la primera al menos es predecible."""
    doc = tmp_path / "d.md"
    doc.write_text("- `alfa` — x\n  - [x] prefiero\n  - [x] descarte\n", encoding="utf-8")
    assert read_checkboxes(doc, {"prefiero": "prefer", "descarte": "discard"}) == {"alfa": "prefer"}


def test_un_documento_que_no_existe_no_es_un_error(tmp_path):
    assert read_checkboxes(tmp_path / "no.md", {"a": "a"}) == {}


# -------------------------------------------------------------- catálogo --
def test_catalog_escribe_los_tres_documentos(tmp_path):
    m = pack(tmp_path, [Mod(slug="alfa", name="Alfa", side=Side.SERVER, filename="a.jar")])
    docs, _ = m.catalog()
    assert len(docs) == 3
    for ruta in docs:
        assert ruta.exists(), f"{ruta.name} debería estar escrito"
    assert "`alfa`" in (tmp_path / "docs" / "mods-catalogo.md").read_text()


def test_catalog_dry_no_escribe_ni_documentos_ni_indice(tmp_path):
    """Un --dry que guardara las marcas estaría escribiendo justo lo que promete no tocar."""
    m = pack(tmp_path, [Mod(slug="alfa", group="g", state=State.HOLD, filename="a.jar")])
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "mods-duplicados.md").write_text(
        "- `alfa` — x\n  - [x] prefiero\n", encoding="utf-8"
    )
    docs, recogidas = m.catalog(write=False)
    assert recogidas == {"alfa": "pick=prefer"}, "debe decir qué recogería"
    assert not (tmp_path / "docs" / "mods-catalogo.md").exists()
    m.index.reload()
    assert m.index.get("alfa").raw.get("pick", "") == "", "no debe haberlo guardado"


def test_catalog_guarda_las_marcas_antes_de_regenerar(tmp_path):
    m = pack(tmp_path, [Mod(slug="alfa", group="g", state=State.HOLD, filename="a.jar")])
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "mods-duplicados.md").write_text(
        "- `alfa` — x\n  - [x] prefiero\n", encoding="utf-8"
    )
    m.catalog(force=True)  # el documento del test no lleva la marca de modman
    m.index.reload()
    assert m.index.get("alfa").raw["pick"] == "prefer"
    # y la marca sigue puesta en el documento regenerado
    assert "- [x] prefiero" in (tmp_path / "docs" / "mods-duplicados.md").read_text()


def test_no_pisa_un_documento_sin_marca(tmp_path):
    """Pasó de verdad: regenerar se llevó cuarenta líneas de diagnóstico escritas a mano."""
    m = pack(tmp_path, [Mod(slug="alfa", filename="a.jar")])
    (tmp_path / "docs").mkdir()
    doc = tmp_path / "docs" / "mods-catalogo.md"
    doc.write_text("# viejo\n\n## análisis escrito a mano que no se puede perder\n", encoding="utf-8")
    with pytest.raises(ManagerError, match="marca"):
        m.catalog()
    assert "escrito a mano" in doc.read_text(), "no debe haberlo tocado"


def test_conserva_lo_que_hay_bajo_la_marca(tmp_path):
    from modman.core.reports import MARCA

    m = pack(tmp_path, [Mod(slug="alfa", filename="a.jar")])
    (tmp_path / "docs").mkdir()
    doc = tmp_path / "docs" / "mods-catalogo.md"
    doc.write_text(f"# viejo\n\n{MARCA}\n\n## mi análisis\n\ndetalle importante\n", encoding="utf-8")
    m.catalog()
    nuevo = doc.read_text()
    assert "## mi análisis" in nuevo and "detalle importante" in nuevo
    assert "`alfa`" in nuevo, "y además regeneró lo suyo"
    assert nuevo.count(MARCA) == 1, "la marca no debe duplicarse al regenerar"


def test_force_permite_pisar_un_documento_sin_marca(tmp_path):
    m = pack(tmp_path, [Mod(slug="alfa", filename="a.jar")])
    (tmp_path / "docs").mkdir()
    doc = tmp_path / "docs" / "mods-catalogo.md"
    doc.write_text("# sin nada que conservar\n", encoding="utf-8")
    m.catalog(force=True)
    assert "`alfa`" in doc.read_text()


def test_catalog_sin_docs_dir_dice_como_arreglarlo(tmp_path):
    (tmp_path / "modman.toml").write_text(
        '[[targets]]\nname = "s"\npath = "d"\naccepts = ["server"]\n', encoding="utf-8"
    )
    (tmp_path / "d").mkdir()
    m = Modman(_config.load(tmp_path / "modman.toml"))
    with pytest.raises(ManagerError, match=r"\[docs\]"):
        m.catalog()


def test_las_descripciones_propias_ganan_al_resumen(tmp_path):
    m = pack(tmp_path, [Mod(slug="alfa", filename="a.jar", raw={"summary": "english summary"})])
    (tmp_path / "desc.toml").write_text('[descriptions]\nalfa = "lo que hace"\n', encoding="utf-8")
    m.cfg.__dict__["descriptions_file"] = tmp_path / "desc.toml"  # frozen dataclass
    docs, _ = m.catalog(write=False)
    catalogo = [v for k, v in docs.items() if "catalogo" in k.name][0]
    assert "lo que hace" in catalogo and "english summary" not in catalogo


# ------------------------------------------------------------ apply-picks --
def test_apply_picks_enciende_los_preferidos_y_apaga_los_descartados(tmp_path):
    m = pack(
        tmp_path,
        [
            Mod(slug="alfa", state=State.HOLD, group="g", filename="a.jar", raw={"pick": "prefer"}),
            Mod(slug="beta", state=State.HOLD, group="g", filename="b.jar", raw={"pick": "discard"}),
            Mod(slug="gamma", state=State.HOLD, group="g", filename="c.jar"),
        ],
    )
    m.apply_picks()
    m.index.reload()
    assert m.index.get("alfa").state is State.ON
    assert m.index.get("beta").state is State.OFF
    assert m.index.get("gamma").state is State.HOLD, "sin marcar se queda como estaba"
    assert "descartado" in m.index.get("beta").note


def test_apply_picks_no_enciende_un_mod_roto(tmp_path):
    """Encenderlo solo consigue que el servidor no arranque y que cueste ver por qué."""
    m = pack(
        tmp_path,
        [Mod(slug="alfa", state=State.HOLD, filename="a.jar", raw={"pick": "prefer", "broken": True})],
    )
    mensajes = m.apply_picks()
    m.index.reload()
    assert m.index.get("alfa").state is not State.ON
    assert any("roto" in x for x in mensajes)


def test_apply_picks_no_enciende_lo_que_depende_de_una_libreria_rota(tmp_path):
    lib = Mod(slug="libx", lib=True, filename="l.jar", raw={"broken": True})
    lib.scan.modids = ["libx"]
    usuario = Mod(slug="alfa", state=State.HOLD, filename="a.jar", raw={"pick": "prefer"})
    usuario.scan.requires = ["libx"]
    m = pack(tmp_path, [lib, usuario])
    mensajes = m.apply_picks()
    m.index.reload()
    assert m.index.get("alfa").state is not State.ON
    assert any("librerías rotas" in x for x in mensajes)


# ---------------------------------------------------------------- patched --
def test_patched_copia_el_jar_y_le_da_prioridad(tmp_path):
    m = pack(tmp_path, [Mod(slug="alfa", filename="original.jar", sha512="", side=Side.SERVER)])
    jar = tmp_path / "alfa-parcheado.jar"
    jar.write_bytes(b"parcheado")
    mod = m.register_patched("alfa", jar, note="sin el mixin que mata el cliente")
    assert mod.patched == "alfa-parcheado.jar"
    assert (m.cfg.patched_dir / "alfa-parcheado.jar").exists()
    assert mod.deployed_name == "alfa-parcheado.jar", "el parcheado gana al original"
    assert m.fetch(mod) == m.cfg.patched_dir / "alfa-parcheado.jar"


def test_si_el_jar_parcheado_desaparece_el_despliegue_se_niega(tmp_path):
    """No debe volver al original: eso desharía el parche sin que nadie se entere."""
    m = pack(tmp_path, [Mod(slug="alfa", filename="original.jar", side=Side.SERVER)])
    jar = tmp_path / "p.jar"
    jar.write_bytes(b"x")
    mod = m.register_patched("alfa", jar)
    (m.cfg.patched_dir / "p.jar").unlink()
    with pytest.raises(ManagerError, match="parcheado"):
        m.fetch(mod)


def test_si_el_jar_parcheado_cambia_tambien_se_niega(tmp_path):
    m = pack(tmp_path, [Mod(slug="alfa", filename="original.jar", side=Side.SERVER)])
    jar = tmp_path / "p.jar"
    jar.write_bytes(b"x")
    mod = m.register_patched("alfa", jar)
    (m.cfg.patched_dir / "p.jar").write_bytes(b"otra cosa")
    with pytest.raises(ManagerError, match="no cuadra"):
        m.fetch(mod)


def test_patched_con_un_jar_que_no_existe(tmp_path):
    m = pack(tmp_path, [Mod(slug="alfa", filename="o.jar")])
    with pytest.raises(ManagerError, match="no existe"):
        m.register_patched("alfa", tmp_path / "fantasma.jar")
