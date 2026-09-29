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


# ------------------------------------------------------- tipos de contenido --
def test_un_shader_va_a_su_destino_y_no_a_la_carpeta_de_mods(tmp_path):
    """El launcher no distingue; el índice sí. Un shader en mods/ no lo carga nadie."""
    from modman.core.model import Kind

    escribir_config(tmp_path, """
[pack]
dir = "pack"
[[targets]]
name = "mods"
path = "mods"
accepts = ["client"]
kinds = ["mod"]
[[targets]]
name = "shaders"
path = "shaderpacks"
accepts = ["client"]
kinds = ["shader"]
""")
    (tmp_path / "mods").mkdir()
    (tmp_path / "shaderpacks").mkdir()
    cfg = config.load(start=tmp_path)
    idx = Index(cfg.index_dir)
    idx.save(Mod(slug="comp", kind=Kind.SHADER, side=Side.CLIENT, filename="Complementary.zip"))
    idx.save(Mod(slug="jei", kind=Kind.MOD, side=Side.CLIENT, filename="jei.jar"))
    idx.reload()
    plan = Deployer(cfg, idx).plan()
    destinos = {s.slug: s.target for s in plan.steps if s.action is Action.COPY}
    assert destinos == {"comp": "shaders", "jei": "mods"}


def test_un_shader_desactivado_no_se_despliega_con_disabled(tmp_path):
    """`.disabled` solo lo entiende el cargador de mods; en shaderpacks sería basura visible."""
    from modman.core.model import Kind

    m = Mod(slug="c", kind=Kind.SHADER, state=State.OFF, filename="C.zip")
    assert m.deployed_name == ""
    assert Mod(slug="j", kind=Kind.MOD, state=State.OFF, filename="j.jar").deployed_name == "j.jar.disabled"


def test_los_zips_sueltos_de_un_shaderpack_se_detectan_como_no_gestionados(tmp_path):
    from modman.core.model import Kind

    escribir_config(tmp_path, """
[pack]
dir = "pack"
[[targets]]
name = "shaders"
path = "shaderpacks"
accepts = ["client"]
kinds = ["shader"]
""")
    (tmp_path / "shaderpacks").mkdir()
    (tmp_path / "shaderpacks" / "OtroShader.zip").write_bytes(b"x")
    cfg = config.load(start=tmp_path)
    plan = Deployer(cfg, Index(cfg.index_dir)).plan()
    assert plan.untracked["shaders"] == ["OtroShader.zip"]


def test_un_toml_sin_kind_sigue_siendo_un_mod(tmp_path):
    """Compatibilidad: los índices que ya existen no llevan `kind` y no deben cambiar."""
    from modman.core.model import Kind

    vuelta = to_raw(to_mod({"slug": "x", "side": "both", "download": {"filename": "x.jar"}}, "x"))
    assert "kind" not in vuelta
    assert to_mod({"slug": "x"}, "x").kind is Kind.MOD


# ---------------------------------------------------------------------------------------------
# Dependencias que ningún metadato declara. Los cuatro casos son reales y los cuatro fallan en
# silencio: el juego arranca y lo que no funciona se descubre mirando, que es el peor momento.
# ---------------------------------------------------------------------------------------------
def _grafo(*mods):
    from modman.core.graph import Graph

    return Graph({m.slug: m for m in mods})


def test_un_mod_que_pide_una_version_exacta_de_shader_detecta_la_equivocada():
    """El caso EuphoriaPatcher: parchea una versión concreta de Complementary.

    Si el shader instalado es otro, no hay error, ni crash, ni línea en rojo: el shader
    simplemente no se aplica. Pasó de verdad al actualizar el patcher y no el shaderpack.
    """
    from modman.core.model import Kind, Requirement

    shader = Mod(slug="comp", kind=Kind.SHADER, filename="ComplementaryReimagined_r5.6.1.zip")
    patcher = Mod(
        slug="euphoria", version_number="1.10.5-r5.9.3",
        requires=(Requirement(slug="comp", kind=Kind.SHADER, version="r5.9.3"),),
    )
    fallos = _grafo(shader, patcher).check_requirements()
    assert len(fallos) == 1 and fallos[0].blocking
    assert "r5.6.1" in fallos[0].problem

    shader.filename = "ComplementaryReimagined_r5.9.3.zip"
    assert _grafo(shader, patcher).check_requirements() == []


