# 0009. API de administración con token propio y política recargable en caliente

- **Estado**: Aceptado
- **Fecha**: 2026-11-09

## Contexto

Hasta ahora, cambiar la política o pedir una excepción exigía editar `config/policy.yaml` y reiniciar el servicio (ADR 0007), y no había forma de consultar qué decidió el proxy ni qué SBOMs se guardaron. D4-5 pide una API de administración que permita:

- listar y filtrar decisiones;
- ver los SBOMs por proyecto o build;
- crear, listar y revocar excepciones;
- ver la política activa y recargarla sin reiniciar.

Esa API cambia lo que el firewall bloquea, así que es el punto más sensible del servicio: quien la controle puede dejar pasar cualquier paquete. Además, D4-5 exige que **una excepción recién creada desbloquee el paquete en la petición siguiente**.

## Decisión

**Autenticación**

- La API vive en `/api/v1/admin` y exige un **token de administrador propio**, distinto de los tokens de proyecto. Un token de proyecto (el de `.npmrc` o `pip.conf` de cada pipeline, que circula por muchas máquinas) nunca abre la API.
- Solo se acepta por `Authorization: Bearer`. El servicio guarda solo el **hash pbkdf2** (`SUPPLY_SHIELD_ADMIN_TOKEN_HASH`, mismo formato que ADR 0006), en un `SecretStr`. El hash se verifica en un hilo aparte (`asyncio.to_thread`) para no bloquear el bucle de eventos.
- Sin hash configurado, la API responde **503** (deshabilitada) y no se abre la base. Si el valor no tiene formato de hash (p. ej., se pegó el token en claro), **el servicio no arranca**.
- Las respuestas llevan `Cache-Control: no-store`. Los fallos de autenticación se registran con IP y ruta, nunca con el token.

**Política activa (`PolicyHolder`)**

- La política deja de ser un objeto fijo en `app.state.policy` y pasa a `app.state.policy_holder`: guarda la política del archivo y las excepciones de la API, y publica en cada cambio una `Policy` nueva e inmutable. El motor (`evaluate`) sigue siendo una función pura que recibe esa `Policy`.
- Cada cambio sube `revision`. Quien cachee decisiones (D3-6) debe incluirla en la clave de caché; así una excepción nueva se nota en la petición siguiente aunque la decisión estuviera cacheada. Un refresco que no cambia nada no sube la revisión.
- **Recarga**: `POST /policy/reload` vuelve a leer el archivo configurado (la petición no puede elegir la ruta). Si no existe o es inválido, responde 422 y **la política anterior sigue activa**. Al arrancar se mantiene la regla del ADR 0007: archivo inválido = el servicio no arranca.

**Excepciones en la base**

- Las excepciones creadas por la API se guardan en la tabla `exceptions` (migración `4c1e7a9d2b30`), con la misma forma que las del YAML: paquete y/o advisory, regla opcional, un proyecto opcional (vacío = todos), justificación, autor y vencimiento por **día** (UTC, inclusive). Las del YAML siguen funcionando igual y se suman.
- **Revocar no borra**: guarda quién, cuándo y por qué. La auditoría conserva todo el historial.
- Crear o revocar escribe en la base y actualiza el `PolicyHolder` **bajo el mismo candado** que el refresco periódico, para que un refresco que leyó la base antes de una revocación no pueda volver a publicar la excepción revocada.
- Cada proceso relee las excepciones cada `SUPPLY_SHIELD_POLICY_EXCEPTIONS_REFRESH_SECONDS` (30 s por defecto) para ver lo que hicieron otras réplicas. Si la base no responde, se queda con lo que tenía: nunca agrega excepciones que no pudo leer, y una fila corrupta se descarta (eso solo vuelve más estricta la política). Cada lectura, también la del arranque, se abandona a los 5 s: una base que no contesta (en lugar de rechazar la conexión) no demora el arranque del proxy ni retiene el candado de la política.

**Validación de excepciones (defensa contra excepciones demasiado amplias)**

- El proyecto debe existir en `config/projects.yaml`: un nombre mal escrito no crea una excepción que nunca se aplica.
- `expires` no puede estar en el pasado ni a más de `SUPPLY_SHIELD_ADMIN_EXCEPTION_MAX_DAYS` días (90 por defecto).
- Una excepción por paquete **sin versión ni advisory** cubriría cualquier hallazgo futuro de ese paquete, incluido malware. Se exige `rule` y se prohíbe `known_malware` en ese caso.
- Campos desconocidos, en el cuerpo y en los filtros de consulta, dan 422.

**Decisiones y SBOMs**

- La tabla `decisions` gana `build_id` y `findings` (todos los hallazgos, como JSON) para que la auditoría pueda reconstruir cada decisión. El repositorio ofrece `record_decision` y `record_sbom` para que D1-3, D2-4 y D5-6 escriban; la API solo lee.
- Paginación por `limit` (máx. 200) y `offset`, con `next_offset`.

## Alternativas consideradas

- **Reutilizar los tokens de proyecto con un rol "admin"**: los tokens de proyecto viven en `.npmrc` y `pip.conf` de muchas máquinas y pipelines; si uno se filtra no debe poder cambiar la política.
- **JWT/OAuth2 con usuarios y roles**: permitiría saber quién hizo cada cambio sin confiar en el campo `author`, pero exige un proveedor de identidad. Para el MVP basta un token; el campo `author`/`revoked_by` y el log de auditoría cubren la trazabilidad. Se puede agregar sin cambiar los endpoints.
- **Escribir las excepciones en `config/policy.yaml`**: el archivo es configuración versionada en git y montada como solo lectura en el contenedor; reescribirlo desde la API mezclaría dos fuentes de verdad y no funciona con varias réplicas.
- **Borrar las excepciones al revocarlas**: más simple, pero pierde la historia que necesita la auditoría.
- **Leer las excepciones de la base en cada evaluación**: siempre al día entre réplicas, pero agrega una consulta a la base en el camino caliente del proxy (D3-6 pide menos de 10 ms con caché caliente).
- **Recargar la política vigilando el archivo (inotify)**: no funciona igual en todos los sistemas de archivos de contenedores y no permite devolver el error de validación a quien editó el archivo.
- **Probar el repositorio solo con mocks**: no detectaría errores de SQL. Las pruebas unitarias usan una base SQLite temporal en memoria (`aiosqlite`, solo dependencia de desarrollo); el DDL de PostgreSQL de la migración se revisa con `alembic upgrade --sql`.

## Consecuencias

- Crear o revocar una excepción tiene efecto inmediato en el proceso que atiende la petición y, como mucho, en `SUPPLY_SHIELD_POLICY_EXCEPTIONS_REFRESH_SECONDS` en las demás réplicas. Con una sola réplica (el `docker compose` actual) el efecto es inmediato.
- El token de administrador es un secreto compartido: quien lo tenga puede crear excepciones. Debe vivir en un gestor de secretos y rotarse (generar uno nuevo con `scripts/create_admin_token.py` y cambiar el hash). Se recomienda además no exponer `/api/v1/admin` fuera de la red interna (reverse proxy o regla de red).
- No hay límite de intentos de autenticación: pbkdf2 encarece cada intento, pero el rate limit por cliente queda para D1-4.
- `author` y `revoked_by` los declara quien llama; no están verificados contra una identidad.
- `app.state.policy` desaparece: el código que necesite la política la toma de `app.state.policy_holder.current`.
- El `downgrade` de la migración borra las excepciones que no caben en el esquema anterior (sin proyecto, sin paquete o revocadas).
