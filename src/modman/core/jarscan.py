#!/usr/bin/env python3
"""jarscan — qué hay dentro de un jar de mod: modids, dependencias, señales de riesgo y LADO real.

Heredado de `scripts/modman/depmap.py` de DeceasedCraft (parser de neoforge.mods.toml externo + jarjar internos,
fabric.mod.json, flags WRAPPER/CORESERVICE/FABRIC_ONLY) y ampliado con el análisis de lado, porque lo que declaran los
mods en Modrinth (client_side/server_side) falla a menudo. Señales:
  - `clientSideOnly = true` o `displayTest = IGNORE_ALL_VERSION|IGNORE_SERVER_VERSION` en el toml → el mod se declara cliente.
  - `data/<modid>/…` (recetas, loot, worldgen, tags, estructuras…) → lógica de servidor (datapack embebido).
  - `assets/<modid>/models|textures|blockstates` → recursos que el cliente necesita si el mod añade bloques/ítems.
  - clases fuera de paquetes `client` que referencian `net/minecraft/client/` → riesgo de NoClassDefFoundError en servidor.
  - registra canales de red (`PayloadRegistrar`) sin `optional()` → el otro lado debe llevar el mod (veredicto both).
  - claves de idioma `block.*` / `item.*` / `entity.*` → añade contenido → both (el cliente necesita el registro).
  - nombres de mixin sobre física (Entity, Block, VoxelShape…) o interfaz (Screen, Menu, Hud…) → señal `ui_physics`,
    informativa: si además trae datos y recursos, both (si solo está en un lado, el otro predice distinto).
Veredicto `side_scan`: client | server | both | both? (dudoso). Es una guía; la prueba real es el boot y el join.

Uso: jarscan.py <jar> [...] [--json]
"""
import io
import json
import re
import sys
import tomllib
import zipfile
from pathlib import Path

BUILTIN = {"minecraft", "neoforge", "forge", "java"}
DATA_SERVER = ("recipe", "recipes", "loot_table", "loot_tables", "worldgen", "structure", "structures", "advancement",
               "advancements", "tags", "enchantment", "damage_type", "dimension", "function", "functions")
CLIENT_PKG = re.compile(r"(^|/)(client|render|renderer|gui|screen|screens|hud|shader|particles?)(/|$)")


def parse_toml_bytes(data: bytes):
    try:
        return tomllib.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return None


