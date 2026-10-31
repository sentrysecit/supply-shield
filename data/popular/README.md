# Paquetes populares

Los 5 000 paquetes más descargados de cada ecosistema, en orden. El detector de typosquatting compara cada nombre pedido contra estas listas.

| Archivo     | Fuente                                                                                      |
| ----------- | ------------------------------------------------------------------------------------------- |
| `npm.json`  | Lista `topDownload` de [`npm-high-impact`](https://github.com/wooorm/npm-high-impact) (MIT) |
| `pypi.json` | [`hugovk/top-pypi-packages`](https://github.com/hugovk/top-pypi-packages), últimos 30 días  |

Por qué se eligieron esas fuentes y sus límites: [ADR 0004](../../docs/adr/0004-popular-package-sources.md).

## Formato

```json
{
  "format_version": 1,
  "ecosystem": "pypi",
  "generated_at": "2026-10-27T21:19:45Z",
  "source": {
    "name": "hugovk/top-pypi-packages",
    "url": "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json",
    "ranking": "downloads in the last 30 days, most downloaded first",
    "published_at": "2026-10-01T12:40:51Z"
  },
  "packages": ["boto3", "packaging", "..."]
}
```

- `generated_at`: cuándo el script escribió la lista.
- `source.published_at`: cuándo la fuente publicó ese ranking.
- `packages`: nombres ya normalizados (PEP 503 en PyPI, minúsculas en npm), sin duplicados, del más al menos popular.

El modelo y el cargador están en `supply_shield.analyzers.typosquatting.popular`.

## Actualizar

No se deben editar estos archivos a mano. Para regenerarlos:

```bash
uv run python scripts/fetch_popular.py                    # los dos ecosistemas
uv run python scripts/fetch_popular.py --ecosystem pypi   # solo uno
```

El script solo reescribe un archivo si cambian los nombres o su orden. El workflow `Refresh popular packages` lo corre cada lunes y abre un PR cuando hay cambios.
