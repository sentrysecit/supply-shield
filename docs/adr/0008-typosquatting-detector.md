# 0008. Detector de typosquatting por puntaje combinado contra los paquetes populares

- **Estado**: Propuesto
- **Fecha**: 2026-11-07

## Contexto

Un typosquat es un paquete publicado con un nombre parecido al de uno popular (`crossenv` por `cross-env`, `jeIlyfish` por `jellyfish`) para que alguien lo instale por error. D4-4 tiene que detectarlo antes de servir el paquete, con un puntaje de 0 a 1 y el nombre del paquete legítimo, y con estas condiciones:

- detectar `crossenv`, `python3-dateutil` y `jeIlyfish`;
- no marcar ningún paquete del top 1 000;
- tardar menos de 5 ms por paquete (p95);
- combinar distancia de edición (Damerau-Levenshtein y Jaro-Winkler), separadores, letras parecidas, prefijos y sufijos, y `@scope` en npm;
- tener una lista de nombres permitidos.

Contra qué se compara ya está resuelto: los 5 000 paquetes más descargados de cada ecosistema (D4-2, [ADR 0004](0004-popular-package-sources.md)). El motor de políticas ([ADR 0007](0007-policy-engine.md)) bloquea la regla `typosquatting` desde severidad `high`.

## Decisión

**Exenciones.** Un nombre que está en la lista popular o en la lista de permitidos nunca se marca. Un paquete popular no es typosquat de otro: pares como `request`/`requests` o `string-width`/`string-width-cjs` son legítimos y conviven en el top. Así se cumple el criterio del top 1 000 de forma estructural, no por ajuste fino de pesos.

**Candidatos.** Para cada nombre pedido se buscan objetivos de dos formas:

- búsquedas exactas O(1) en diccionarios precalculados: misma clave sin separadores, misma clave con letras parecidas unificadas y nombre base tras quitar un afijo de ataque;
- búsqueda difusa sobre los 5 000 nombres con `rapidfuzz`: Levenshtein ≤ 4 y Jaro-Winkler ≥ 0,92, **sin límite de resultados**.

**Damerau-Levenshtein exacto con un prefiltro sin pérdidas.** Damerau-Levenshtein completo sobre 5 000 nombres tarda de 3 a 10 ms por consulta. En vez de cambiarlo por una variante más rápida pero distinta (OSA), prefiltramos con Levenshtein: una transposición cuesta 1 en Damerau-Levenshtein y 2 en Levenshtein, así que DL ≤ 2 implica Levenshtein ≤ 4. Sobre los pocos candidatos que pasan (de 1 a 50) se calcula Damerau-Levenshtein exacto. Una prueba compara el resultado con la búsqueda exhaustiva para asegurar que el prefiltro no pierde candidatos.

**Señales y pesos.** Cada técnica que coincide aporta un peso:

| Técnica | Cuándo | Peso |
| --- | --- | --- |
| `separators` | igual salvo `-`, `_`, `.` | 0,95 |
| `confusables` | igual al unificar `rn`→`m`, `vv`→`w`, `i`/`1`→`l`, `0`→`o` (nombres de 4 o más caracteres) | 0,95 |
| `scope` | `@scope/` quitado o agregado (`types-node`), o scope mal escrito con el mismo nombre (`@typse/node`) | 0,90 |
| `edit_distance` | Damerau-Levenshtein 1 (5 o más caracteres) / 2 (8 o más) | 0,90 / 0,75 |
| `edit_distance` | solo cambia el número final (`eventemitter2`/`eventemitter3`) | 0,70 |
| `jaro_winkler` | similitud ≥ 0,92 | 0,8 × similitud |
| `affix` | prefijo o sufijo de ataque sobre un nombre popular (`py-requests`) | 0,80 |

El puntaje es el peso más alto más 0,05 por cada señal adicional, con tope en 1. Gana el objetivo de mayor puntaje y, si empatan, el más popular. El puntaje se traduce a severidad con umbrales configurables: `critical` ≥ 0,95, `high` ≥ 0,85, `medium` ≥ 0,70, `low` ≥ 0,60, y por debajo no hay hallazgo. Con la política por defecto, una señal fuerte (separadores, letras parecidas, scope, una sola edición) bloquea; una débil y aislada (afijo, solo Jaro-Winkler, cambio del número final) se registra y no rompe builds.

**Lista de permitidos.** `data/typosquatting/allowlist.yaml`, por ecosistema, con nombre normalizado y razón obligatoria. Es para falsos positivos del detector. Para un bloqueo puntual y temporal están las excepciones de la política, que vencen.

**Arranque.** Las listas y la de permitidos se cargan al crear la app (`app.state.typosquatting_analyzer`). Si falta un archivo o es inválido, **la app no arranca**: sin listas, el detector no protege nada y no debe fallar en silencio. La imagen Docker incluye `data/popular/` y `data/typosquatting/`, de solo lectura para el usuario de la app.

**Medición** (Windows, Python 3.13, con las listas reales):

- p95 de 1,8 a 2,1 ms por nombre; unos 3,2 ms bajo la medición de cobertura de la CI.
- Los tres casos de aceptación quedan en `critical`.
- Línea base de falsos positivos: comparando los paquetes #1001–#5000 (todos legítimos) solo contra el top 1 000 y sin la exención, se marcarían como `high` o más 40 de 4 000 en npm (1,0 %) y 31 de 4 000 en PyPI (0,8 %). Es una cota pesimista: esos nombres son, por construcción, parecidos a otros populares.

## Alternativas consideradas

- **OSA (Damerau-Levenshtein restringido) sin prefiltro**: ~1 ms, pero difiere de Damerau-Levenshtein en casos de ediciones superpuestas. Con el prefiltro, Damerau-Levenshtein exacto cuesta casi lo mismo.
- **`limit` en la búsqueda difusa**: más rápido, pero puede dejar fuera al objetivo correcto cuando hay muchos nombres parecidos (`@aws-sdk/client-*`).
- **Suma ponderada de todas las señales**: diluye una señal fuerte cuando las demás no aplican (`rnoment` solo coincide por letras parecidas). El máximo con bonificación respeta la señal más fuerte y premia que coincidan varias.
- **Marcar cualquier nombre popular bajo otro scope (`@acme/lodash`)**: republicar con scope propio es habitual y daría muchos falsos positivos. El caso peligroso (un nombre interno pedido al registro público) lo cubre dependency confusion (D5-4).
- **Mapear `cl`→`d` y `5`→`s`**: aparecen poco en ataques y confunden pares legítimos (`clone`/`done`).
- **Arrancar sin el detector si faltan las listas**: dejaría el firewall sin una protección sin que nadie se entere.

## Consecuencias

- Los pesos y umbrales son provisionales. D4-6 los recalibra con un dataset etiquetado y puede llamar directamente a `score_name`, que es una función pura.
- Un nombre popular nunca se marca, aunque sea un typosquat que logró colarse en el top. Mitigación: el PR semanal que actualiza las listas se revisa (ADR 0004) y los demás detectores (malware, vulnerabilidades) siguen aplicando.
- Las variantes que solo cambian el número final (`lodash2`) se registran pero no bloquean. Si D4-6 encuentra ataques con ese patrón, se sube el peso `numbered_variant`.
- El detector ignora la versión: todas las versiones de un paquete reciben el mismo resultado. La caché de hallazgos (D3-6) podría aprovecharlo y guardar por nombre.
- D3-6 debe registrar `app.state.typosquatting_analyzer` en el evaluador. D3-5 define qué pasa si el detector falla en tiempo de ejecución (*fail-open* para esta regla).