def scan_jar(path: Path, deep: bool = True) -> dict:
    # `provides` incluye los modids de los jars internos (jar-in-jar); `own` solo los que el
    # jar declara como propios. La diferencia importa: dos mods pueden embeber la misma librería
    # sin chocar —NeoForge se queda con la versión más alta—, y confundirlos hacía que modman se
    # negara a desplegar Iris porque Kiwi trae dentro el mismo `fabric_api_base`.
    info = {"jar": path.name, "provides": set(), "own": set(), "incompatible": {}, "required": {}, "optional": {}, "flags": [], "mixin_configs": 0,
            "declared_client_only": False, "display_test": None, "data_dirs": set(), "asset_dirs": set(),
            "classes": 0, "client_ref_outside": 0, "client_ref_classes": [], "required_side": {}, "net_channel": "", "content": set(), "ui_physics": set()}
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        info["flags"].append(f"UNREADABLE:{e}")
        return info
    names = set(z.namelist())

    def absorb(toml, entry_names, outer):
        if not toml:
            return
        for mod in toml.get("mods", []):
            mid = mod.get("modId")
            if mid:
                info["provides"].add(mid)
                if outer:
                    info["own"].add(mid)
            for p in (mod.get("provides") or []):
                info["provides"].add(p)
                if outer:
                    info["own"].add(p)
            if outer:
                if mod.get("clientSideOnly") is True or toml.get("clientSideOnly") is True:
                    info["declared_client_only"] = True
                dt = mod.get("displayTest") or toml.get("displayTest")
                if dt:
                    info["display_test"] = dt
        mixins = toml.get("mixins", [])
        info["mixin_configs"] += len(mixins)
        if outer:
            for mx in mixins:
                cfg = mx.get("config")
                if cfg and cfg not in entry_names:
                    info["flags"].append("WRAPPER")
                    break
        for _mid, dlist in (toml.get("dependencies") or {}).items():
            if not isinstance(dlist, list):
                continue
            for d in dlist:
                did = d.get("modId")
                if not did or did in BUILTIN:
                    continue
                rng = d.get("versionRange", "*")
                # Un rango imposible no es una dependencia: es una **incompatibilidad
                # declarada**. Es como Forge y NeoForge dicen «si esto está instalado, no
                # arranco», y el modismo es literal: Embeddium 0.3.31 declara
                # `textrues_embeddium_options` con `[0.0.0-NOT-COMPATIBLE]` porque absorbió
                # ese mod. Leerlo como dependencia lleva a instalarlo, que es justo lo que
                # rompe el arranque: «Unsupported installed optional dependencies».
                if "NOT-COMPATIBLE" in rng.upper() or rng.replace(" ", "") in ("[,)", "(,)"):
                    info["incompatible"][did] = rng
                    continue
                # `type` es el campo de NeoForge; los mods de Forge 1.20.1 y anteriores usan
                # `mandatory`. Mirar solo `type` daba por obligatorio **todo** lo declarado por
                # un mod de Forge, incluidas sus dependencias opcionales.
                if (tipo := d.get("type")):
                    dtype = str(tipo).lower()
                else:
                    dtype = "required" if d.get("mandatory", True) else "optional"
                (info["required"] if dtype == "required" else info["optional"])[did] = rng
                if outer and dtype == "required":
                    info["required_side"][did] = str(d.get("side") or "BOTH").upper()   # en qué lado hace falta la dep

    entry = "META-INF/neoforge.mods.toml" if "META-INF/neoforge.mods.toml" in names else \
            ("META-INF/mods.toml" if "META-INF/mods.toml" in names else None)
    if entry:
        absorb(parse_toml_bytes(z.read(entry)), names, True)
    elif "fabric.mod.json" in names:
        info["flags"].append("FABRIC_ONLY")
        try:
            fj = json.loads(z.read("fabric.mod.json"))
            if fj.get("id"):
                info["provides"].add(fj["id"])
                info["own"].add(fj["id"])
        except Exception:
            pass
    for svc in ("META-INF/services/cpw.mods.modlauncher.api.ITransformationService",
                "META-INF/services/net.neoforged.neoforgespi.locating.IDependencyLocator"):
        if svc in names:
            info["flags"].append("CORESERVICE")
            break
    for n in names:
        # jars internos: NeoForge los coloca en META-INF/jarjar/ y algunos mods en META-INF/jars/ (LambDynamicLights)
        if (n.startswith("META-INF/jarjar/") or n.startswith("META-INF/jars/")) and n.endswith(".jar"):
            try:
                inner = zipfile.ZipFile(io.BytesIO(z.read(n)))
                inames = set(inner.namelist())
                ie = "META-INF/neoforge.mods.toml" if "META-INF/neoforge.mods.toml" in inames else \
                     ("META-INF/mods.toml" if "META-INF/mods.toml" in inames else None)
                if ie:
                    absorb(parse_toml_bytes(inner.read(ie)), inames, False)
            except Exception:
                pass

    # contenido nuevo (bloques/ítems/mobs): claves de idioma del propio mod y modelos/blockstates → el cliente lo necesita
    for n in names:
        if "/lang/" in n and n.endswith(".json") and n.startswith("assets/"):
            try:
                for k in json.loads(z.read(n)).keys():
                    if k.startswith("block."):
                        info["content"].add("bloques")
                    elif k.startswith("item."):
                        info["content"].add("items")
                    elif k.startswith("entity."):
                        info["content"].add("mobs")
            except Exception:
                pass
    # mixins que tocan física o interfaz: por el nombre de la clase mixin (heurística, informativa)
    PHYS = ("Entity", "LivingEntity", "Player", "Block", "VoxelShape", "Collision", "Fluid", "Boat")
    UI = ("Screen", "Menu", "Gui", "Hud", "Inventory", "Container")
    for n in names:
        if "/mixin" in n.lower() and n.endswith(".class"):
            base = n.split("/")[-1][:-6]
            if any(b in base for b in UI):
                info["ui_physics"].add("ui")
            elif any(b in base for b in PHYS):
                info["ui_physics"].add("física")

    ids = info["provides"] or {"?"}
    for n in names:
        parts = n.split("/")
        if len(parts) >= 3 and parts[0] == "data" and parts[1] in ids:
            if parts[2]: info["data_dirs"].add(parts[2])
        elif len(parts) >= 3 and parts[0] == "assets" and parts[1] in ids:
            if parts[2]: info["asset_dirs"].add(parts[2])
    if deep:
        needle = b"net/minecraft/client/"
        for n in names:
            if not n.endswith(".class"):
                continue
            info["classes"] += 1
            # canal de red NeoForge: si registra payloads sin marcarlos optional(), el otro lado DEBE llevar el mod
            # (kick "This channel is missing on the client side, but required on the server" — JamLib, 2026-09-22)
            data = z.read(n)
            if b"network/registration/PayloadRegistrar" in data or b"RegisterPayloadHandlersEvent" in data:
                if info["net_channel"] != "optional":
                    info["net_channel"] = "optional" if b"optional" in data else "required"
            # físicas y mecánicas con interfaz: solo cuenta si el mixin apunta ahí (nombre de clase mixin), ver mixin_targets
            if CLIENT_PKG.search(n.lower()):
                continue
            try:
                if needle in z.read(n):
                    info["client_ref_outside"] += 1
                    if len(info["client_ref_classes"]) < 5:
                        info["client_ref_classes"].append(n)
            except Exception:
                pass
    info["side_scan"] = verdict(info)
    return info


