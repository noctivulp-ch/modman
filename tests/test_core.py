"""Pruebas del núcleo.

**Por qué existen:** el núcleo decide qué jar se copia y qué jar se borra en carpetas de mods
reales. Un fallo silencioso aquí no da un error: da un servidor que no arranca, o un cliente que
ve Reimus donde los demás ven otra cosa, y el síntoma aparece lejos de la causa.

Nada de esto toca la red ni un pack real: la configuración se construye en un directorio temporal
y el escáner se inyecta. Un test que necesite el pack del usuario no se puede ejecutar en otro sitio.
"""

from pathlib import Path

import pytest

from modman.core import config
from modman.core.deploy import Deployer
from modman.core.extensions import Extensions, Facts, SideOpinion
from modman.core.index import Index, dumps, to_mod, to_raw
from modman.core.model import Action, Mod, Side, State, Target


# ------------------------------------------------------------------ config --
def escribir_config(tmp_path: Path, cuerpo: str) -> Path:
    (tmp_path / "modman.toml").write_text(cuerpo, encoding="utf-8")
    return tmp_path / "modman.toml"


def test_config_resuelve_rutas_relativas_al_toml(tmp_path):
    escribir_config(tmp_path, """
[pack]
dir = "pack"
[[targets]]
name = "srv"
path = "server/mods"
accepts = ["server"]
""")
    cfg = config.load(start=tmp_path)
    assert cfg.index_dir == tmp_path / "pack" / "index"
    assert cfg.targets[0].path == (tmp_path / "server" / "mods").resolve()


def test_config_se_busca_hacia_arriba(tmp_path):
    """`modman status` debe funcionar desde cualquier subdirectorio, como git."""
    escribir_config(tmp_path, """
[[targets]]
name = "srv"
path = "mods"
accepts = ["server"]
""")
    hondo = tmp_path / "a" / "b" / "c"
    hondo.mkdir(parents=True)
    assert config.load(start=hondo).root == tmp_path


def test_config_sin_destinos_explica_como_arreglarlo(tmp_path):
    """Un error de configuración tiene que decir qué escribir, no solo que falta algo."""
    escribir_config(tmp_path, '[pack]\ndir = "pack"\n')
    with pytest.raises(config.ConfigError, match=r"\[\[targets\]\]"):
        config.load(start=tmp_path)


def test_config_rechaza_destinos_duplicados(tmp_path):
    escribir_config(tmp_path, """
[[targets]]
name = "srv"
path = "a"
accepts = ["server"]
[[targets]]
name = "srv"
path = "b"
accepts = ["client"]
""")
    with pytest.raises(config.ConfigError, match="únicos"):
        config.load(start=tmp_path)


# -------------------------------------------------------------------- lados --
def test_un_mod_both_llega_a_cualquier_destino():
    srv = Target("srv", Path("/s"), frozenset({Side.SERVER}))
    cli = Target("cli", Path("/c"), frozenset({Side.CLIENT}))
    assert srv.wants(Side.BOTH) and cli.wants(Side.BOTH)
    assert srv.wants(Side.SERVER) and not srv.wants(Side.CLIENT)


# -------------------------------------------------------------------- índice --
def test_el_indice_no_pierde_campos_que_no_modela(tmp_path):
    """Lo que garantiza que se puede estrenar sobre un índice ya existente."""
    raw = {
        "name": "X",
        "slug": "x",
        "side": "both",
        "state": "on",
        "license": "MIT",
        "categories": ["a", "b"],
        "version": {"id": "v1", "number": "1.0"},
        "download": {"filename": "x.jar", "sha512": "abc"},
        "scan": {"modids": ["x"], "ui_physics": ["física"]},
    }
    vuelta = to_raw(to_mod(raw, "x"))
    assert vuelta["license"] == "MIT"
    assert vuelta["categories"] == ["a", "b"]
    assert vuelta["scan"]["ui_physics"] == ["física"]


def test_dumps_mantiene_el_orden_para_que_el_diff_se_lea():
    salida = dumps({"b": 1, "a": 2, "t": {"z": 1, "y": 2}})
    assert salida.index("b = 1") < salida.index("a = 2") < salida.index("[t]")


def test_resolver_ambiguo_es_error_no_una_apuesta(tmp_path):
    idx = Index(tmp_path)
    for slug in ("mod-uno", "mod-dos"):
        idx.save(Mod(slug=slug, name=slug))
    idx.reload()
    assert idx.resolve("mod-uno") == "mod-uno"
    with pytest.raises(Exception, match="ambiguo"):
        idx.resolve("mod-")


# ------------------------------------------------------------------ despliegue --
def _pack(tmp_path: Path, side: Side, state: State) -> tuple[config.Config, Index]:
    escribir_config(tmp_path, f"""
[pack]
dir = "pack"
[[targets]]
name = "srv"
path = "srv"
accepts = ["server"]
[[targets]]
name = "cli"
path = "cli"
accepts = ["client"]
""")
    (tmp_path / "srv").mkdir()
    (tmp_path / "cli").mkdir()
    cfg = config.load(start=tmp_path)
    idx = Index(cfg.index_dir)
    idx.save(Mod(slug="m", name="M", side=side, state=state, filename="m.jar", sha512=""))
    idx.reload()
    return cfg, idx


