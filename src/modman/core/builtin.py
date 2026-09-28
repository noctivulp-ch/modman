"""Los módulos que vienen de fábrica, escritos contra la misma superficie que los futuros.

No hay atajos para estos: `jarscan` se engancha como cualquier analizador y las reglas de lado son
cuatro asesores independientes. Si uno estuviera cableado por dentro, la superficie sería decorado
y el primer módulo de verdad descubriría que no encaja.

Las reglas de lado son la decisión del usuario del 2026-09-22, y ahora cada una vota por separado
con su motivo, en vez de resolverse dentro de una función. Así se puede desactivar una, añadir otra
para un pack concreto, o ver por qué discrepan dos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import jarscan
from .extensions import Facts, SideOpinion
from .model import Mod, Side


def _lista(valor) -> list[str]:
    """Normaliza a lista ordenada.

    `jarscan` devuelve `set` en varias claves, y un set no admite slicing ni tiene orden estable.
    Sin esto, un asesor que quiera enseñar «las tres primeras señales» revienta, y el orden del
    motivo cambiaría entre ejecuciones ensuciando el diff del índice.
    """
    if valor is None:
        return []
    if isinstance(valor, (str, bytes)):
        return [valor.decode() if isinstance(valor, bytes) else valor]
    try:
        return sorted(str(v) for v in valor)
    except TypeError:
        return [str(valor)]


class JarscanAnalyzer:
    """Lo que se puede saber abriendo el jar: modids, dependencias y señales.

    `deep` cuesta bastante (recorre las clases), así que el escaneo completo es opción y el
    barato —solo el manifiesto y los metadatos— es el que usa el desplegador para detectar
    choques de modid.
    """

    name = "jarscan"

    def __init__(self, *, deep: bool = True) -> None:
        self.deep = deep

    def analyze(self, jar: Path) -> dict[str, Any]:
        return jarscan.scan_jar(jar, deep=self.deep)


class ContentAdvisor:
    """Regla 1: si registra bloques, ítems o mobs, hace falta en los dos lados.

    El cliente necesita el registro para conocerlos y dibujarlos, y las traducciones. Es la regla
    más fiable de las cuatro —un `block.*` en el lang no admite otra lectura—, de ahí la confianza
    alta.
    """

    name = "contenido"

    def advise(self, mod: Mod, facts: Facts) -> SideOpinion | None:
        content = _lista(facts.get("content"))
        if not content:
            return None
        return SideOpinion(Side.BOTH, f"registra contenido ({', '.join(content[:3])})", 0.9)


class PhysicsUIAdvisor:
    """Regla 2: si toca físicas o mecánicas con interfaz, en los dos lados.

    Con el mod en un solo lado el otro predice distinto y se ve a tirones. No es un fallo que
    aparezca en ningún log, así que conviene que salte aquí.
    """

    name = "fisica-ui"

    def advise(self, mod: Mod, facts: Facts) -> SideOpinion | None:
        señales = _lista(facts.get("ui_physics"))
        if not señales:
            return None
        return SideOpinion(Side.BOTH, f"toca física o interfaz ({', '.join(señales[:3])})", 0.8)


class NetworkChannelAdvisor:
    """Regla 3: un canal de red **obligatorio** exige el mod en los dos lados.

    Un `PayloadRegistrar` sin `optional()` hace que el cliente sin el mod no pueda ni conectarse;
    con `optional()` el cliente se conecta igual y el mod se apaña.

    `net_channel` toma tres valores en el índice real —vacío (140 mods), `optional` (31) y
    `required` (20)—, así que hay que distinguirlos: la primera versión de este asesor opinaba con
    los tres y mandaba a `both` 31 mods que no lo necesitan. Se vio comparando su consenso contra
    los lados ya decididos a mano.
    """

    name = "canal-red"

    OBLIGATORIOS = {"required", "mandatory", "true"}

    def advise(self, mod: Mod, facts: Facts) -> SideOpinion | None:
        canal = str(facts.get("net_channel", "") or "").strip().lower()
        if not canal:
            return None
        if canal not in self.OBLIGATORIOS:
            # Opcional: es un dato, no un motivo para forzar el lado.
            return None
        return SideOpinion(Side.BOTH, "registra un canal de red obligatorio", 0.95)


class DeclaredSideAdvisor:
    """Lo que el propio jar declara. Es la pista más débil, y a veces miente.

    `clientSideOnly` y `displayTest` son declaraciones del autor, no hechos: hay mods marcados
    como solo-cliente que registran bloques. Por eso va con poca confianza y pierde contra los
    asesores que miran el contenido.
    """

    name = "declarado"

    def advise(self, mod: Mod, facts: Facts) -> SideOpinion | None:
        # Nombres tal y como los devuelve jarscan, no como quedan luego en el TOML del índice.
        if facts.get("declared_client_only"):
            return SideOpinion(Side.CLIENT, "el jar se declara clientSideOnly", 0.4)
        data = _lista(facts.get("data_dirs"))
        assets = _lista(facts.get("asset_dirs"))
        refs = facts.get("client_ref_outside", 0) or 0
        if data and not assets and not refs:
            return SideOpinion(Side.SERVER, "solo trae data/ y no referencia el cliente", 0.35)
        if assets and not data:
            return SideOpinion(Side.CLIENT, "solo trae assets/ (modelos o texturas)", 0.3)
        return None


def default_extensions(*, deep_scan: bool = True):
    """Las extensiones de fábrica ya registradas, más lo que aporten otros paquetes.

    Se llaman los entry points al final para que un plugin pueda sustituir un módulo de fábrica
    registrando uno con el mismo nombre.
    """
    from .extensions import Extensions

    ext = Extensions()
    ext.add_analyzer(JarscanAnalyzer(deep=deep_scan))
    for advisor in (ContentAdvisor(), PhysicsUIAdvisor(), NetworkChannelAdvisor(), DeclaredSideAdvisor()):
        ext.add_advisor(advisor)
    ext.load_entry_points()
    return ext
