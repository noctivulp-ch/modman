"""`pack_format`: por qué Minecraft marca un paquete de texturas como incompatible.

Un paquete de recursos declara en su `pack.mcmeta` un número de formato, y cada versión del juego
espera el suyo. Cuando no coinciden, **el paquete se puede cargar igual**: aparece en la lista
bajo «Incompatible», el juego avisa y, en la práctica, casi siempre funciona —los formatos cambian
por detalles de estructura, no por el contenido—. Ese «casi» es el problema: nadie sabe si el
paquete que está usando es de su versión o de tres anteriores, y cuando algo se ve mal no hay por
dónde empezar.

Ningún launcher lo comprueba, porque para el launcher un `.zip` en `resourcepacks/` es un archivo.
Aquí se comprueba, porque el índice sí sabe qué versión del juego corre el pack.

**Esta tabla envejece.** Cada versión de Minecraft añade una fila, así que se actualiza a mano y
lo que no esté en ella no se juzga: preferimos «no lo sé» a un veredicto inventado, que es peor
que no comprobar nada.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

#: `versión de Minecraft → pack_format de recursos`. Varias versiones comparten formato.
RESOURCE_FORMATS: dict[str, int] = {
    "1.6.1": 1, "1.7": 1, "1.7.10": 1, "1.8": 1, "1.8.9": 1,
    "1.9": 2, "1.10": 2, "1.10.2": 2,
    "1.11": 3, "1.11.2": 3, "1.12": 3, "1.12.1": 3, "1.12.2": 3,
    "1.13": 4, "1.13.2": 4, "1.14": 4, "1.14.4": 4,
    "1.15": 5, "1.15.2": 5, "1.16": 5, "1.16.1": 5,
    "1.16.2": 6, "1.16.3": 6, "1.16.4": 6, "1.16.5": 6,
    "1.17": 7, "1.17.1": 7,
    "1.18": 8, "1.18.1": 8, "1.18.2": 8,
    "1.19": 9, "1.19.1": 9, "1.19.2": 9,
    "1.19.3": 12,
    "1.19.4": 13,
    "1.20": 15, "1.20.1": 15,
    "1.20.2": 18,
    "1.20.3": 22, "1.20.4": 22,
    "1.20.5": 32, "1.20.6": 32,
    "1.21": 34, "1.21.1": 34,
    "1.21.2": 42, "1.21.3": 42,
    "1.21.4": 46,
    "1.21.5": 55,
}

#: Lo mismo para datapacks, que llevan su propia numeración. Solo las filas verificadas: una
#: versión que falte sale como «sin tabla» y no como incompatible.
DATA_FORMATS: dict[str, int] = {
    "1.16.2": 6, "1.16.5": 6,
    "1.17": 7, "1.17.1": 7,
    "1.18": 8, "1.18.1": 8,
    "1.18.2": 9,
    "1.19": 10, "1.19.2": 10, "1.19.3": 10,
    "1.19.4": 12,
    "1.20": 15, "1.20.1": 15,
    "1.20.2": 18,
}


def expected_format(mc_version: str, *, data: bool = False) -> int | None:
    """El `pack_format` que espera esa versión del juego, o `None` si no está en la tabla."""
    tabla = DATA_FORMATS if data else RESOURCE_FORMATS
    return tabla.get((mc_version or "").strip())


def versions_for(fmt: int, *, data: bool = False) -> list[str]:
    """Qué versiones del juego usan ese formato. Para poder decir «esto es de 1.20.1»."""
    tabla = DATA_FORMATS if data else RESOURCE_FORMATS
    return [v for v, f in tabla.items() if f == fmt]


def read_pack_meta(archive: Path) -> dict:
    """Lee `pack.mcmeta` de un `.zip` (o de una carpeta ya descomprimida).

    Devuelve `{}` si no lo tiene: un shaderpack normalmente no lleva `pack.mcmeta`, y eso es un
    dato y no un error.
    """
    try:
        if archive.is_dir():
            f = archive / "pack.mcmeta"
            crudo = f.read_bytes() if f.is_file() else None
        else:
            with zipfile.ZipFile(archive) as z:
                nombre = next((n for n in z.namelist() if n.endswith("pack.mcmeta") and n.count("/") <= 1), None)
                crudo = z.read(nombre) if nombre else None
    except (OSError, zipfile.BadZipFile, KeyError):
        return {}
    if not crudo:
        return {}
    try:
        datos = json.loads(crudo.decode("utf-8-sig", "replace")).get("pack") or {}
    except (ValueError, AttributeError):
        return {}
    salida: dict = {}
    if isinstance(datos.get("pack_format"), int):
        salida["format"] = datos["pack_format"]
    # `supported_formats` (desde 1.20.2) declara un rango y hace compatible al paquete en todo él.
    sop = datos.get("supported_formats")
    if isinstance(sop, list) and len(sop) == 2:
        salida["supported"] = [int(sop[0]), int(sop[1])]
    elif isinstance(sop, dict) and "min_inclusive" in sop:
        salida["supported"] = [int(sop["min_inclusive"]), int(sop["max_inclusive"])]
    elif isinstance(sop, int):
        salida["supported"] = [sop, sop]
    if isinstance(datos.get("description"), str):
        salida["description"] = datos["description"][:200]
    return salida


def compatible(meta: dict, esperado: int) -> bool:
    """¿El juego lo dará por compatible, o lo listará bajo «Incompatible»?"""
    rango = meta.get("supported")
    if rango:
        return rango[0] <= esperado <= rango[1]
    fmt = meta.get("format")
    return fmt is None or fmt == esperado