def verdict(i: dict) -> str:
    if i["net_channel"] == "required":
        return "both"            # canal obligatorio: hace falta en cliente y servidor pase lo que pase
    if {"bloques", "items", "mobs"} & i["content"]:
        return "both"            # registra contenido: el cliente lo necesita para conocerlo y dibujarlo
    server_data = any(d in DATA_SERVER for d in i["data_dirs"])
    client_assets = bool({"models", "textures", "blockstates", "shaders", "particles"} & i["asset_dirs"])
    declared_client = i["declared_client_only"] or (i["display_test"] or "").upper() in ("IGNORE_ALL_VERSION", "IGNORE_SERVER_VERSION")
    if declared_client and not server_data:
        # IGNORE_SERVER_VERSION lo usan tanto mods de cliente como de servidor puro ("no hace falta en el otro lado")
        if not i["declared_client_only"] and i["client_ref_outside"] == 0 and not client_assets:
            return "server"
        if i["declared_client_only"] or client_assets or i["client_ref_outside"] > 0.1 * max(i["classes"], 1):
            return "client"
        return "both?"
    if server_data and client_assets:
        return "both"
    if i["ui_physics"] and client_assets and server_data:
        return "both"            # toca físicas o interfaz y trae datos y recursos: los dos lados deben coincidir
    if server_data and not client_assets:
        return "server"          # datapack embebido / lógica; cliente opcional
    if not server_data and (client_assets or i["client_ref_outside"] > 0) and i["classes"]:
        return "client" if i["client_ref_outside"] > 0.3 * max(i["classes"], 1) else "both?"
    return "both?"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = [scan_jar(Path(a)) for a in args]
    if "--json" in sys.argv:
        print(json.dumps([{**o, "provides": sorted(o["provides"]), "data_dirs": sorted(o["data_dirs"]),
                           "asset_dirs": sorted(o["asset_dirs"])} for o in out], indent=1, ensure_ascii=False))
        return
    for o in out:
        print(f"{o['jar']}\n  modids {sorted(o['provides'])}  lado={o['side_scan']}  clientOnly={o['declared_client_only']} displayTest={o['display_test']}"
              f"\n  data={sorted(o['data_dirs'])}\n  assets={sorted(o['asset_dirs'])}\n  clases={o['classes']} refs cliente fuera de /client/={o['client_ref_outside']} {o['client_ref_classes']}"
              f"\n  required={o['required']} optional={list(o['optional'])} flags={o['flags']} mixins={o['mixin_configs']}")


if __name__ == "__main__":
    main()
