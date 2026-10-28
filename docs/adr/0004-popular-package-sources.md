# 0004. Fuentes de la lista de paquetes populares

- **Estado**: Propuesto
- **Fecha**: 2026-10-27

## Contexto

El detector de typosquatting (D4-4) compara cada nombre pedido contra los paquetes más usados de su ecosistema. Necesita, para npm y para PyPI, los 5 000 nombres más populares en orden, con fecha y fuente, versionados en el repo y fáciles de actualizar.

Ninguno de los dos registros publica un ranking oficial de descargas:

- **PyPI** expone las descargas solo a través del dataset público de BigQuery o de réplicas como ClickPy (ClickHouse). Consultarlo directamente exige credenciales y cuota.
- **npm** tiene una API de descargas por paquete (`api.npmjs.org/downloads`), pero no un listado ordenado. La búsqueda del registro ya no ordena por popularidad de forma fiable.

## Decisión

Usaremos dos fuentes públicas que ya calculan el ranking, una por ecosistema. El script `scripts/fetch_popular.py` las descarga, normaliza los nombres con `normalize_package_name` (D3-1), quita duplicados e inválidos, y guarda los primeros 5 000 en `data/popular/<ecosistema>.json`.

| Ecosistema | Fuente | Ranking | Frecuencia | Licencia |
| --- | --- | --- | --- | --- |
| PyPI | [`hugovk/top-pypi-packages`](https://github.com/hugovk/top-pypi-packages): `https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json` | Descargas de los últimos 30 días (ClickPy), 15 000 nombres | Mensual, el día 1 | No declara licencia; se cita la fuente (DOI de Zenodo en su README) |
| npm | Lista `topDownload` del paquete [`npm-high-impact`](https://github.com/wooorm/npm-high-impact), descargada como tarball del registro oficial | Más descargados primero, ~15 900 nombres | Cada pocos meses, al publicar versión | MIT |

Reglas del script:

- El tarball de npm se descarga **solo** de `registry.npmjs.org` y se verifica contra el `sha512` (`integrity`) publicado en la metadata del paquete. El archivo `lib/top-download.js` se lee en memoria y se acepta solo si el arreglo contiene literales de texto; cualquier otra cosa hace fallar la ejecución.
- Si una fuente entrega menos de 5 000 nombres válidos, el script falla y no toca los archivos. Una descarga truncada nunca reemplaza una lista completa.
- Un archivo se reescribe solo si cambian los nombres o su orden. La fecha de generación y los datos de la fuente no cuentan como cambio.
- Un job semanal (`.github/workflows/popular-packages.yml`) corre el script y abre o actualiza un único PR cuando hay diferencias.

Cada archivo guarda `generated_at` (cuándo se generó), `source.published_at` (cuándo publicó la fuente ese ranking), `source.name`, `source.url` y `source.ranking`.

## Alternativas consideradas

- **API de ecosyste.ms** (`packages.ecosyste.ms`, orden por descargas): cubre los dos ecosistemas con una sola API, pero en las pruebas tardó entre 70 y 90 s por página de 100 resultados y a veces cortó la conexión. Llegar a 5 000 nombres exige 50 páginas, poco viable para un job semanal. `npm-high-impact` ya se construye con esos datos.
- **BigQuery o ClickPy directo para PyPI**: da datos del día, a cambio de credenciales, cuota y una consulta propia que mantener. hugovk ya publica ese mismo cálculo cada mes.
- **API de descargas de npm sobre una lista de candidatos**: da cifras actuales, pero los paquetes con `@scope` se consultan de a uno. Son más de un tercio de la lista, así que serían miles de peticiones por ejecución.
- **Escribir la lista a mano**: no escala y no deja claro de dónde sale cada nombre.

## Consecuencias

- La lista de npm puede tener varios meses de antigüedad, porque depende de cuándo publique `npm-high-impact`. `source.published_at` deja visible esa edad y D4-6 puede medir si afecta la detección.
- Las dos fuentes miden popularidad por descargas, y las descargas se pueden inflar. Estar en la lista **no** significa que un paquete sea confiable: el detector la usa como conjunto de nombres a proteger, no como lista de permitidos. Quien revise el PR semanal debe mirar los nombres nuevos.
- Dependemos de dos proyectos mantenidos por personas, no por los registros. Si alguno deja de publicar, el job falla (o deja de abrir PRs) y la lista se queda con su última versión, que sigue sirviendo. Habría que volver a evaluar las alternativas de arriba.
- npm normaliza a minúsculas, igual que `PackageRef`. Los pocos nombres heredados con mayúsculas (por ejemplo, `JSONStream`) quedan en minúsculas.
- La imagen Docker todavía no incluye `data/`. D4-4 debe copiar la lista al contenedor y exponer su ruta desde `Settings`.
