# API de administración

La API de administración (D4-5) permite consultar lo que hizo el firewall y cambiar lo que bloquea sin reiniciar el servicio. Vive en `/api/v1/admin` y la documentación interactiva completa está en `/docs` (OpenAPI en `/openapi.json`). Las decisiones de diseño están en el [ADR 0009](adr/0009-admin-api.md).

| Método | Ruta | Qué hace |
| --- | --- | --- |
| `GET` | `/api/v1/admin/decisions` | Lista y filtra las decisiones del proxy. |
| `GET` | `/api/v1/admin/sboms` | Lista los SBOMs por proyecto o build. |
| `GET` | `/api/v1/admin/sboms/{id}` | Descarga un SBOM CycloneDX. |
| `POST` | `/api/v1/admin/exceptions` | Crea una excepción; se aplica en la petición siguiente. |
| `GET` | `/api/v1/admin/exceptions` | Lista las excepciones creadas por la API. |
| `GET` | `/api/v1/admin/exceptions/{id}` | Muestra una excepción. |
| `POST` | `/api/v1/admin/exceptions/{id}/revoke` | Revoca una excepción; vuelve a bloquear en la petición siguiente. |
| `POST` | `/api/v1/admin/projects/{project}/tokens` | Crea un token de proyecto y lo muestra una sola vez. |
| `GET` | `/api/v1/admin/projects/{project}/tokens` | Lista los tokens de un proyecto (sin el secreto). |
| `GET` | `/api/v1/admin/projects/{project}/tokens/{id}` | Muestra un token de proyecto. |
| `POST` | `/api/v1/admin/projects/{project}/tokens/{id}/revoke` | Revoca un token de proyecto. |
| `GET` | `/api/v1/admin/policy` | Muestra la política activa, su origen y su sha256. |
| `POST` | `/api/v1/admin/policy/reload` | Recarga `config/policy.yaml` sin reiniciar. |

## Habilitarla

1. Genera un token y su hash:

   ```bash
   uv run python scripts/create_admin_token.py
   ```

   El script imprime el token (guárdalo en un gestor de secretos; no se vuelve a mostrar) y la línea para el `.env`.

2. Configura el hash, **entre comillas simples** porque lleva `$`:

   ```dotenv
   SUPPLY_SHIELD_ADMIN_TOKEN_HASH='pbkdf2_sha256$100000$...$...'
   ```

3. Aplica las migraciones de la base. Con `docker compose` (`make up`) lo hace solo el servicio `migrate` antes de arrancar la app. Fuera de Docker, corre `uv run alembic upgrade head`, que toma la base del entorno o del `.env`, igual que el servicio.

Sin `SUPPLY_SHIELD_ADMIN_TOKEN_HASH`, la API responde **503**. Si el valor no tiene formato de hash (por ejemplo, pegaste el token en claro), **el servicio no arranca**.

| Variable | Por defecto | Qué controla |
| --- | --- | --- |
| `SUPPLY_SHIELD_ADMIN_TOKEN_HASH` | *(vacío)* | Hash pbkdf2 del token de administrador. Vacío = API deshabilitada. |
| `SUPPLY_SHIELD_ADMIN_EXCEPTION_MAX_DAYS` | `90` | Vencimiento máximo de una excepción, en días desde hoy. |
| `SUPPLY_SHIELD_POLICY_EXCEPTIONS_REFRESH_SECONDS` | `30` | Cada cuánto relee cada proceso las excepciones de la base. |

## Autenticación

Todas las rutas exigen `Authorization: Bearer <token-de-administrador>`.

- Un **token de proyecto no sirve**, aunque sea válido para el proxy.
- No se acepta Basic auth ni el token en la URL.
- Sin token o con uno inválido: **401** con `WWW-Authenticate: Bearer`.
- Las respuestas llevan `Cache-Control: no-store`.
- El token nunca se escribe en los logs; los fallos se registran con la IP y la ruta (`admin.auth_invalid_token`).

```bash
export SS_ADMIN="ssa_..."   # el token, no el hash
curl -H "Authorization: Bearer $SS_ADMIN" http://localhost:8000/api/v1/admin/policy
```

**Recomendaciones de despliegue:**

