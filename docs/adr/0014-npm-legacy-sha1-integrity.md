# 0014. Verificar con SHA-1 los tarballs npm antiguos sin SHA-512

- **Estado**: Aceptado
- **Fecha**: 2026-11-24

## Contexto

El proxy verifica cada tarball mientras lo reenvía (ADR [0005](0005-shared-upstream-client-with-host-allowlist.md)) y corta la descarga si el hash no coincide. En npm, el hash esperado sale del `dist` de cada versión del packument:

- `dist.integrity` es una cadena SSRI (`sha512-<base64>`), que puede traer varias entradas separadas por espacios, también de otros algoritmos.
- `dist.shasum` es el SHA-1 en hexadecimal. Es el único hash de las versiones publicadas antes de que npm adoptara `integrity` (2017), y sigue apareciendo en dependencias transitivas muy usadas.

Si el proxy solo aceptara SHA-512, esas versiones antiguas no se podrían instalar a través de él. Si las sirviera sin verificar, el firewall dejaría pasar artefactos sin ningún control de integridad.

## Decisión

Para cada versión, el proxy usa el digest más fuerte que publica el upstream:

1. la entrada `sha512` de `dist.integrity`, esté en la posición que esté;
2. si no hay, la entrada `sha1` de `dist.integrity`;
3. si tampoco hay, `dist.shasum` (SHA-1 en hexadecimal).

Si una entrada `sha512` o `sha1` está mal formada, o la versión no publica ninguno de esos digests, el proxy no sirve el tarball y responde 502. Cuando verifica con SHA-1 registra el evento `npm.tarball.legacy_digest` con el paquete y el archivo.

## Alternativas consideradas

- **Bloquear las versiones sin SHA-512**: es más estricto, pero rompe `npm install` de proyectos reales que dependen de paquetes antiguos, y empuja a los equipos a saltarse el proxy.
- **Servirlas sin verificar**: deja pasar un artefacto corrompido o manipulado en tránsito, justo lo que el ADR 0005 quiere evitar.

## Consecuencias

- Las versiones antiguas se instalan a través del proxy con el mismo control que aplica el propio cliente npm, que también acepta `sha1` cuando es lo único publicado.
- SHA-1 tiene colisiones prácticas, pero un ataque contra este control necesita una segunda preimagen de un tarball ya publicado, algo que hoy no es factible. Si npm publicara un SHA-512 para esas versiones, el proxy lo usaría sin cambios.
- Una política futura (D1-3) podría bloquear las versiones que solo tienen SHA-1. El evento `npm.tarball.legacy_digest` permite medir cuántas se usan antes de decidirlo.
