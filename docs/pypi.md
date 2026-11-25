# Espejo de PyPI

Supply-Shield actúa como *registry mirror* de PyPI ([ADR 0002](adr/0002-registry-mirror-instead-of-mitm-proxy.md)): el cliente apunta su `index-url` al proxy y obtiene de ahí el índice simple y los artefactos. El proxy los reenvía desde `pypi.org` y `files.pythonhosted.org` **sin modificar los bytes**, así que los hashes que ve el cliente son los del upstream. Las decisiones de diseño están en el [ADR 0015](adr/0015-pypi-simple-proxy.md).

## Rutas

| Ruta | Qué hace |
| --- | --- |
| `GET /pypi/simple/<proyecto>/` | Índice simple del proyecto, en HTML (PEP 503) o JSON (PEP 691) según `Accept`. Un nombre que no cumple la PEP 503 responde 404. |
| `GET /pypi/simple/<proyecto>` | Redirige (307) a la forma con barra final; lo hace Starlette (`redirect_slashes`). |
| `GET /pypi/packages/sha256/<hex>/<ruta>` | Reenvía el artefacto verificando su SHA-256 mientras lo streamea. |
| `GET /pypi/packages/<ruta>` | Reenvía el artefacto sin verificar (el índice no trajo hash). |

El índice incluye la metadata PEP 658 (`data-core-metadata`), respeta las versiones *yanked* (`data-yanked`) y reescribe cada enlace a `/pypi/packages/sha256/<hex>/…`, conservando el fragmento `#sha256=`. Un archivo cuyo enlace no apunta a `https://files.pythonhosted.org/packages/` se quita del índice, para que el cliente no descargue nada sin pasar por el proxy. El JSON reenvía además `versions`, `size` y `upload-time` (PEP 700), que usa, por ejemplo, `uv --exclude-newer`. El hash va en la ruta y no en una query porque pip y uv piden la metadata añadiendo `.metadata` a la URL; la metadata no se verifica con el hash del archivo, la comprueba el cliente con `data-core-metadata`. Las fechas de publicación y los mantenedores se toman de la JSON API de PyPI y se exponen como `UpstreamMetadata` al resto del sistema.

El tamaño máximo de un artefacto lo fija `SUPPLY_SHIELD_PYPI_MAX_ARTIFACT_BYTES` (4 GiB por defecto), aparte del límite general de 512 MiB, porque hay wheels más grandes (torch, CUDA).

Todas las rutas exigen el token de proyecto ([ADR 0006](adr/0006-project-auth-tokens.md)) y cuelgan del prefijo `/pypi` ([ADR 0013](adr/0013-proxy-routes-with-per-ecosystem-prefix.md)). Todavía **no aplican bloqueos**: eso es la tarea D1-3.

## Configurar un cliente

`pip` se configura con `index-url`. Con un token de proyecto (D2-1) va en la URL:

```ini
# pip.conf
[global]
index-url = https://<proyecto>:<token>@proxy.example/pypi/simple
```

Con `uv`, la misma idea:

```bash
UV_INDEX_URL=https://<proyecto>:<token>@proxy.example/pypi/simple uv pip install requests
```

Para verificar integridad, `pip install --require-hashes` usa el fragmento `#sha256=` que el índice ya trae del upstream; no hay que tocar el `requirements.txt`.

## Normalización de nombres

Los nombres se normalizan según la PEP 503 (`Django` = `django`, `my_pkg` = `my-pkg`) con `supply_shield.core.naming`. La ruta `/pypi/simple/django/` sirve también a un cliente que pida `/pypi/simple/Django/`.

## Uso desde el código

```python
from supply_shield.proxy.pypi import PyPIRegistry

registry = app.state.pypi_registry  # construido por create_app()
# SimpleProject: nombre normalizado + archivos
project = await registry.fetch_index("Django")
# UpstreamMetadata: versiones, fechas y mantenedores
metadata = await registry.fetch_metadata("Django")
```

El parseo, el render y la reescritura de URLs son funciones puras (`parse_simple_html`, `parse_simple_json`, `render_html`, `render_json`, `proxy_file_url`), fáciles de probar sin red. Las pruebas de las rutas simulan el upstream con `respx`.

## Pendiente

- D1-3: pedir una decisión al motor de evaluación, quitar del índice las versiones bloqueadas y responder 403 si se pide un archivo bloqueado.
- D5-7: servir los artefactos desde la caché de disco en lugar de ir siempre al upstream.
