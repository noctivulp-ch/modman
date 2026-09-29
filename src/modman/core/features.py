"""Qué mod implementa cada capacidad, por loader. La tabla que hace esto extrapolable.

El caso que motivó todo —*Glowing Emissive Ores*— publica sus doce combinaciones en la
descripción, ordenadas y por loader. **Es la excepción.** La mayoría de los paquetes no dice
nada: `faithful-32x` no menciona ningún requisito y `complementary-reimagined` lo despacha con
una frase suelta. Si la detección dependiera de leer la prosa, funcionaría en un paquete y
fallaría en los otros mil.

Lo que sí es común a todos es **lo que llevan dentro** y **lo que declaran como metadato**:

- un shaderpack declara en Modrinth para qué cargadores es (`iris`, `optifine`, `canvas`), y de
  ahí sale su requisito real sin leer una línea de texto;
- un paquete de recursos lleva las carpetas de OptiFine que usa (`optifine/ctm`,
  `emissive.properties`, `optifine/cit`, `optifine/random`), y cada una de esas capacidades la
  reimplementa un mod distinto **según el loader**.

Eso es lo que hay aquí: capacidad → formas de cubrirla, cada una con los loaders donde vale. El
conocimiento del dominio vive en esta tabla, no en la página de cada paquete, así que un paquete
que no documente nada obtiene las mismas opciones que uno que lo documente todo.

Mantener esto es mantener una tabla: cuando aparezca otro mod de texturas conectadas, se añade
una fila y lo hereda todo el catálogo.
"""

from __future__ import annotations

from .model import Need, Option

#: Loaders que comparten ecosistema de mods. NeoForge carga mods de Forge en 1.20.1 y anteriores,
#: y con Sinytra Connector también mods de Fabric, pero eso no se da por hecho: quien lo use lo
#: declara en la opción correspondiente.
FABRICOS = ("fabric", "quilt")

#: `capacidad → opciones que la cubren`. El orden es el de preferencia cuando varias aplican:
#: primero lo nativo del loader, al final lo que el ecosistema desaconseja.
CAPACIDADES: dict[str, tuple] = {
    "texturas conectadas": (
        ("continuity", FABRICOS, ("continuity",), True, "nativo de Fabric"),
        ("neocontinuity", ("neoforge",), ("neocontinuity",), True, "puerto de Continuity a NeoForge"),
        ("ctm", ("forge",), ("ctm",), True, "ConnectedTexturesMod, el clásico de Forge"),
        ("fusion", (), ("fusion-connected-textures",), True, "funciona en los tres loaders"),
        ("continuity-connector", ("neoforge", "forge"),
         ("continuity", "connector", "sodium|embeddium"), True,
         "Continuity de Fabric vía Sinytra Connector; más piezas y más frágil"),
        ("optifine", (), ("optifine",), False, "incompatible con Sodium y compañía"),
    ),
    "texturas emisivas": (
        ("continuity", FABRICOS, ("continuity",), True, "Continuity cubre emisivas desde 1.18.2"),
        ("neocontinuity", ("neoforge",), ("neocontinuity",), True, ""),
        ("etf", (), ("entitytexturefeatures",), True, "ETF; para entidades, no para bloques"),
        ("fusion", (), ("fusion-connected-textures",), True, ""),
        ("moremcmeta", ("fabric", "forge"), ("moremcmeta", "moremcmeta-emissive"), True, ""),
        ("optifine", (), ("optifine",), False, ""),
    ),
    "texturas de ítem (CIT)": (
        ("cit-resewn", FABRICOS, ("cit-resewn",), True, ""),
        ("cit-resewn-connector", ("neoforge", "forge"), ("cit-resewn", "connector"), True, ""),
        ("optifine", (), ("optifine",), False, ""),
    ),
    "entidades aleatorias": (
        ("etf", (), ("entitytexturefeatures",), True, ""),
        ("optifine", (), ("optifine",), False, ""),
    ),
    #: Un shaderpack no se puede ni seleccionar sin uno de estos.
    "cargador de shaders (iris)": (
        ("iris", ("fabric", "quilt", "neoforge"), ("iris",), True,
         "Iris; hay puerto oficial a NeoForge desde 1.21.1"),
        ("oculus", ("forge", "neoforge"), ("oculus",), True, "Oculus, el puerto de Iris a Forge"),
    ),
    "cargador de shaders (optifine)": (
        ("optifine", ("forge",), ("optifine",), False, ""),
        ("optifabric", FABRICOS, ("optifine", "optifabric"), False, ""),
    ),
    "cargador de shaders (canvas)": (
        ("canvas", FABRICOS, ("canvas",), True, ""),
    ),
}

#: `loader declarado del shaderpack en Modrinth → capacidad que necesita`.
SHADER_LOADERS = {
    "iris": "cargador de shaders (iris)",
    "optifine": "cargador de shaders (optifine)",
    "canvas": "cargador de shaders (canvas)",
}


def options_for(capacidad: str, *, loader: str = "", prefix: str = "auto") -> list[Option]:
    """Las formas de cubrir una capacidad, filtradas por el loader del pack.

    El filtro se aplica aquí y no al mostrar porque una opción de otro loader no es «una opción
    que no aplica»: es ruido que empuja hacia abajo la que sirve. Sin loader se devuelven todas,
    que es lo que hace falta para documentar.
    """
    fuera: list[Option] = []
    for oid, loaders, needs, recomendada, nota in CAPACIDADES.get(capacidad, ()):
        if loader and loaders and loader.lower() not in loaders:
            continue
        fuera.append(
            Option(
                id=f"{prefix}-{oid}",
                needs=tuple(Need.parse(n) for n in needs),
                loaders=tuple(loaders),
                recommended=recomendada,
                note=f"{capacidad}" + (f" — {nota}" if nota else ""),
            )
        )
    return fuera


def options_from_pack(pack_facts: dict, *, kind_is_shader: bool = False,
                      project_loaders: tuple = (), loader: str = "") -> list[Option]:
    """Las opciones que se deducen **sin leer prosa**: del zip y de los metadatos.

    Es el camino que funciona para cualquier paquete, incluidos los que no documentan nada.
    """
    fuera: list[Option] = []
    if kind_is_shader or pack_facts.get("shaderpack"):
        capacidades = [SHADER_LOADERS[l] for l in project_loaders if l in SHADER_LOADERS]
        # Un shaderpack sin loaders declarados necesita Iris igualmente: es lo que hay hoy.
        for cap in capacidades or ["cargador de shaders (iris)"]:
            fuera += options_for(cap, loader=loader)
    for etiqueta in pack_facts.get("optifine_features") or ():
        fuera += options_for(etiqueta, loader=loader)
    # Dos capacidades pueden proponer la misma opción (Continuity cubre conectadas y emisivas):
    # se queda la primera, que lleva la nota de la capacidad más específica que la pidió.
    vistos: dict[str, Option] = {}
    for o in fuera:
        vistos.setdefault(o.id, o)
    return list(vistos.values())
