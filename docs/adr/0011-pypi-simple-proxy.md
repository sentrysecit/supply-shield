# 0011. Espejo de PyPI: índice simple (PEP 503/691), PEP 658 y verificación SHA-256

- **Estado**: Propuesto
- **Fecha**: 2026-11-14

## Contexto

D2-2 pide que `pip install`, `pip install --require-hashes` y `uv pip install` funcionen a través de Supply-Shield, tanto con wheels como con sdists. El proxy es un *registry mirror* ([ADR 0002](0002-registry-mirror-instead-of-mitm-proxy.md)): el cliente apunta `index-url` a este servicio y no se intercepta TLS. Toda la salida a `pypi.org` y `files.pythonhosted.org` pasa por el cliente compartido ([ADR 0005](0005-shared-upstream-client-with-host-allowlist.md)), que aplica la allowlist y los reintentos.

Hay que resolver varias piezas del protocolo de PyPI: el índice simple en HTML (PEP 503) y JSON (PEP 691), la metadata PEP 658 publicada junto a cada archivo, las versiones *yanked*, la normalización de nombres (`Django` = `django`) y las fechas de publicación, que el índice simple no trae. Además, el hash de PyPI es **SHA-256** (el fragmento `#sha256=`), mientras que npm usa SHA-512.

## Decisión

**Fuente de datos.** El índice se pide a `https://pypi.org/simple/<nombre>/` con `Accept: application/vnd.pypi.simple.v1+json, text/html`; si la respuesta es JSON se parsea como PEP 691 y si es HTML como PEP 503. El hash SHA-256 se toma del fragmento `#sha256=` (HTML) o de `hashes.sha256` (JSON); PEP 658 de `data-core-metadata` (HTML) o `core-metadata` (JSON); *yanked* de `data-yanked`/`yanked`.

**Formato servido.** Se elige según el header `Accept` del cliente: JSON si pide explícitamente un tipo JSON con un `q` mayor o igual al del HTML, y HTML en cualquier otro caso (sin header, solo `*/*`, o HTML preferido). Se responden los *media types* de PEP 691 (`application/vnd.pypi.simple.v1+json`) y PEP 503 (`text/html`).

**Reescritura de URLs.** Los enlaces a `files.pythonhosted.org` se reescriben a `/packages/<ruta>` del proxy, conservando el fragmento `#sha256=` (lo que lee pip para `--require-hashes`) y añadiendo el mismo hash como `?sha256=` para que el proxy lo verifique al reenviar. Una URL de archivo que no apunte a ese host se deja intacta.

**Fechas y mantenedores.** Para `UpstreamMetadata` (versiones, fechas y responsables) se consulta la JSON API `https://pypi.org/pypi/<nombre>/json`. La fecha de una versión es la primera subida entre sus archivos. PyPI no expone scripts de instalación, así que `scripts` queda vacío.

**Verificación de integridad.** El helper de streaming de D1-1 se generaliza con un parámetro `algorithm` (`sha512` por defecto para npm, `sha256` para PyPI). Si el cliente pide un artefacto con `?sha256=`, el proxy calcula el SHA-256 mientras reenvía los bytes y corta la descarga si no coincide; sin hash, reenvía sin verificar y confía en la comprobación del cliente.

**Rutas.** `/simple/<proyecto>/` sirve el índice (y `/simple/<proyecto>` redirige con 307), `/packages/<ruta>` reenvía el artefacto. El cliente HTTP se construye al crear la app y se cierra en el *lifespan*. Estas rutas todavía no exigen token ni aplican bloqueos: el enforcement y la autenticación son D1-3.

## Alternativas consideradas

- **Servir el HTML del upstream tal cual**: lo más simple, pero deja los `href` apuntando a `files.pythonhosted.org`, con lo que el cliente se saltaría el firewall.
- **Construir el índice solo desde la JSON API `/pypi/<nombre>/json`**: da fechas y hashes en un mismo sitio, pero no publica la metadata PEP 658 ni la forma exacta del índice simple; el cliente perdería PEP 658 y habría que sintetizar el índice.
- **Pasar el upstream en cada formato ya resuelto**: obligaría a dos peticiones (una por formato) y a confiar en que el upstream negocie igual que el cliente.
- **Verificar con el hash en la ruta en lugar de la query**: más limpio, pero pip solo lee el fragmento `#sha256=`, y sin query el proxy no tendría el hash esperado para verificar.
- **Un cliente `httpx` propio en cada petición**: dispersa timeouts y allowlist; por eso se reutiliza el compartido (ADR 0005).

## Consecuencias

- `pip` y `uv` funcionan contra el proxy sin modificar bytes ni hashes; el fragmento `#sha256=` que ve el cliente es el del upstream.
- El proxy valida el host antes de abrir conexión y no sigue redirects, igual que el resto de la salida (ADR 0005).
- Falta D1-3: las rutas no autentican ni eliminan versiones bloqueadas. Hasta entonces, el espejo sirve todo lo que el upstream permite.
- Los archivos de `files.pythonhosted.org` con nombre de host distinto no se reescriben; si PyPI cambia de CDN, hay que actualizar `proxy/pypi/urls.py`.
- El helper `stream_upstream` cambia de firma (parámetro `algorithm`, digest opcional); los llamadores posicionales existentes siguen funcionando.
