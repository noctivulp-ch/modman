"""Un modpack de ejemplo para las capturas: mods inventados, sin datos de ningún pack real.

    from pack_ejemplo import montar
    config = montar(Path("/tmp/algo"))   # devuelve la ruta del modman.toml

Dos destinos (servidor y cliente) con jars de mentira: zips mínimos que solo declaran su modid,
lo justo para que modman los reconozca. Hay un poco de todo lo que la TUI enseña: mods ya
desplegados, otros pendientes de desplegar, uno desactivado con su motivo, un duplicado en
`hold`, una librería, una dependencia sin cubrir y un jar suelto que no está en el índice.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from modman.core.index import Index
from modman.core.model import Mod, Scan, Side, State

CONFIG = """\
[pack]
dir = "pack"

[game]
version = "1.21.1"
loader = "neoforge"

[[targets]]
name = "servidor"
path = "servidor/mods"
accepts = ["server"]

[[targets]]
name = "cliente"
path = "cliente/mods"
accepts = ["client"]
"""

#: slug, nombre, lado, estado, versión, modids que requiere, grupo, nota, librería
MODS = [
    ("nucleo-lumen", "Núcleo Lumen", Side.BOTH, State.ON, "3.2.0", [], "", "", True),
    ("cosechas-vivas", "Cosechas Vivas", Side.BOTH, State.ON, "1.8.4", ["nucleo_lumen"], "", "", False),
    ("rutas-de-hierro", "Rutas de Hierro", Side.BOTH, State.ON, "2.0.1",
     ["nucleo_lumen", "engranajes"], "", "", False),
    ("almacen-compacto", "Almacén Compacto", Side.BOTH, State.ON, "5.1.0", [], "almacen",
     "Se queda este: el otro duplica la función y pesa el doble.", False),
    ("cajones-apilables", "Cajones Apilables", Side.BOTH, State.HOLD, "0.9.2", [], "almacen",
     "Duplicado de almacen-compacto; pendiente de probar en servidor.", False),
    ("brujula-estelar", "Brújula Estelar", Side.CLIENT, State.ON, "1.4.0", [], "", "", False),
    ("minimapa-claro", "Minimapa Claro", Side.CLIENT, State.ON, "4.6.2", ["nucleo_lumen"], "", "", False),
    ("nieblas-suaves", "Nieblas Suaves", Side.CLIENT, State.OFF, "2.2.0", [], "",
     "Rompe la niebla del Nether con el shader actual. Volver a probar en la 2.3.", False),
    ("copias-nocturnas", "Copias Nocturnas", Side.SERVER, State.ON, "1.1.3", [], "", "", False),
    ("comercio-aldeano", "Comercio Aldeano", Side.BOTH, State.ON, "3.0.0", ["nucleo_lumen"], "", "", False),
    ("faroles-del-camino", "Faroles del Camino", Side.BOTH, State.ON, "1.0.7", [], "", "", False),
    ("rendimiento-plus", "Rendimiento Plus", Side.BOTH, State.ON, "0.6.0", [], "", "", False),
]

#: Ya desplegados en cada destino; el resto aparece como pendiente («·»).
DESPLEGADOS = {
    "servidor": ["nucleo-lumen", "cosechas-vivas", "rutas-de-hierro", "almacen-compacto",
                 "copias-nocturnas", "faroles-del-camino"],
    "cliente": ["nucleo-lumen", "cosechas-vivas", "rutas-de-hierro", "almacen-compacto",
                "brujula-estelar", "minimapa-claro", "faroles-del-camino"],
}


def _modid(slug: str) -> str:
    return slug.replace("-", "_")


def _jar(ruta: Path, modid: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", f'modLoader="javafml"\n[[mods]]\nmodId="{modid}"\n')


def montar(raiz: Path) -> Path:
    raiz.mkdir(parents=True, exist_ok=True)
    (raiz / "modman.toml").write_text(CONFIG, encoding="utf-8")
    idx = Index(raiz / "pack" / "index")
    por_slug = {}
    for slug, nombre, lado, estado, version, req, grupo, nota, lib in MODS:
        m = Mod(
            slug=slug, name=nombre, side=lado, state=estado, group=grupo, note=nota, lib=lib,
            version_number=version, filename=f"{slug}-{version}.jar",
            scan=Scan(modids=[_modid(slug)], requires=req),
        )
        idx.save(m)
        por_slug[slug] = m
    for destino, slugs in DESPLEGADOS.items():
        for s in slugs:
            m = por_slug[s]
            _jar(raiz / destino / "mods" / m.deployed_name, _modid(s))
    _jar(raiz / "servidor" / "mods" / "parche-local-dev.jar", "parche_local")
    return raiz / "modman.toml"
