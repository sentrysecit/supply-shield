# Detector de typosquatting

El detector compara el nombre de cada paquete pedido con los 5 000 más descargados de su ecosistema ([`data/popular/`](../data/popular/README.md)). Si el nombre imita a uno de ellos, produce un hallazgo `typosquatting` con un puntaje de 0 a 1 y el paquete legítimo con el que se confunde. La política ([policy.md](policy.md)) decide qué hacer con ese hallazgo. Las decisiones de diseño están en el [ADR 0008](adr/0008-typosquatting-detector.md).

## Qué detecta

| Técnica | Ejemplo | Peso |
| --- | --- | --- |
| `separators`: igual salvo `-`, `_` o `.` | `crossenv` → `cross-env` | 0,95 |
| `confusables`: letras que se leen igual (`rn`/`m`, `vv`/`w`, `i`/`1`/`l`, `0`/`o`) | `jeIlyfish` → `jellyfish`, `rnoment` → `moment` | 0,95 |
| `scope`: `@scope/` quitado, agregado o mal escrito (npm) | `types-node`, `@typse/node` → `@types/node` | 0,90 |
| `edit_distance`: una letra de más, de menos, cambiada o dos letras vecinas intercambiadas (Damerau-Levenshtein) | `loadsh` → `lodash`, `python3-dateutil` → `python-dateutil` | 0,90 (1 edición) / 0,75 (2 ediciones) |
| `edit_distance`: solo cambia el número final | `eventemitter2` → `eventemitter3` | 0,70 |
| `jaro_winkler`: nombres muy parecidos, sobre todo al principio | `reac` → `react` | 0,8 × similitud |
| `affix`: prefijo o sufijo de ataque sobre un nombre popular | `py-requests`, `lodash-js` → … | 0,80 |

Los afijos de ataque son `python3-`, `python-`, `py-`, `py`, `node-`, `js-` al principio y `-python`, `-py`, `-js`, `.js`, `js`, `-dev`, `-lib` al final.

Reglas para evitar falsos positivos:

- Un nombre que está en la lista popular **nunca** se marca: un paquete popular no es typosquat de otro.
- Un nombre de la [lista de permitidos](#lista-de-permitidos) nunca se marca.
- `confusables` y la comparación difusa solo se aplican a nombres de 4 o más caracteres. Una edición cuenta desde 5 caracteres y dos ediciones desde 8. En nombres cortos, el parecido suele ser casualidad (`mz` y `ms`).
- Un nombre popular publicado bajo otro scope (`@acme/lodash`) no se marca: lo cubre dependency confusion (D5-4).

## Puntaje y severidad

El puntaje de un objetivo es el peso de la técnica más fuerte más **0,05** por cada técnica adicional que coincide, con tope en 1. Si el nombre se parece a varios paquetes populares, gana el de mayor puntaje y, si empatan, el más popular.

| Puntaje | Severidad | Con la política por defecto |
| --- | --- | --- |
| ≥ 0,95 | `critical` | bloquea |
| ≥ 0,85 | `high` | bloquea |
| ≥ 0,70 | `medium` | solo se registra |
| ≥ 0,60 | `low` | solo se registra |
| < 0,60 | — (sin hallazgo) | — |

Una sola técnica fuerte (separadores, letras parecidas, scope o una edición) basta para bloquear. Una débil y aislada (afijo, solo Jaro-Winkler, cambio del número final) solo se registra. Los umbrales se configuran con `SUPPLY_SHIELD_TYPOSQUATTING_{REPORT,MEDIUM,HIGH,CRITICAL}_THRESHOLD` y deben ir en orden creciente. Los pesos son provisionales: D4-6 los recalibra midiendo precisión y recall.

## El hallazgo

```text
rule:       typosquatting
severity:   critical
source:     typosquatting
detail:     El nombre 'crossenv' se parece al paquete popular 'cross-env' (#1931 en npm);
            puntaje 1.00 por separators, edit_distance, jaro_winkler
references: https://www.npmjs.com/package/cross-env
```

`detail` es lo que verá el usuario en el motivo del bloqueo. La técnica más fuerte va primero. Todas las versiones de un paquete reciben el mismo resultado, porque el detector solo mira el nombre.

## Lista de permitidos

Si el detector marca un paquete legítimo, se agrega a [`data/typosquatting/allowlist.yaml`](../data/typosquatting/allowlist.yaml) por PR, con la razón:

```yaml
version: 1
npm:
  - name: preact
    reason: "Alternativa ligera a react, de otro equipo; no es una imitación."
pypi: []
```

- El nombre va **normalizado**: en npm, en minúsculas y con su `@scope`; en PyPI, según PEP 503 (`Django_Foo` → `django-foo`). Un nombre sin normalizar hace fallar el arranque y el error indica cómo escribirlo.
- La lista es para falsos positivos permanentes. Para dejar pasar algo temporalmente, usa una excepción de la política, que vence ([policy.md](policy.md#excepciones)).

## Configuración y arranque

| Variable | Por defecto |
| --- | --- |
| `SUPPLY_SHIELD_POPULAR_PACKAGES_DIR` | `data/popular` (debe contener `npm.json` y `pypi.json`) |
| `SUPPLY_SHIELD_TYPOSQUATTING_ALLOWLIST_FILE` | `data/typosquatting/allowlist.yaml` |
| `SUPPLY_SHIELD_TYPOSQUATTING_*_THRESHOLD` | 0,60 / 0,70 / 0,85 / 0,95 |

Las listas se cargan al arrancar. **Si falta un archivo o es inválido, el servicio no arranca.** La imagen Docker ya incluye los dos directorios.

## Uso desde el código

```python
from supply_shield.analyzers.typosquatting import score_name
from supply_shield.core import Ecosystem

analyzer = app.state.typosquatting_analyzer  # cargado por create_app()
findings = await analyzer.analyze(package, context)  # list[Finding], protocolo Analyzer
match = analyzer.match(package)  # TyposquatMatch | None, sin umbrales

# Para medir (D4-6): función pura, sin umbrales ni E/S.
match = score_name("crossenv", analyzer.indexes[Ecosystem.NPM])
```

## Rendimiento

Medido con las listas reales: p95 de unos 2 ms por nombre (unos 3 ms bajo la medición de cobertura de la CI), frente a un presupuesto de 5 ms. Una prueba lo verifica en cada ejecución (`tests/unit/typosquatting/test_real_lists.py`).
