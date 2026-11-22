# Medición del detector de typosquatting (D4-6)

Herramientas para medir el detector de typosquatting con ataques reales. El informe con los resultados y su análisis está en [`docs/results/typosquatting.md`](../../docs/results/typosquatting.md).

| Archivo | Qué es |
| --- | --- |
| `build_dataset.py` | Descarga las fuentes fijadas y arma `dataset/` (necesita red). |
| `evaluate.py` | Puntúa el dataset con el detector y escribe `docs/results/typosquatting/` (sin red). |
| `svg_charts.py` | Gráficas SVG de `evaluate.py`, sin dependencias. |
| `labels.csv` | Revisión manual: qué paquete imita cada typosquat declarado. |
| `dataset/` | El dataset etiquetado, versionado en el repo. |

## Uso

```bash
# Medir (sin red, ~1 min).
uv run python eval/typosquatting/evaluate.py

# Rearmar el dataset (~2 min, ~125 MB de descargas).
uv run python eval/typosquatting/build_dataset.py --pending pendientes.csv
```

`evaluate.py` usa las listas populares, la lista de permitidos y los umbrales de `Settings`, igual que el servicio. Con `--popular-dir` y `--allowlist` se puede medir otra configuración; `--output-dir` cambia dónde se escriben los resultados.

## El dataset

Un archivo por ecosistema y grupo, en CSV con LF:

| Archivo | Columnas | Contenido |
| --- | --- | --- |
| `<eco>-typosquats.csv` | `name,target,advisories` | Maliciosos cuyo aviso de OpenSSF dice "typosquat". `target` es el paquete imitado; vacío si no hay uno identificable. |
| `<eco>-next.csv` | `rank,name` | Legítimos de la misma fuente que `data/popular/`, que no están en la lista. |
| `<eco>-random.csv` | `name` | Muestra reproducible de todos los nombres del registro. |
| `sources.json` | — | De dónde salió cada fuente (URL, commit o versión, sha256, fecha), el tamaño de cada grupo y los cruces entre maliciosos y listas. |

**Fuentes fijadas** (constantes al principio de `build_dataset.py`):

- **OpenSSF malicious-packages:** tarball de un commit. Solo se leen `osv/malicious/{npm,pypi}`, en memoria y sin extraer nada a disco.
- **Datadog malicious-software-packages-dataset:** solo los `manifest.json` de un commit. Sirven para excluir nombres de los negativos. **Nunca se descargan las muestras de malware.**
- **Rankings:** el mismo volcado de `hugovk/top-pypi-packages` y la misma versión de `npm-high-impact` que generaron `data/popular/`. El tarball se verifica contra su `sha512`.
- **Todos los nombres:**
  - npm: `all-the-package-names` en una versión fija, también verificado con `sha512`.
  - PyPI: el índice simple en JSON (PEP 691). No se puede fijar, así que `sources.json` guarda la fecha y el sha256.

**Muestra aleatoria.** Son los nombres con el menor `sha256(semilla:ecosistema:nombre)`. Se repite igual con la misma entrada y no depende del orden. Si el registro suma nombres, los elegidos siguen en la muestra salvo que uno nuevo les gane el lugar.

## Revisar `labels.csv`

Formato: `ecosystem,name,target`, con nombres normalizados como en la lista de permitidos. `target` vacío significa "sin paquete imitado identificable". Un nombre sin normalizar o repetido hace fallar `build_dataset.py` e indica la línea.

Criterio:

- Se anota el paquete **público y concreto** que el nombre imita, cuando el nombre o el aviso lo identifican sin ambigüedad. Puede ser:
  - un error de tipeo (`requessts` → `requests`);
  - un cambio de separadores o de `@scope` (`@anfular/core` → `@angular/core`);
  - el nombre real con palabras agregadas (`chalk-core` → `chalk`).
- Queda vacío si no hay un paquete concreto: nombres internos de una empresa (*dependency confusion*), nombres al azar, paquetes de prueba o imitaciones de una marca (`anthropic-toolkit`).
- **Se revisa sin mirar qué responde el detector.** Si no, las métricas miden la revisión y no el detector.

Al cambiar la versión de OpenSSF en `build_dataset.py`, los typosquats nuevos sin revisar quedan fuera de las métricas y se listan en el archivo de `--pending`. Ese archivo tiene el mismo formato: se completa `target` y se agregan las filas a `labels.csv`.

La revisión actual la hizo el asistente de código del equipo. Conviene que una persona audite una muestra.