def test_plan_manda_un_mod_de_servidor_solo_al_servidor(tmp_path):
    cfg, idx = _pack(tmp_path, Side.SERVER, State.ON)
    plan = Deployer(cfg, idx).plan()
    copias = [s for s in plan.steps if s.action is Action.COPY]
    assert [s.target for s in copias] == ["srv"]


def test_un_mod_off_se_despliega_como_disabled(tmp_path):
    cfg, idx = _pack(tmp_path, Side.BOTH, State.OFF)
    plan = Deployer(cfg, idx).plan()
    assert all(s.path.name == "m.jar.disabled" for s in plan.steps if s.action is Action.COPY)


def test_hold_se_despliega_igual_que_off(tmp_path):
    cfg, idx = _pack(tmp_path, Side.BOTH, State.HOLD)
    plan = Deployer(cfg, idx).plan()
    assert all(s.path.name.endswith(".disabled") for s in plan.steps if s.action is Action.COPY)


def test_un_jar_ajeno_con_el_mismo_modid_bloquea_el_despliegue(tmp_path):
    """Dos jars con el mismo modid tumban el juego; más vale no desplegar y avisar."""
    cfg, idx = _pack(tmp_path, Side.SERVER, State.ON)
    mod = idx.get("m")
    mod.scan.modids = ["mimod"]
    idx.save(mod)
    ajeno = cfg.target("srv").path / "otro.jar"
    ajeno.write_bytes(b"x")
    dep = Deployer(cfg, idx, scanner=lambda p: ["mimod"] if p.name == "otro.jar" else [])
    plan = dep.plan()
    assert plan.conflicts and "mimod" not in str(plan.changes)
    assert not [s for s in plan.changes if s.target == "srv"]


def test_los_jars_ajenos_se_informan_y_no_se_tocan(tmp_path):
    cfg, idx = _pack(tmp_path, Side.SERVER, State.ON)
    (cfg.target("srv").path / "ajeno.jar").write_bytes(b"x")
    plan = Deployer(cfg, idx).plan()
    assert plan.untracked["srv"] == ["ajeno.jar"]
    assert not [s for s in plan.changes if s.path.name == "ajeno.jar"]


def test_plan_no_escribe_nada(tmp_path):
    """`--dry` es gratis porque planificar y aplicar están separados."""
    cfg, idx = _pack(tmp_path, Side.BOTH, State.ON)
    antes = {p.name for p in cfg.target("srv").path.iterdir()}
    Deployer(cfg, idx).plan()
    assert {p.name for p in cfg.target("srv").path.iterdir()} == antes


def test_apply_copia_de_forma_atomica_y_sin_restos(tmp_path):
    cfg, idx = _pack(tmp_path, Side.SERVER, State.ON)
    origen = tmp_path / "origen.jar"
    origen.write_bytes(b"contenido")
    dep = Deployer(cfg, idx)
    plan = dep.plan()
    assert dep.apply(plan, fetcher=lambda _m: origen) == 1
    destino = cfg.target("srv").path / "m.jar"
    assert destino.read_bytes() == b"contenido"
    assert not list(cfg.target("srv").path.glob(".*tmp"))


# ---------------------------------------------------------------- extensiones --
class _Roto:
    name = "roto"

    def analyze(self, jar):
        raise RuntimeError("revienta")


class _Bueno:
    name = "bueno"

    def analyze(self, jar):
        return {"dato": 1}


def test_un_analizador_roto_no_tumba_a_los_demas_ni_se_silencia(tmp_path):
    """El fallo se aísla pero queda registrado: esconderlo costó un bug en el desarrollo."""
    ext = Extensions()
    ext.add_analyzer(_Roto())
    ext.add_analyzer(_Bueno())
    facts = ext.analyze(tmp_path / "x.jar")
    assert facts.get("dato") == 1
    assert "error" in facts.of("roto")
    assert ext.errors and "roto" in ext.errors[0]


def test_el_empate_entre_lados_se_resuelve_a_both():
    """Un mod de más en un lado no hace nada; uno de menos rompe la partida."""
    class A:
        name = "a"
        def advise(self, mod, facts): return SideOpinion(Side.SERVER, "porque a", 0.5)
    class B:
        name = "b"
        def advise(self, mod, facts): return SideOpinion(Side.CLIENT, "porque b", 0.5)
    ext = Extensions()
    ext.add_advisor(A())
    ext.add_advisor(B())
    cons = ext.consensus(Mod(slug="m"), Facts(jar=Path("x")))
    assert cons.side is Side.BOTH and "empate" in cons.reason


def test_gana_el_asesor_con_mas_confianza():
    class Fuerte:
        name = "fuerte"
        def advise(self, mod, facts): return SideOpinion(Side.BOTH, "contenido", 0.9)
    class Debil:
        name = "debil"
        def advise(self, mod, facts): return SideOpinion(Side.CLIENT, "declarado", 0.3)
    ext = Extensions()
    ext.add_advisor(Debil())
    ext.add_advisor(Fuerte())
    assert ext.consensus(Mod(slug="m"), Facts(jar=Path("x"))).side is Side.BOTH