def test_un_cargador_de_shaders_se_cumple_con_cualquiera_de_los_dos():
    """Iris **u** Oculus: con `any_of`, uno basta y no hay que mentir eligiendo uno."""
    from modman.core.model import Kind, Requirement

    pack = Mod(
        slug="comp", kind=Kind.SHADER,
        requires=(Requirement(slug="iris", any_of=("iris", "oculus"), min_version="1.7"),),
    )
    assert _grafo(pack).check_requirements()[0].needs == "iris o oculus"
    oculus = Mod(slug="oculus", version_number="1.8.0")
    assert _grafo(pack, oculus).check_requirements() == []


def test_la_version_minima_compara_numeros_y_no_subcadenas():
    """`1.7` no puede cumplirse con `1.10` por parecerse: hay que comparar números."""
    from modman.core.model import Requirement, version_tuple

    assert version_tuple("iris-1.10.0+mc1.21.1") > version_tuple("1.7")
    pack = Mod(slug="comp", requires=(Requirement(slug="iris", min_version="1.7"),))
    assert _grafo(pack, Mod(slug="iris", version_number="1.6.9")).check_requirements()
    assert _grafo(pack, Mod(slug="iris", version_number="1.10.0")).check_requirements() == []


def test_una_dependencia_parcial_avisa_pero_no_bloquea():
    """Faithful sin Continuity carga y se ve; solo pierde las texturas conectadas.

    Si esto saliera como error, `doctor` fallaría en CI por algo cosmético y se dejaría de mirar.
    """
    from modman.core.model import Kind, Level, Requirement

    pack = Mod(
        slug="faithful", kind=Kind.RESOURCEPACK,
        requires=(Requirement(slug="continuity", any_of=("continuity", "ctm"), level=Level.PARTIAL),),
    )
    fallo = _grafo(pack).check_requirements()[0]
    assert not fallo.blocking and "[parcial]" in str(fallo)


def test_un_pack_format_de_otra_version_se_detecta_sin_abrir_el_juego():
    """Lo que el gestor del launcher no mira: el número de `pack.mcmeta` contra la versión del pack."""
    from modman.core.model import Kind

    pack = Mod(slug="faithful", kind=Kind.RESOURCEPACK, raw={"pack": {"format": 15}})
    (issue,) = _grafo(pack).check_pack_formats("1.21.1")
    assert issue.declared == 15 and issue.expected == 34 and "1.20.1" in issue.made_for
    # `supported_formats` declara un rango y hace compatible al paquete dentro de él.
    pack.raw["pack"]["supported"] = [15, 34]
    assert _grafo(pack).check_pack_formats("1.21.1") == []
    # Y de una versión que no está en la tabla no se opina, en vez de inventar un veredicto.
    del pack.raw["pack"]["supported"]
    assert _grafo(pack).check_pack_formats("1.99") == []


def test_las_dependencias_declaradas_sobreviven_al_guardado():
    from modman.core.model import Kind, Level, Requirement

    mod = Mod(slug="f", kind=Kind.RESOURCEPACK, requires=(
        Requirement(slug="continuity", any_of=("continuity", "ctm"), level=Level.PARTIAL,
                    min_version="3.0", reason="CTM"),
    ))
    vuelta = to_mod(to_raw(mod), "f").requires[0]
    assert vuelta.candidates == ("continuity", "ctm")
    assert vuelta.level is Level.PARTIAL and vuelta.min_version == "3.0"


def test_dos_mods_que_embeben_la_misma_libreria_no_chocan(tmp_path):
    """El choque de modid se mide entre los modids **propios**, no los de los jars internos.

    Encontrado desplegando de verdad: modman se negaba a poner Iris porque Kiwi y la Fabric API
    traen dentro el mismo `fabric_api_base`. Embeber la misma librería es normal —NeoForge se
    queda con la versión más alta— y bloquear por eso deja un pack sin desplegar por nada.
    """
    from modman.core.model import Scan

    cfg, idx = _pack(tmp_path, Side.CLIENT, State.ON)
    mod = idx.get("m")
    mod.scan = Scan(modids=["m", "fabric_api_base"], own=["m"])
    idx.save(mod)
    ajeno = cfg.target("cli").path / "kiwi.jar"
    ajeno.parent.mkdir(parents=True, exist_ok=True)
    ajeno.write_bytes(b"x")
    plan = Deployer(cfg, Index(cfg.index_dir), scanner=lambda p: ["kiwi", "fabric_api_base"]).plan()
    assert plan.conflicts == []
    plan2 = Deployer(cfg, Index(cfg.index_dir), scanner=lambda p: ["m"]).plan()
    assert plan2.conflicts and "m" in plan2.conflicts[0]


