"""Leer los requisitos de una descripción de Modrinth y convertirlos en opciones.

Hay paquetes cuyos requisitos no son una lista de mods sino **un menú**: varias combinaciones
excluyentes, cada una para un loader y un rango de versiones. *Glowing Emissive Ores* publica
doce entre NeoForge, Forge y Fabric, y elegir mal significa instalar tres mods que no hacen
nada. Eso no cabe en ningún metadato: vive en la descripción, escrito para personas.

Este módulo la lee. La forma que reconoce es la que usan de hecho esas páginas:

    <summary>NeoForge Requirements</summary>
    ### Option 1 (1.21, 1.21.1 and 26.1 to 26.2)
    - [NeoContinuity](https://modrinth.com/mod/neocontinuity/)
    ### Option 2 (1.21, 1.21.1 and 26.1.2)
    - [Continuity](https://modrinth.com/mod/continuity)
    - [Sodium](https://modrinth.com/mod/sodium) (NeoForge Version) or [Embeddium](...)

**Es un parser de texto libre y se equivoca.** Por eso no escribe nada en el índice: propone, y
lo revisa quien decide, igual que las opiniones de lado. Un requisito inventado a partir de una
frase mal leída sería indistinguible de uno comprobado en cuanto pasa una semana.
"""

from __future__ import annotations

import re

from .model import Need, Option

#: `<summary>NeoForge Requirements</summary>` y variantes; también un encabezado normal.
_SECCION = re.compile(r"(?:<summary>|#{2,4}\s*)([\w/ ]{2,30}?)\s+Requirements\s*(?:</summary>)?", re.I)
#: Un encabezado de requisitos sin opciones numeradas, que es la forma corriente. Se aceptan
#: varias palabras porque cada autor la escribe a su manera y la sección es la misma.
_SECCION_PLANA = re.compile(
    r"^\s*(?:<summary>|#{1,5}\s*)\s*(?:[\w/ ]{0,20}?\b)?"
    r"(requirements?|requisitos?|dependencies|dependencias|required\s+mods?|needed\s+mods?)\b",
    re.I | re.M,
)
_OPCION = re.compile(r"^#{2,5}\s*Option\s*(\d+)\s*(?:\(([^)]*)\))?\s*$", re.I | re.M)
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_ENLACE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_SLUG_MODRINTH = re.compile(r"modrinth\.com/(?:mod|resourcepack|shader|datapack|plugin)/([\w-]+)")

LOADERS = {"neoforge": "neoforge", "forge": "forge", "fabric": "fabric", "quilt": "quilt"}

#: Sitios de los que un enlace **sí** puede ser un mod, y cómo sale su slug. Sin esta lista
#: blanca, un «refer to this thread for more info» acababa convertido en una dependencia con
#: slug propio, y una dependencia inventada es peor que una que falta.
_SITIOS = (
    (re.compile(r"curseforge\.com/minecraft/mc-mods/([\w-]+)"), None),
    (re.compile(r"optifine\.net"), "optifine"),
    (re.compile(r"irisshaders\.(?:dev|net)"), "iris"),
)

#: Frases que degradan un requisito a recomendación. El autor las escribe entre paréntesis
#: detrás del enlace, y tratarlas como obligatorias hace instalar mods que no hacen falta.
_BLANDAS = ("not needed", "highly recommend", "recommended", "only required if", "only needed if")


def _loaders_de(titulo: str) -> tuple:
    """Los loaders que nombra un encabezado.

    «NeoForge Requirements» contiene la subcadena `forge`, así que la búsqueda ingenua etiquetaba
    la sección de NeoForge como válida también para Forge y mezclaba dos ecosistemas. Se busca
    como palabra y `neoforge` se consume antes.
    """
    t = titulo.lower()
    encontrados = []
    for k, v in LOADERS.items():
        if re.search(rf"(?<![a-z]){k}(?![a-z])", t):
            encontrados.append(v)
            t = t.replace(k, " ")
    return tuple(dict.fromkeys(encontrados))


def parse_versions(texto: str) -> tuple:
    """`"1.21, 1.21.1 and 26.1 to 26.2"` → `("1.21", "1.21.1", "26.1..26.2")`.

    Un rango se guarda como `a..b` y se resuelve comparando números al consultarlo, no aquí:
    expandirlo exigiría conocer todas las versiones de Minecraft que existen y que existirán.
    """
    if not texto:
        return ()
    if "not recommended" in texto.lower():
        return ()
    piezas = re.split(r",|\band\b|\bor\b|\+|/", texto)
    fuera: list[str] = []
    for p in piezas:
        p = p.strip().strip(".")
        if not p:
            continue
        m = re.match(r"^([\d.]+)\s+to\s+([\d.]+)$", p, re.I)
        if m:
            fuera.append(f"{m.group(1)}..{m.group(2)}")
        elif re.match(r"^[\d.]+$", p):
            fuera.append(p)
    return tuple(fuera)


