#!/usr/bin/env bash
# Enlaza las skills de este repo en ~/.claude, dejando el repo como fuente de verdad.
#
# Con enlace y no copia a propósito: `mod-ops` llegó a describir un modman de dos generaciones
# atrás porque la copia del sistema y la herramienta evolucionaron por separado.
set -euo pipefail
cd "$(dirname "$0")"
ORIGEN="$(pwd)"
DESTINO="${CLAUDE_SKILLS_DIR:-$HOME/.claude/local-plugins/minecraft/skills}"

estado() {
    for d in */; do
        s="${d%/}"
        [ -f "$s/SKILL.md" ] || continue
        t="$DESTINO/$s/SKILL.md"
        if [ -L "$t" ]; then
            apunta="$(readlink -f "$t")"
            if [ "$apunta" = "$ORIGEN/$s/SKILL.md" ]; then echo "  $s: enlazada a este repo"
            else echo "  $s: enlazada a OTRO sitio ($apunta)"; fi
        elif [ -f "$t" ]; then echo "  $s: hay una COPIA en el sistema, sin enlazar"
        else echo "  $s: no instalada"; fi
    done
}

if [ "${1:-}" = "--status" ]; then
    echo "origen:  $ORIGEN"; echo "destino: $DESTINO"; estado; exit 0
fi

for d in */; do
    s="${d%/}"
    [ -f "$s/SKILL.md" ] || continue
    mkdir -p "$DESTINO/$s"
    t="$DESTINO/$s/SKILL.md"
    # Una copia previa se respalda antes de sustituirla: puede tener cambios que no estén aquí.
    if [ -f "$t" ] && [ ! -L "$t" ]; then
        cp "$t" "$t.antes-de-enlazar.$(date +%Y%m%d%H%M%S)"
        echo "  $s: copia previa respaldada"
    fi
    ln -sfn "$ORIGEN/$s/SKILL.md" "$t"
    echo "  $s: enlazada"
done
echo
echo "Comprobación:"; estado