# ---------------------------------------------------------------------------------------------
# Stacks alternativos: cuando los requisitos no son una lista sino un menú de combinaciones
# excluyentes. Glowing Emissive Ores publica doce; la mayoría de los paquetes, ninguna.
# ---------------------------------------------------------------------------------------------
BODY_GEO = """
<summary>NeoForge Requirements</summary>

### Option 1 (1.21, 1.21.1 and 26.1 to 26.2)
- [NeoContinuity](https://modrinth.com/mod/neocontinuity/)
### Option 2 (1.21, 1.21.1 and 26.1.2)
- [Continuity](https://modrinth.com/mod/continuity)
- [Sinytra Connector](https://modrinth.com/mod/connector) - (Required for Continuity)
- [Sodium](https://modrinth.com/mod/sodium) (NeoForge Version) or [Embeddium](https://modrinth.com/mod/embeddium)

<summary>Fabric/Quilt Requirements</summary>

### Option 1 (1.18.2 to 26.2)
- [Continuity](https://modrinth.com/mod/continuity)
- [Sodium](https://modrinth.com/mod/sodium) - (Not needed, but I highly recommend this)
### Option 2 (Not recommended)
- [OptiFine](https://optifine.net/downloads)
- [OptiFabric](https://www.curseforge.com/minecraft/mc-mods/optifabric)

</details>

## Other stuff
- [Some random thread](https://discord.gg/whatever)
"""


def test_las_opciones_se_leen_de_la_descripcion_con_sus_loaders_y_versiones():
    from modman.core.reqparse import parse_options

    ops = {o.id: o for o in parse_options(BODY_GEO)}
    assert set(ops) == {"neoforge-1", "neoforge-2", "fabric-1", "fabric-2"}
    # «NeoForge Requirements» contiene la subcadena `forge`: la sección no es también de Forge.
    assert ops["neoforge-1"].loaders == ("neoforge",)
    assert ops["neoforge-1"].mc == ("1.21", "1.21.1", "26.1..26.2")
    # Dos enlaces en una viñeta son alternativas, no dos requisitos.
    assert ops["neoforge-2"].needs[-1].slugs == ("sodium", "embeddium")
    # «Not needed, but I highly recommend» no es un requisito.
    assert ops["fabric-1"].needs[-1].optional
    # Dos viñetas son dos requisitos (OptiFine **y** OptiFabric), a diferencia de dos enlaces
    # en la misma viñeta, que son alternativas.
    assert not ops["fabric-2"].recommended
    assert [n.slugs for n in ops["fabric-2"].needs] == [("optifine",), ("optifabric",)]
    # Lo de después del bloque de requisitos no es un requisito, y un enlace de Discord no es
    # un mod: sin las dos cosas, la última opción se llevaba media página.
    assert len(ops["fabric-2"].needs) == 2


def test_las_opciones_se_filtran_por_el_loader_y_la_version_del_pack():
    from modman.core.reqparse import parse_options

    ops = parse_options(BODY_GEO)
    aplican = [o.id for o in ops if o.applies("1.21.1", "neoforge")]
    assert aplican == ["neoforge-1", "neoforge-2"]
    # Un rango `a..b` se resuelve comparando números, no expandiendo versiones.
    assert [o.id for o in ops if o.applies("26.1.5", "neoforge")] == ["neoforge-1"]


def test_los_stacks_se_ordenan_por_lo_que_falta_instalar():
    """Lo que hace útil el menú no es saber si sirve, sino cuál cuesta menos."""
    from modman.core.model import Choice, Kind
    from modman.core.reqparse import parse_options

    geo = Mod(slug="geo", kind=Kind.RESOURCEPACK,
              choice=Choice(name="emisivas", options=tuple(parse_options(BODY_GEO))))
    (informe,) = _grafo(geo, Mod(slug="sodium", version_number="0.8")).check_choices("1.21.1", "neoforge")
    # Primero las que aplican, y entre ellas la de un mod antes que la de tres.
    assert [e.option.id for e in informe.statuses[:2]] == ["neoforge-1", "neoforge-2"]
    assert informe.applicable[0].missing == ["neocontinuity"]
    assert not informe.ok
    # Con el stack cubierto, el informe lo da por bueno y dice con qué.
    (ok,) = _grafo(geo, Mod(slug="neocontinuity")).check_choices("1.21.1", "neoforge")
    assert ok.ok and ok.satisfied[0].option.id == "neoforge-1"