def _hasta_el_final_de_la_opcion(texto: str) -> str:
    """Corta donde acaba la lista de la opción.

    Sin esto, la última opción de una sección se come las viñetas de lo que venga después: en
    Glowing Emissive Ores se llevaba los paquetes de terceros del final de la página como si
    fueran requisitos de OptiFine.
    """
    lineas: list[str] = []
    for l in texto.splitlines():
        desnuda = l.strip().lower()
        if desnuda.startswith(("</details>", "<details>", "<summary>", "#")):
            break
        lineas.append(l)
    return "\n".join(lineas)


def _parece(texto: str, slug: str) -> bool:
    """¿El nombre del enlace y su slug hablan del mismo mod?

    En esta misma página hay un enlace escrito «Continuity» que apunta a *connectedness*. El
    parser no puede saber cuál quiso el autor, pero sí avisar de que no cuadran.
    """
    palabras = {w for w in re.split(r"[^a-z0-9]+", texto.lower()) if len(w) > 3}
    partes = set(slug.split("-"))
    return not palabras or bool(palabras & partes) or any(w in slug for w in palabras)


def _necesidad(linea: str) -> Need | None:
    """Una viñeta → un `Need`. Varios enlaces en la misma viñeta son alternativas."""
    enlaces = _ENLACE.findall(linea)
    if not enlaces:
        return None
    slugs: list[str] = []
    reconocidos: list[str] = []
    for texto, url in enlaces:
        m = _SLUG_MODRINTH.search(url)
        if m:
            slugs.append(m.group(1))
            continue
        # OptiFine y compañía no están en Modrinth: se reconocen por su sitio, para que la
        # opción no aparezca incompleta, aunque `add` no vaya a poder instalarla.
        for patron, fijo in _SITIOS:
            if (mm := patron.search(url)):
                slugs.append(fijo or mm.group(1))
                break
    if not slugs:
        return None
    resto = _ENLACE.sub("", linea).lower()
    nota = " ".join(_ENLACE.sub(lambda m: m.group(1), linea).split())
    dudosos = [t for (t, u) in enlaces
               for sl in [_SLUG_MODRINTH.search(u)]
               if sl and not _parece(t, sl.group(1))]
    if dudosos:
        nota = f"[revisar: el enlace de «{dudosos[0]}» apunta a otro proyecto] " + nota
    return Need(
        slugs=tuple(dict.fromkeys(slugs)),
        optional=any(f in resto for f in _BLANDAS),
        note=nota,
    )


def parse_flat(body: str) -> list[Option]:
    """Una sección de requisitos sin opciones numeradas → una sola opción.

    Es la forma corriente: `# Requirements` y una lista. `bliss-shader` lo escribe así y
    *Glowing Emissive Ores* es la excepción, no la regla, de modo que sin esto el parser solo
    acertaría en el paquete que lo motivó.
    """
    salida: list[Option] = []
    for i, m in enumerate(_SECCION_PLANA.finditer(body)):
        cuerpo = _hasta_el_final_de_la_opcion(body[m.end():])
        needs = [n for n in (_necesidad(l) for l in cuerpo.splitlines() if _BULLET.match(l)) if n]
        if needs:
            loaders = _loaders_de(m.group(0))
            salida.append(
                Option(id=f"web-{i + 1}", needs=tuple(needs), loaders=loaders,
                       note=" ".join(m.group(0).split()).strip("# "))
            )
    return salida


def parse_options(body: str) -> list[Option]:
    """Todas las opciones que declara una descripción, por loader.

    El id es `<loader>-<n>` porque el autor numera desde 1 en cada sección y «Option 2» sin más
    es ambiguo en cuanto hay tres loaders.
    """
    if not body:
        return []
    secciones: list[tuple[int, tuple]] = [
        (m.start(), _loaders_de(m.group(1))) for m in _SECCION.finditer(body)
    ]
    salida: list[Option] = []
    marcas = list(_OPCION.finditer(body))
    if not marcas:
        return parse_flat(body)
    for i, m in enumerate(marcas):
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(body)
        cuerpo = _hasta_el_final_de_la_opcion(body[m.end():fin])
        loaders = next((l for pos, l in reversed(secciones) if pos < m.start()), ())
        parens = m.group(2) or ""
        needs = [n for n in (_necesidad(l) for l in cuerpo.splitlines() if _BULLET.match(l)) if n]
        if not needs:
            continue
        prefijo = loaders[0] if loaders else "any"
        salida.append(
            Option(
                id=f"{prefijo}-{m.group(1)}",
                needs=tuple(needs),
                loaders=loaders,
                mc=parse_versions(parens),
                recommended="not recommended" not in parens.lower(),
                note=parens.strip(),
            )
        )
    return salida
