"""Qué exige un shaderpack de su cargador, y qué implementa el cargador que hay instalado.

Iris define un conjunto de *feature flags* —`SSBO`, `COMPUTE_SHADERS`, `CUSTOM_IMAGES`…— y un
shaderpack declara en su `shaders.properties` cuáles necesita:

    iris.features.required = CUSTOM_IMAGES SSBO COMPUTE_SHADERS
    iris.features.optional = BLOCK_EMISSION_ATTRIBUTE HIGHER_SHADOWCOLOR

Si el cargador no implementa una de las **required**, el shader no se carga: sale una pantalla
de error al seleccionarlo y no hay nada en el log que lo relacione con la versión del cargador.
Es la pregunta práctica de cualquiera que use Oculus —el puerto de Iris a Forge, que va por
detrás— y no hay forma de responderla sin abrir el juego y probar shader por shader.

**La lista de features no se codifica aquí.** Se lee del jar del cargador instalado, del enum
`FeatureFlags` que Iris y Oculus comparten. Una tabla escrita a mano quedaría vieja con cada
versión y daría veredictos falsos sobre la única cosa que se quiere saber con certeza.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

#: La clase del enum, en las rutas que ha tenido entre versiones de Iris y de Oculus.
CLASES = (
    "net/irisshaders/iris/features/FeatureFlags.class",
    "net/coderbot/iris/features/FeatureFlags.class",
)

_NOMBRE = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")

#: Valores del enum que no son capacidades.
_NO_SON_FEATURES = {"UNKNOWN", "VALUES"}


def _utf8_del_pool(datos: bytes) -> list[str]:
    """Las cadenas del *constant pool* de un `.class`, sin dependencias ni bytecode.

    Se parsea el pool y no se llama a `strings` porque el resultado tiene que ser el mismo en
    cualquier máquina: `strings` no siempre está instalado y su salida depende de su versión.
    """
    if len(datos) < 10 or datos[:4] != b"\xca\xfe\xba\xbe":
        return []
    total = int.from_bytes(datos[8:10], "big")
    i, n, fuera = 10, 1, []
    # Tamaño fijo de cada tipo de entrada; 1 (Utf8) y 15/18 son los que no lo tienen.
    tamaños = {3: 4, 4: 4, 5: 8, 6: 8, 7: 2, 8: 2, 9: 4, 10: 4, 11: 4, 12: 4, 16: 2, 17: 4,
               19: 2, 20: 2, 18: 4, 15: 3}
    while n < total and i < len(datos):
        etiqueta = datos[i]
        i += 1
        if etiqueta == 1:
            largo = int.from_bytes(datos[i:i + 2], "big")
            fuera.append(datos[i + 2:i + 2 + largo].decode("utf-8", "replace"))
            i += 2 + largo
        else:
            i += tamaños.get(etiqueta, 2)
        # Long y Double ocupan dos huecos del pool. Sin esto, el índice se desalinea y el resto
        # del pool se lee como basura.
        n += 2 if etiqueta in (5, 6) else 1
    return fuera


def loader_features(jar: Path) -> set:
    """Las capacidades que implementa el cargador de shaders de ese jar. Vacío si no lo es."""
    try:
        with zipfile.ZipFile(jar) as z:
            nombres = set(z.namelist())
            ruta = next((c for c in CLASES if c in nombres), None)
            if ruta is None:
                return set()
            cadenas = _utf8_del_pool(z.read(ruta))
    except (OSError, zipfile.BadZipFile, KeyError):
        return set()
    return {c for c in cadenas if _NOMBRE.match(c)} - _NO_SON_FEATURES


def pack_features(archive: Path) -> dict:
    """Las que el shaderpack declara necesitar y las que solo aprovecha si están."""
    try:
        with zipfile.ZipFile(archive) as z:
            nombre = next((n for n in z.namelist() if n.endswith("shaders/shaders.properties")), None)
            if nombre is None:
                return {}
            texto = z.read(nombre).decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile, KeyError):
        return {}
    fuera: dict = {}
    for clave, destino in (("required", "required"), ("optional", "optional")):
        m = re.search(rf"^\s*iris\.features\.{clave}\s*=\s*(.+)$", texto, re.M)
        if m:
            fuera[destino] = sorted({p for p in m.group(1).split() if _NOMBRE.match(p)})
    return fuera
