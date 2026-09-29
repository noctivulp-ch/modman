# Skills que acompañan a modman

La skill vive **aquí**, en git. Lo que hay en `~/.claude/` es un enlace a este archivo, así que
editarlo en el repo se nota en la siguiente invocación y el historial queda versionado con la
herramienta que describe.

| Skill | Qué cubre |
|---|---|
| [`mod-ops`](mod-ops/SKILL.md) | Gestionar, rescatar y **reparar** los mods de un modpack: updates, integrar mods nuevos, campañas de activación por olas, bisección, y recuperar un pack que se rompió tras actualizar. Cubre tres escenarios de montaje (proyecto Gradle estilo DCPort, instancia de launcher, pack ajeno) y empieza siempre por asegurar la vuelta atrás |

```bash
./skills/install.sh            # enlaza (o reenlaza) en ~/.claude
./skills/install.sh --status   # ver qué hay instalado y si apunta aquí
```

## Por qué enlace y no copia

Una copia se queda vieja en cuanto se toca uno de los dos lados, y ya pasó: `mod-ops` describía un
modman de dos generaciones atrás (`scripts/modman/modman.py`, `depmap.py`, packwiz) porque la copia
del sistema y la herramienta evolucionaron por separado. Con un enlace no hay dos versiones.

## Lo que esta skill ya no hace

No lleva la lista de comandos escrita dentro. Eso es justo lo que la desfasó dos veces, así que
remite a `modman --json help`, que la herramienta genera de su propia especificación y no puede
mentir.

## Skills que NO están aquí

`minecraft:mod-version` y `minecraft:crash-review` siguen donde estaban. Tratan de **desarrollar un
mod** —versionado semver del port, diagnóstico de crashes del cliente con `gradlew` y `run-test.sh`—
y no de gestionar un modpack. Su sitio natural es el repo del mod, no el de esta herramienta.
