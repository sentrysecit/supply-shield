# 0015. Espejo de PyPI: índice simple (PEP 503/691), PEP 658 y verificación SHA-256

- **Estado**: Aceptado
- **Fecha**: 2026-11-14

## Contexto

D2-2 pide que `pip install`, `pip install --require-hashes` y `uv pip install` funcionen a través de Supply-Shield, tanto con wheels como con sdists. El proxy es un *registry mirror* ([ADR 0002](0002-registry-mirror-instead-of-mitm-proxy.md)): el cliente apunta `index-url` a este servicio y no se intercepta TLS. Toda la salida a `pypi.org` y `files.pythonhosted.org` pasa por el cliente compartido ([ADR 0005](0005-shared-upstream-client-with-host-allowlist.md)), que aplica la allowlist y los reintentos.

Hay que resolver varias piezas del protocolo de PyPI: el índice simple en HTML (PEP 503) y JSON (PEP 691), la metadata PEP 658 publicada junto a cada archivo, las versiones *yanked*, la normalización de nombres (`Django` = `django`) y las fechas de publicación, que el índice simple no trae. Además, el hash de PyPI es **SHA-256** (el fragmento `#sha256=`), mientras que npm usa SHA-512.

## Decisión

**Fuente de datos.** El índice se pide a `https://pypi.org/simple/<nombre>/` con `Accept: application/vnd.pypi.simple.v1+json, text/html`; si la respuesta es JSON se parsea como PEP 691 y si es HTML como PEP 503. El hash SHA-256 se toma del fragmento `#sha256=` (HTML) o de `hashes.sha256` (JSON); PEP 658 de `data-core-metadata` (HTML) o `core-metadata` (JSON); *yanked* de `data-yanked`/`yanked`.

**Formato servido.** Se elige según el header `Accept` del cliente: JSON si pide explícitamente un tipo JSON con un `q` mayor o igual al del HTML, y HTML en cualquier otro caso (sin header, solo `*/*`, o HTML preferido). Se responden los *media types* de PEP 691 (`application/vnd.pypi.simple.v1+json`) y PEP 503 (`text/html`).

**Reescritura de URLs.** Los enlaces a `files.pythonhosted.org` se reescriben a `{SUPPLY_SHIELD_PROXY_BASE_URL}/pypi/packages/sha256/<hash>/<ruta>`, conservando el fragmento `#sha256=` (lo que lee pip para `--require-hashes`). El hash va en la ruta y no en una query: pip y uv derivan la URL de la metadata PEP 658 como `<url sin fragmento>.metadata`, y con `?sha256=<hash>` quedaría `?sha256=<hash>.metadata`. Un archivo sin hash se reescribe a `/pypi/packages/<ruta>` y se reenvía sin verificar. Una URL que no apunte a ese host se deja intacta.

**Fechas y mantenedores.** Para `UpstreamMetadata` (versiones, fechas y responsables) se consulta la JSON API `https://pypi.org/pypi/<nombre>/json`. La fecha de una versión es la primera subida entre sus archivos. PyPI no expone scripts de instalación, así que `scripts` queda vacío.

**Verificación de integridad.** Se reutiliza `open_upstream_download` (D1-1) con `algorithm="sha256"`: valida status, codificación y tamaño antes de responder, de modo que un 404 o 503 del upstream llega al cliente como 502 y no como un 200 cortado. Si la ruta lleva hash, el proxy lo calcula mientras reenvía los bytes y aborta la respuesta si no coincide. Los archivos `.metadata` no se verifican con el hash del wheel (es otro contenido); el cliente los comprueba con el hash de `data-core-metadata`.

**Rutas y autenticación.** Siguen el ADR [0013](0013-proxy-routes-with-per-ecosystem-prefix.md): `/pypi/simple/<proyecto>/` sirve el índice (y sin barra final redirige con 307) y `/pypi/packages/...` reenvía el artefacto. Todas exigen el token de proyecto (ADR [0006](0006-project-auth-tokens.md)). El cliente HTTP es el único de la app (ADR 0005). El bloqueo por política es D1-3.

## Alternativas consideradas

- **Servir el HTML del upstream tal cual**: lo más simple, pero deja los `href` apuntando a `files.pythonhosted.org`, con lo que el cliente se saltaría el firewall.
- **Construir el índice solo desde la JSON API `/pypi/<nombre>/json`**: da fechas y hashes en un mismo sitio, pero no publica la metadata PEP 658 ni la forma exacta del índice simple; el cliente perdería PEP 658 y habría que sintetizar el índice.
- **Pasar el upstream en cada formato ya resuelto**: obligaría a dos peticiones (una por formato) y a confiar en que el upstream negocie igual que el cliente.
- **Hash en una query (`?sha256=`)**: rompe la metadata PEP 658, porque pip añade `.metadata` al final de la URL. Por eso el hash va en la ruta.
- **Un cliente `httpx` propio en cada petición**: dispersa timeouts y allowlist; por eso se reutiliza el compartido (ADR 0005).

## Consecuencias

- `pip` y `uv` funcionan contra el proxy sin modificar bytes ni hashes; el fragmento `#sha256=` que ve el cliente es el del upstream.
- El proxy valida el host antes de abrir conexión y no sigue redirects, igual que el resto de la salida (ADR 0005).
- Falta D1-3: las rutas no eliminan versiones bloqueadas. Hasta entonces, el espejo sirve todo lo que el upstream permite a quien tenga token.
- Los archivos de `files.pythonhosted.org` con nombre de host distinto no se reescriben; si PyPI cambia de CDN, hay que actualizar `proxy/pypi/urls.py`.
- `DigestAlgorithm` incluye `sha256` y `open_upstream_download` acepta un digest `None` para reenviar sin verificar.
