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


# ------------------------------------------------- shaders y paquetes de recursos --
class PackAnalyzer:
    """Mira dentro de un `.zip` que no es un mod: shaderpack o paquete de recursos.

    Es el analizador que responde a lo que el gestor de un launcher no sabe. De un `.zip` suelto
    en `resourcepacks/` o `shaderpacks/` se puede saber bastante más de lo que enseña su nombre:

    - **`pack.mcmeta`** dice para qué versión del juego se hizo. Con eso se sabe, *antes* de
      abrir el juego, si va a aparecer bajo «Incompatible» (ver `packmeta`).
    - **Una carpeta `shaders/`** en la raíz lo delata como shaderpack, y entonces necesita un
      cargador —Iris u Oculus— que rara vez está escrito en ninguna parte.
    - **Carpetas de OptiFine** (`optifine/ctm`, `emissive.properties`, `optifine/cit`,
      `optifine/random`) marcan contenido que en Fabric/NeoForge **no hace nada** sin el mod que
      lo reimplementa: Continuity o CTM para las texturas conectadas, ETF/EMF para emisivas y
      entidades, CIT Resewn para los ítems. El paquete carga igual y se ve a medias, que es el
      fallo más difícil de atribuir de los tres.

    No decide nada: deja los hechos y `suggested_requirements` propone las dependencias.
    """

    name = "packscan"

    #: `marca en el zip → (slugs que lo implementan, qué contenido queda inerte sin ellos)`.
    OPTIFINE = (
        (("/optifine/ctm/", "/optifine/ctm.properties", "/mcpatcher/ctm/"),
         ("continuity", "ctm"), "texturas conectadas"),
        (("/optifine/emissive.properties", "_e.png"),
         ("entitytexturefeatures", "entity_texture_features"), "texturas emisivas"),
        (("/optifine/cit/", "/mcpatcher/cit/"), ("cit-resewn",), "texturas de ítem (CIT)"),
        (("/optifine/random/", "/optifine/mob/"), ("entitytexturefeatures",), "entidades aleatorias"),
    )

    def analyze(self, jar: Path) -> dict[str, Any]:
        import zipfile

        from . import packmeta

        datos: dict[str, Any] = {}
        meta = packmeta.read_pack_meta(jar)
        if meta:
            datos["pack"] = meta
        try:
            with zipfile.ZipFile(jar) as z:
                nombres = z.namelist()
        except Exception:
            return datos
        raiz = {n.split("/", 1)[0] for n in nombres if "/" in n}
        if "shaders" in raiz or any(n.startswith("shaders/") for n in nombres):
            datos["shaderpack"] = True
            # Iris lee `shaders.properties`; una marca `iris.` dentro es un shader que ya usa
            # extensiones suyas y no va a funcionar en un OptiFine puro.
            props = next((n for n in nombres if n.endswith("shaders/shaders.properties")), None)
            if props:
                try:
                    with zipfile.ZipFile(jar) as z:
                        texto = z.read(props).decode("utf-8", "replace")
                    if "iris." in texto:
                        datos["iris_only"] = True
                except Exception:
                    pass
        elif any(n.startswith("assets/") for n in nombres):
            datos["resourcepack"] = True
        marcas: list[str] = []
        unido = "\n".join("/" + n for n in nombres)
        for patrones, _slugs, etiqueta in self.OPTIFINE:
            if any(p in unido for p in patrones):
                marcas.append(etiqueta)
        if marcas:
            datos["optifine_features"] = marcas
        return datos


def suggested_requirements(facts_pack: dict[str, Any]) -> list:
    """De lo observado en un `.zip` a las dependencias que habría que declarar.

    Son propuestas, como las opiniones de lado: se enseñan con su motivo y las escribe quien
    decide. La versión mínima de Iris no se inventa —depende de la versión del juego— y por eso
    se deja vacía en lugar de poner un número que envejezca mal.
    """
    from .model import Kind, Level, Requirement

    salida: list = []
    if facts_pack.get("shaderpack"):
        salida.append(
            Requirement(
                slug="iris",
                any_of=("iris", "oculus"),
                kind=Kind.MOD,
                reason="un shaderpack necesita un cargador de shaders; en NeoForge es Oculus",
            )
        )
    etiquetas = facts_pack.get("optifine_features") or []
    for patrones, slugs, etiqueta in PackAnalyzer.OPTIFINE:
        if etiqueta in etiquetas:
            salida.append(
                Requirement(
                    slug=slugs[0],
                    any_of=tuple(slugs),
                    kind=Kind.MOD,
                    level=Level.PARTIAL,
                    reason=f"lleva {etiqueta} de OptiFine: sin el mod que las reimplementa, carga sin ellas",
                )
            )
    return salida


def default_extensions(*, deep_scan: bool = True):
    """Las extensiones de fábrica ya registradas, más lo que aporten otros paquetes.

    Se llaman los entry points al final para que un plugin pueda sustituir un módulo de fábrica
    registrando uno con el mismo nombre.
    """
    from .extensions import Extensions

    ext = Extensions()
    ext.add_analyzer(JarscanAnalyzer(deep=deep_scan))
    ext.add_analyzer(PackAnalyzer())
    for advisor in (ContentAdvisor(), PhysicsUIAdvisor(), NetworkChannelAdvisor(), DeclaredSideAdvisor()):
        ext.add_advisor(advisor)
    ext.load_entry_points()
    return ext
