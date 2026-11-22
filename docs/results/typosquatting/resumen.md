# Medición del detector de typosquatting: tablas generadas

Archivo generado por `eval/typosquatting/evaluate.py`; no lo edites a mano.
El análisis está en [`../typosquatting.md`](../typosquatting.md).

## Dataset

| Grupo | npm | PyPI |
| --- | --- | --- |
| Typosquats con paquete imitado | 284 | 219 |
| Typosquats sin paquete imitado identificable | 411 | 344 |
| Legítimos: la lista popular | 5 000 | 5 000 |
| Legítimos: siguientes del ranking | 10 844 | 9 998 |
| Legítimos: muestra aleatoria del registro | 5 000 | 5 000 |

## Métricas en los umbrales configurados

Con la política por defecto, el detector bloquea desde `high`. FP es la proporción
de cada grupo de legítimos que se bloquearía con ese umbral.

### npm

| Umbral | Precisión | Recall | F1 | Recall en alcance | FP lista popular | FP siguientes | FP aleatorios |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0,70 · `medium` | 2,7 % | 18 % | 4,8 % | 31 % | 0 % | 16 % | 1,6 % |
| 0,85 · `high`, bloquea | 5,5 % | 3,5 % | 4,3 % | 6 % | 0 % | 1,5 % | 0,2 % |
| 0,95 · `critical` | 5,7 % | 3,2 % | 4,1 % | 5,4 % | 0 % | 1,3 % | 0,1 % |

### PyPI

| Umbral | Precisión | Recall | F1 | Recall en alcance | FP lista popular | FP siguientes | FP aleatorios |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0,70 · `medium` | 6,4 % | 59 % | 12 % | 81 % | 0 % | 17 % | 5 % |
| 0,85 · `high`, bloquea | 24 % | 39 % | 30 % | 54 % | 0 % | 2,2 % | 0,9 % |
| 0,95 · `critical` | 29 % | 34 % | 31 % | 47 % | 0 % | 1,5 % | 0,6 % |

### Ambos ecosistemas

| Umbral | Precisión | Recall | F1 | Recall en alcance | FP lista popular | FP siguientes | FP aleatorios |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0,70 · `medium` | 4,6 % | 36 % | 8,2 % | 56 % | 0 % | 16 % | 3,3 % |
| 0,85 · `high`, bloquea | 18 % | 19 % | 18 % | 30 % | 0 % | 1,9 % | 0,5 % |
| 0,95 · `critical` | 20 % | 17 % | 18 % | 26 % | 0 % | 1,4 % | 0,4 % |

## Umbral con mejor F1

| Alcance | Umbral con mejor F1 | F1 | Precisión | Recall | F1 con el actual |
| --- | --- | --- | --- | --- | --- |
| npm | 0,76 | 5 % | 3,6 % | 8,1 % | 4,3 % |
| PyPI | 0,95 | 31 % | 29 % | 34 % | 30 % |
| Ambos ecosistemas | 0,90 | 19 % | 18 % | 19 % | 18 % |

## Alcance de la lista popular (umbral 0,85)

| Ecosistema | Bloqueados con el objetivo en la lista | Bloqueados con el objetivo fuera de la lista | Bloqueados con el objetivo correcto |
| --- | --- | --- | --- |
| npm | 10 de 166 | 0 de 118 | 10 de 10 |
| PyPI | 86 de 159 | 0 de 60 | 85 de 86 |

## Por qué se escapan los typosquats (umbral 0,85)

| Forma del nombre | npm | PyPI | Ejemplos |
| --- | --- | --- | --- |
| El paquete imitado no está en la lista popular | 118 | 60 | `0nestjs` → `nestjs`, `1nestjs` → `nestjs`, `2nestjs` → `nestjs`, `@0xlr/stripe-checkout-js` → `stripe-checkout-js`, `@asenfotech/unplugin-element-plus` → `unplugin-element-plus`, `@debile/require-dir` → `require-dir` |
| El nombre, igual o con un error, bajo un `@scope` ajeno (`@x/ws` por `ws`) | 14 | 0 | `@ai_/autoprefixers` → `autoprefixer`, `@dervix/ws` → `ws`, `@equans-services/codex` → `@openai/codex`, `@equansservices/codex` → `@openai/codex`, `@httpactions/strict-uri-encode` → `strict-uri-encode`, `@nestjs-passport/jwt` → `@nestjs/jwt` |
| Nombre imitado más otras palabras (`chalk-core`) | 81 | 27 | `async-mutex-v3` → `async-mutex`, `axios-fast` → `axios`, `bs58-15` → `bs58`, `bs58-33` → `bs58`, `bs58-77` → `bs58`, `bs58-86` → `bs58` |
| Una palabra del nombre imitado, a veces mal escrita, más otras (`typescipt-cli`) | 39 | 18 | `axois-http` → `axios`, `base65-12x` → `base-x`, `base65-15x` → `base-x`, `base99-85x` → `base-x`, `comander-cli` → `commander`, `comander-lib` → `commander` |
| Se parece, pero el puntaje no llega al umbral | 7 | 18 | `axiosqqq` → `axios`, `chalkdev` → `chalk`, `haswons` → `hasown`, `loadutils` → `loader-utils`, `momenntjs` → `moment`, `passport811` → `passport` |
| Otra forma | 15 | 10 | `@wethenorth12/playwrite` → `playwright`, `@zzzcrypto/playwrite` → `playwright`, `@zzzgenesis00/playwrite` → `playwright`, `chlklib` → `chalk`, `dit-envv` → `dotenv`, `erslove` → `resolve` |