def test_la_opcion_elegida_manda_sobre_tener_otra_cubierta():
    """Tener Continuity por otro motivo no significa que el stack elegido sea ese."""
    from modman.core.model import Choice
    from modman.core.reqparse import parse_options

    geo = Mod(slug="geo", choice=Choice(options=tuple(parse_options(BODY_GEO)), chosen="neoforge-2"))
    (informe,) = _grafo(geo, Mod(slug="neocontinuity")).check_choices("1.21.1", "neoforge")
    assert not informe.ok and "neoforge-2" in str(informe)


def test_un_jar_presente_pero_sin_indexar_sale_como_pista_con_su_prueba():
    """Un pack heredado no está indexado entero y el mod suele llevar meses en la carpeta.

    No se da por cubierto —el slug del proyecto y el modid del jar no siempre coinciden— pero sí
    se enseña el jar, y la opción deja de ordenarse como la peor de todas.
    """
    from modman.core.model import Choice, Need, Option

    geo = Mod(slug="geo", choice=Choice(options=(
        Option("a", needs=(Need(("neocontinuity",)),)),
        Option("b", needs=(Need(("fusion-connected-textures",)),)),
    )))
    (informe,) = _grafo(geo).check_choices("1.21.1", "neoforge", {"fusion": "fusion-1.3.15b.jar"})
    assert informe.statuses[0].option.id == "b"
    assert informe.statuses[0].maybe_satisfied and informe.unverified and not informe.ok
    # `sodium` no lo cubre `sodium_extra`, aunque empiece igual: sería un falso positivo.
    from modman.core.graph import _pista

    assert _pista(Need(("sodium",)), {"sodium_extra": "x.jar"}) == ""
    assert _pista(Need(("fusion-connected-textures",)), {"fusion": "f.jar"}) == "f.jar"


def test_un_paquete_que_no_documenta_nada_saca_sus_opciones_de_los_metadatos():
    """El caso normal, y el que decide si esto sirve más allá de un paquete.

    `complementary-reimagined` no dice en ninguna parte que necesite Iris —lo menciona de pasada
    en una frase— pero declara `loaders = [iris, optifine]`, y un zip de texturas lleva dentro
    las carpetas de OptiFine que usa. De ahí salen las opciones sin leer prosa.
    """
    from modman.core.features import options_from_pack

    ops = options_from_pack({"shaderpack": True}, project_loaders=("iris", "optifine"),
                            loader="neoforge")
    assert [o.id for o in ops] == ["auto-iris", "auto-oculus"]
    # En Fabric no se ofrece Oculus, que es el puerto a Forge: sería ruido delante de la buena.
    assert [o.id for o in options_from_pack({"shaderpack": True}, project_loaders=("iris",),
                                            loader="fabric")] == ["auto-iris"]
    emisivas = options_from_pack({"optifine_features": ["texturas emisivas"]}, loader="neoforge")
    assert "auto-neocontinuity" in [o.id for o in emisivas]
    assert all(o.recommended for o in emisivas if o.id != "auto-optifine")


def test_una_seccion_de_requisitos_sin_opciones_numeradas_es_una_opcion():
    """La forma corriente: `# Requirements` y una lista. GEO es la excepción, no la regla."""
    from modman.core.reqparse import parse_options

    (op,) = parse_options(
        '# Requirements\n- The mod ["Iris"](https://irisshaders.dev/download). '
        '["Optifine"](https://optifine.net/downloads) might work as well.\n'
    )
    assert op.id == "web-1" and op.needs[0].slugs == ("iris", "optifine")


def test_las_opciones_y_la_eleccion_sobreviven_al_guardado():
    from modman.core.model import Choice, Need, Option

    m = Mod(slug="geo", choice=Choice(name="emisivas", chosen="a", options=(
        Option("a", needs=(Need(("continuity",), "1.2"), Need(("sodium",), optional=True)),
               loaders=("fabric",), mc=("1.20.1", "1.21..1.21.4")),
        Option("b", needs=(Need(("optifine",)),), recommended=False),
    )))
    vuelta = to_mod(to_raw(m), "geo").choice
    assert vuelta.chosen == "a" and vuelta.name == "emisivas"
    a = vuelta.option("a")
    assert a.needs[0].min_version == "1.2" and a.needs[1].optional and a.mc == ("1.20.1", "1.21..1.21.4")
    # `recommended = false` es un dato y el volcado no debe perderlo por ser falsy.
    assert vuelta.option("b").recommended is False
