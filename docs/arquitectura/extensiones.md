# Los cuatro enganches de extensión

modman se amplía sin tocar el núcleo. Hay cuatro puntos, cada uno con un contrato explícito en
`core/extensions.py`, y los módulos de fábrica están escritos contra ellos igual que lo estaría uno
externo: si alguno estuviera cableado por dentro, la superficie sería decorado y el primer módulo de
verdad descubriría que no encaja.

| Enganche | Aporta | De fábrica | Ejemplo futuro |
|---|---|---|---|
| `Analyzer` | hechos observados de un jar | `jarscan` | leer mixins, medir el tiempo de carga |
| `SideAdvisor` | una opinión sobre el lado, con motivo y confianza | 4 reglas | deducirlo del log de un arranque real |
| `Reporter` | documentos generados desde el índice | catálogo, duplicados, caídos | tabla para una wiki, informe de tamaño |
| `Source` | de dónde salen los jars | Modrinth | CurseForge, carpeta local, GitHub releases |

## Las dos reglas que lo sostienen

**Los analizadores aportan, no pisan.** Cada uno escribe bajo su propia clave en `Facts`, así que
añadir uno no cambia lo que ven los demás ni puede romperlos. Un módulo que quiera datos de otro los
pide por nombre (`facts.of("jarscan")`) y con eso queda declarada la dependencia.

**Los asesores de lado votan.** Devuelven sugerencia, motivo y confianza; decide la persona leyendo
los motivos. Medido contra un índice real de 191 mods con el lado decidido a mano, el consenso
coincide **en torno a la mitad de las veces**: sirve para orientar y para avisar de un caso que se
pasó por alto, no para decidir. Un detector nuevo entra como un voto más y no tiene que ganarle a
los anteriores ni sustituirlos, que es lo que obligaba a reescribir la función de escaneo entera.

En un empate entre lados distintos, el consenso resuelve a `both`, porque los dos errores no cuestan
igual: un mod de más en un lado se queda ahí sin hacer nada, y uno de menos rompe el registro o la
partida.

## Escribir un módulo

```python
class MixinAnalyzer:
    name = "mixins"

    def analyze(self, jar: Path) -> dict:
        return {"mixin_targets": [...]}


class ArranqueAdvisor:
    name = "arranque"

    def advise(self, mod, facts) -> SideOpinion | None:
        if facts.get("mixin_targets", analyzer="mixins"):
            return SideOpinion(Side.BOTH, "toca clases de cliente por mixin", 0.7)
        return None
```

Registrarlo en el proceso:

```python
ext = default_extensions()
ext.add_analyzer(MixinAnalyzer())
ext.add_advisor(ArranqueAdvisor())
```

O desde **otro paquete**, sin que modman lo conozca:

```toml
[project.entry-points."modman.analyzers"]
mixins = "mi_paquete.analizadores:MixinAnalyzer"
```

Los grupos son `modman.analyzers`, `modman.advisors`, `modman.reporters` y `modman.sources`. Se
cargan después de los de fábrica, así que registrar uno con el mismo `name` **sustituye** al de
fábrica; es la vía para cambiar una regla de lado en un pack concreto.

## Errores

Un módulo que reviente se aísla —no tumba a los demás ni aborta un escaneo de doscientos mods— pero
**no se silencia**: el fallo queda en `Extensions.errors` y la CLI lo imprime. Escribiendo esta
misma clase, un `except` silencioso escondió un bug propio y el síntoma fue «ningún asesor opina»,
que no apunta a nada. Aislar sí; esconder no.

## Los informes no escriben

`Reporter.report` devuelve `{ruta: contenido}`. Por eso `catalog --dry` sale gratis, la TUI puede
previsualizar y los tests comprueban el resultado sin tocar el disco. Es el mismo criterio que hace
que el desplegador devuelva un plan en vez de copiar jars.

Y una advertencia que vino de un caso real: al regenerar, todo lo que haya bajo la marca
`<!-- modman: lo de abajo se conserva al regenerar -->` se conserva, y un documento que **no** la
lleve no se sobreescribe sin `--force`. La primera versión sí lo hacía, y se llevó cuarenta líneas
de diagnóstico escritas a mano.