## Técnica que decide el bloqueo (umbral 0,85)

Ambos ecosistemas juntos.

| Técnica más fuerte | Typosquats | FP siguientes | FP aleatorios |
| --- | --- | --- | --- |
| `affix` | 1 | 21 | 0 |
| `confusables` | 0 | 1 | 0 |
| `edit_distance` | 88 | 280 | 54 |
| `scope` | 7 | 58 | 0 |
| `separators` | 0 | 26 | 1 |

## Ejemplos (umbral 0,85)

### npm: legítimos bloqueados (siguientes del ranking)

| Paquete | Puesto | Se parece a | Puntaje | Técnicas |
| --- | --- | --- | --- | --- |
| `babel-traverse` | 5080 | `@babel/traverse` | 1,00 | scope, edit_distance, jaro_winkler |
| `babel-template` | 5259 | `@babel/template` | 1,00 | scope, edit_distance, jaro_winkler |
| `babel-generator` | 5787 | `@babel/generator` | 1,00 | scope, edit_distance, jaro_winkler |
| `isurl` | 6111 | `is-url` | 1,00 | separators, edit_distance, jaro_winkler |
| `seed-random` | 6175 | `seedrandom` | 1,00 | separators, edit_distance, jaro_winkler |
| `parseuri` | 6541 | `parseurl` | 1,00 | confusables, edit_distance, jaro_winkler |
| `babel-register` | 6593 | `@babel/register` | 1,00 | scope, edit_distance, jaro_winkler |
| `firebase-functions` | 6888 | `@firebase/functions` | 1,00 | scope, jaro_winkler, edit_distance |
| `babel-helpers` | 6944 | `@babel/helpers` | 1,00 | scope, edit_distance, jaro_winkler |
| `tslog` | 8362 | `ts-log` | 1,00 | separators, edit_distance, jaro_winkler |
| `mem-fs` | 8552 | `memfs` | 1,00 | separators, edit_distance, jaro_winkler |
| `color-space` | 8602 | `colorspace` | 1,00 | separators, edit_distance, jaro_winkler |

### npm: typosquats que no se bloquean

| Paquete | Imita a | Puntaje |
| --- | --- | --- |
| `0nestjs` | `nestjs` | — |
| `1nestjs` | `nestjs` | — |
| `2nestjs` | `nestjs` | — |
| `@0xlr/stripe-checkout-js` | `stripe-checkout-js` | — |
| `@ai_/autoprefixers` | `autoprefixer` | — |
| `@asenfotech/unplugin-element-plus` | `unplugin-element-plus` | — |
| `@debile/require-dir` | `require-dir` | — |
| `@dervix/ws` | `ws` | — |
| `@equans-services/codex` | `@openai/codex` | — |
| `@equansservices/codex` | `@openai/codex` | — |
| `@httpactions/strict-uri-encode` | `strict-uri-encode` | — |
| `@nestjs-passport/jwt` | `@nestjs/jwt` | — |

### PyPI: legítimos bloqueados (siguientes del ranking)

| Paquete | Puesto | Se parece a | Puntaje | Técnicas |
| --- | --- | --- | --- | --- |
| `jarowinkler` | 6211 | `jaro-winkler` | 1,00 | separators, edit_distance, jaro_winkler |
| `git-python` | 6794 | `gitpython` | 1,00 | separators, edit_distance, jaro_winkler |
| `git-url-parse` | 7755 | `giturlparse` | 1,00 | separators, jaro_winkler, edit_distance |
| `json-diff` | 8812 | `jsondiff` | 1,00 | separators, edit_distance, jaro_winkler |
| `config-parser` | 9066 | `configparser` | 1,00 | separators, edit_distance, jaro_winkler |
| `expiring-dict` | 9405 | `expiringdict` | 1,00 | separators, edit_distance, jaro_winkler |
| `databricksapi` | 9548 | `databricks-api` | 1,00 | separators, edit_distance, jaro_winkler |
| `oauth2-client` | 10566 | `oauth2client` | 1,00 | separators, edit_distance, jaro_winkler |
| `django-rest-framework` | 12288 | `djangorestframework` | 1,00 | separators, jaro_winkler, edit_distance |
| `mypy-boto3-emr` | 5016 | `mypy-boto3-ecr` | 0,95 | edit_distance, jaro_winkler |
| `jsons` | 5036 | `json5` | 0,95 | edit_distance, jaro_winkler |
| `pystan` | 5112 | `pystac` | 0,95 | edit_distance, jaro_winkler |

### PyPI: typosquats que no se bloquean

| Paquete | Imita a | Puntaje |
| --- | --- | --- |
| `aiohttp-openssl` | `aiohttp` | 0,74 |
| `aiohttp-util` | `aiohttp` | — |
| `aliababcloud-tea-openapi` | `alibabacloud-tea-openapi` | 0,83 |
| `aliyun-oss2` | `oss2` | — |
| `aliyun-python-sdk-v2` | `aliyun-python-sdk-core` | 0,76 |
| `anthropy` | `anthropic` | 0,80 |
| `asn1tool` | `asn1tools` | — |
| `async-substrate-interface-upgrade` | `async-substrate-interface` | — |
| `aws-consoler2` | `aws-consoler` | — |
| `browser-use-headless` | `browser-use` | — |
| `cffi-curl` | `curl-cffi` | — |
| `cobo-custdoy` | `cobo-custody` | — |