- No publicar `/api/v1/admin` fuera de la red interna (filtra la ruta en el reverse proxy).
- Sirve el servicio solo por **HTTPS**: los tokens de administrador y de proyecto viajan en la cabecera `Authorization`, y en HTTP plano cualquiera en la red puede leerlos.

## Excepciones

### Crear

```bash
curl -X POST http://localhost:8000/api/v1/admin/exceptions \
  -H "Authorization: Bearer $SS_ADMIN" -H "Content-Type: application/json" \
  -d '{
        "project": "frontend",
        "package": {"ecosystem": "npm", "name": "lodash", "version": "4.17.20"},
        "advisory": "GHSA-35jh-r3h4-6jhm",
        "rule": "vulnerability",
        "justification": "La función afectada (template) no se usa; migración en JIRA-123.",
        "author": "equipo-seguridad",
        "expires": "2026-12-01"
      }'
```

Responde **201** con la excepción y su `Location`. Desde la petición siguiente, el proxy deja pasar `lodash@4.17.20` en `frontend`, y la decisión dice qué excepción se aplicó, quién la pidió y cuándo vence.

Los campos significan lo mismo que en [`config/policy.yaml`](policy.md#excepciones), con dos diferencias: `project` es uno solo (vacío = todos los proyectos) y hay reglas extra para que una excepción no quede más amplia de lo necesario:

| Se rechaza (422) | Por qué |
| --- | --- |
| Sin `package` ni `advisory` | No sabría a qué aplicarse. |
| `package` sin versión, sin `advisory` y sin `rule` | Cubriría cualquier hallazgo futuro del paquete. Indica la regla (p. ej. `typosquatting` para un falso positivo). |
| Lo anterior con `rule: known_malware` | Una excepción de malware debe nombrar la versión o el advisory. |
| `project` que no está en `config/projects.yaml` | Una excepción con un proyecto mal escrito nunca se aplicaría. |
| `expires` pasado o a más de `ADMIN_EXCEPTION_MAX_DAYS` días | Las excepciones son temporales; renuévala con una nueva justificación. |
| Nombre de paquete o regla inválidos, textos en blanco o demasiado largos, campos desconocidos | Validación del esquema. |

### Listar, ver y revocar

```bash
# Activas, vencidas o revocadas; filtros opcionales por proyecto, paquete y advisory
curl -H "Authorization: Bearer $SS_ADMIN" \
  "http://localhost:8000/api/v1/admin/exceptions?status=active&ecosystem=npm&package=lodash"

curl -X POST http://localhost:8000/api/v1/admin/exceptions/<id>/revoke \
  -H "Authorization: Bearer $SS_ADMIN" -H "Content-Type: application/json" \
  -d '{"revoked_by": "ciso", "reason": "La migración ya terminó."}'
```

Revocar **no borra** la excepción: queda con `status: revoked`, quién la revocó, cuándo y por qué. Revocar dos veces da **409**.

Este listado muestra solo las excepciones creadas por la API. Las del archivo de política aparecen en `GET /api/v1/admin/policy`.

## Tokens de proyecto

> **Importante:** la autenticación del proxy todavía lee solo `config/projects.yaml`. Los tokens que crea la API quedan guardados y se pueden revocar, pero **no abren el proxy** hasta que la autenticación lea la base (otro PR, ver el [ADR 0010](adr/0010-project-tokens-admin-api.md)).

### Crear

```bash
curl -X POST http://localhost:8000/api/v1/admin/projects/frontend/tokens \
  -H "Authorization: Bearer $SS_ADMIN" -H "Content-Type: application/json" \
  -d '{"name": "ci-github", "created_by": "equipo-plataforma", "expires": "2027-02-01"}'
```

Responde **201** con el campo `token` (`ssp_<key_id>_<secreto>`). **Es la única vez que se muestra:** guárdalo como secreto del pipeline o en un gestor de secretos. El servicio guarda solo su hash; si se pierde, crea otro y revoca el anterior.

| Campo | Qué es |
| --- | --- |
| `name` | Para qué es el token (`ci-github`, `laptop-ana`). Sirve para reconocerlo al revocarlo. |
| `created_by` | Quién lo pide o aprueba. |
| `expires` | Último día en que vale, inclusive, en UTC. Opcional (vacío = no vence), pero se recomienda ponerlo. No puede ser una fecha pasada. |

El proyecto debe existir en `config/projects.yaml` (si no, **404**): ahí está su configuración, como los nombres internos.

### Listar, ver y revocar

```bash
curl -H "Authorization: Bearer $SS_ADMIN" \
  "http://localhost:8000/api/v1/admin/projects/frontend/tokens?status=active"

curl -X POST http://localhost:8000/api/v1/admin/projects/frontend/tokens/<id>/revoke \
  -H "Authorization: Bearer $SS_ADMIN" -H "Content-Type: application/json" \
  -d '{"revoked_by": "ciso", "reason": "Apareció en un log público."}'
```

Los listados nunca muestran el token ni su hash. `hint` (`ssp_<key_id>_…`) es la parte pública del token: si encuentras un token filtrado, su `key_id` te dice qué registro revocar. Revocar no borra el registro y revocar dos veces da **409**. Un proyecto que ya no está en el YAML se puede listar y revocar igual.

### Sin base de datos: el script

```bash
uv run python scripts/create_project_token.py frontend
```

Genera un token con el mismo formato e imprime la entrada para `config/projects.yaml`. Es la forma de crear tokens que el proxy acepta hoy. Si el proyecto ya existe, cambia solo su `token_hash` y reinicia el servicio.

## Política

```bash
curl -H "Authorization: Bearer $SS_ADMIN" http://localhost:8000/api/v1/admin/policy
curl -X POST -H "Authorization: Bearer $SS_ADMIN" http://localhost:8000/api/v1/admin/policy/reload
```

`GET` devuelve la política del archivo, su `sha256` (para comprobar qué versión quedó cargada), cuándo se cargó, cuántas excepciones de la API se aplican encima y la `revision`, que sube con cada cambio.

`reload` vuelve a leer `SUPPLY_SHIELD_POLICY_FILE`. Si el archivo no existe o no es válido responde **422 con el error** y **la política anterior sigue activa**: un error al editar el YAML no deja al proxy sin reglas. Las excepciones de la API se conservan.

## Decisiones y SBOMs

```bash
# Bloqueos del proyecto frontend en un build
curl -H "Authorization: Bearer $SS_ADMIN" \
  "http://localhost:8000/api/v1/admin/decisions?project=frontend&build_id=1234&action=block"

# SBOMs de un build y descarga del CycloneDX
curl -H "Authorization: Bearer $SS_ADMIN" "http://localhost:8000/api/v1/admin/sboms?build_id=1234"
curl -H "Authorization: Bearer $SS_ADMIN" http://localhost:8000/api/v1/admin/sboms/<id> -o sbom.json
```

Filtros de decisiones: `project`, `build_id`, `ecosystem`, `package` (exige `ecosystem`; se normaliza como en el proxy), `version`, `action` (`allow`, `warn`, `block`), `degraded`, `since` (inclusive) y `until` (exclusive). Una fecha sin zona horaria se toma como UTC. Cada decisión trae sus motivos y todos sus hallazgos.

Los listados se paginan con `limit` (1 a 200, por defecto 50) y `offset`; `next_offset` es `null` en la última página. Un parámetro desconocido (p. ej. `?pakage=`) da 422 en lugar de ignorarse.

Hoy nadie escribe todavía decisiones ni SBOMs en la base: lo harán el proxy (D1-3), el SBOM observado (D2-4) y el escaneo de lockfiles (D5-6) con `record_decision` y `record_sbom` de `supply_shield.db.repository`.

## Varias réplicas

Cada proceso aplica al instante lo que crea o revoca él mismo y relee la base cada `SUPPLY_SHIELD_POLICY_EXCEPTIONS_REFRESH_SECONDS`. Con varias réplicas, un cambio llega a las demás en ese plazo. Si la base no responde, cada proceso se queda con las excepciones que ya tenía; si al arrancar no la encuentra, arranca solo con las del archivo (más estricto). Cada lectura espera como mucho 5 s, así que una base que no contesta no demora el arranque del proxy.

## Errores

| Código | Cuándo |
| --- | --- |
| 401 | Falta el token de administrador o no es válido. |
| 404 | No existe la excepción, el SBOM o el token pedido, o el proyecto no está en `config/projects.yaml` (al crear un token). |
| 409 | La excepción o el token ya estaba revocado. |
| 422 | Cuerpo o filtros inválidos, o archivo de política inválido al recargar. |
| 503 | La API está deshabilitada (sin hash) o no tiene base de datos. |
