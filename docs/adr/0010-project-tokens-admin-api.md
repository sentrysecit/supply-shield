# 0010. Tokens de proyecto gestionados desde la API de administración

- **Estado**: Aceptado
- **Fecha**: 2026-11-10

## Contexto

Los tokens de proyecto (ADR 0006) se generan a mano y su hash se escribe en `config/projects.yaml`. Para crear o revocar uno hay que editar el archivo y reiniciar el servicio, y el archivo no guarda quién pidió el token, para qué ni cuándo se revocó. El ADR 0006 preveía que, con la base de datos (D5-5), los tokens se gestionarían desde la API de administración (D4-5). La tabla `tokens` existía desde D5-5, pero nadie la usaba.

Además, la autenticación actual prueba el token contra el hash de **cada** proyecto: con pbkdf2 de 100 000 iteraciones, eso no escala a muchos tokens.

## Decisión

**Formato del token**

- `ssp_<key_id>_<secreto>`: `key_id` son 16 caracteres hexadecimales (64 bits) y el secreto son 256 bits aleatorios (`secrets.token_urlsafe(32)`).
- `key_id` es **público y único** (índice único en `tokens.key_id`). Sirve para tres cosas: buscar el token en la base sin probar el hash contra todos, mostrar un token en los listados (`ssp_<key_id>_…`) sin exponer el secreto y saber a qué registro corresponde un token filtrado.
- El hash pbkdf2 (mismo formato que el ADR 0006) cubre el token completo. El prefijo fijo `ssp_` permite que los escáneres de secretos (p. ej. los patrones propios de GitHub) lo detecten si se sube a un repositorio.

**API**

- `POST /api/v1/admin/projects/{project}/tokens` genera el token **en el servidor** y lo devuelve **una sola vez**. La base guarda solo el hash. El proyecto debe existir en `config/projects.yaml`, porque ahí está su configuración (nombres internos, etc.).
- `GET …/tokens` y `GET …/tokens/{id}` muestran nombre, autor, vencimiento y estado, nunca el token ni su hash. También funcionan para un proyecto que ya no está en el YAML, para poder revocar lo que quedó.
- `POST …/tokens/{id}/revoke` marca el token como revocado y guarda quién, cuándo y por qué, **sin borrarlo**. Revocar dos veces da 409.
- El vencimiento es opcional y por día (UTC, inclusive, como en las excepciones). En la base se guarda como el primer instante en que el token deja de valer (`expires_at`), que es lo que comparará la autenticación.
- Los logs registran `key_id`, proyecto y autor, nunca el token ni el hash.

**Script**

- `scripts/create_project_token.py <proyecto>` genera un token con el mismo formato y la entrada para `config/projects.yaml`. Sirve hoy, mientras la autenticación solo lee el YAML, y después para entornos sin base de datos.

## Alternativas consideradas

- **Que el cliente envíe el hash y el servidor solo lo guarde**: el servidor no podría garantizar la entropía ni el formato del token.
- **Token sin identificador público, buscando por hash**: con pbkdf2 y sal aleatoria no se puede indexar por hash. Un hash sin sal (sha256) sí se puede indexar y alcanza para tokens de 256 bits, pero cambiaría el formato del ADR 0006. El `key_id` resuelve la búsqueda sin cambiar el hash.
- **Usar el `id` (UUID) de la fila como identificador público dentro del token**: funciona, pero alarga el token y mezcla la clave primaria interna con un dato que circula fuera del servicio.
- **Exigir vencimiento**: obligaría a rotar todo, pero rompería pipelines olvidados sin aviso. Queda opcional y se recomienda en la guía. Se puede exigir más adelante con un máximo configurable, como en las excepciones.
- **Borrar al revocar**: pierde la historia que necesita la auditoría.

## Consecuencias

- **La autenticación del proxy todavía no lee la tabla `tokens`**: los tokens creados por la API no abren el proxy hasta que `TokenStore` también los lea. Ese cambio queda para otro PR. Mientras tanto, el token que vale es el de `config/projects.yaml`.
- Esa integración podrá buscar por `key_id` y verificar un solo hash por petición, en lugar de recorrer todos los proyectos.
- La migración `7b3f0c2e8d41` borra las filas que hubiera en `tokens` (nadie las escribía y no tienen `key_id`). Su `downgrade` borra los tokens revocados, para que no vuelvan a valer en un esquema que no sabe que lo están. Bajar y volver a subir la migración borra todos los tokens de la API: hay que emitirlos de nuevo.
- Quien tenga el token de administrador puede emitir tokens para cualquier proyecto. Eso ya podía lograrlo con una excepción, así que el riesgo no cambia; la diferencia es que queda registrado.
