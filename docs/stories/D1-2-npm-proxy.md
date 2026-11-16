# D1-2 · Proxy de npm

- **Rama:** `feat/back-01-npm-proxy`
- **Issue:** BACK-01
- **Módulos:** `src/supply_shield/proxy/npm/` (rutas, packument, tarball)
- **Tamaño:** L · **Semana:** 2–3
- **Depende de:** D1-1 → **satisfecha**: cliente upstream + `stream_upstream` implementados en `feat/d1-1-upstream-client`. D4-1 → **satisfecha**: `UpstreamMetadata`, `PackageRef`, `PackageRelease`, `Maintainer` en `main` (PR #8).
- **ADR del área:** por definir (numeración 0006+, decisión de ruta del proxy)
- **Estado:** implementado

## Contexto

D1-2 es el adaptador de npm: hace que `npm install` hable con Supply-Shield como si este fuera `registry.npmjs.org`. Hoy el proxy solo tiene endpoints de salud; esta tarea le añade las rutas que imitan el protocolo del registro npm (packument y tarballs) y conecta la metadata con el modelo común `UpstreamMetadata` que consumirán los detectores.

El proxy funciona como *registry mirror* (ADR 0002): los clientes apuntan `registry=` al proxy. El proxy reenvía las peticiones permitidas al upstream, reescribe las URLs de descarga para que el cliente vuelva a pasar por él, y sirve los tarballs con verificación de integridad usando D1-1 (ADR 0005). Esta tarea **no bloquea** nada: sirve todo lo que el upstream sirve. Los bloqueos llegan en D1-3 (semana 4).

---

## Historias de usuario

### HU-1 · Servir la metadata del paquete en sus dos formatos

**Como** cliente `npm install`,
**quiero** recibir el packument del paquete en el formato que pido (completo o abreviado),
**para** resolver las versiones como si hablara con el registro oficial.

**Criterios de aceptación (Given / When / Then)**

1. **Given** una petición `GET /npm/{paquete}` sin `Accept` especial, **when** el proxy la procesa, **then** reenvía la petición a `registry.npmjs.org` y devuelve el packument completo sin alterar el JSON.
2. **Given** una petición con `Accept: application/vnd.npm.install-v1+json`, **when** el proxy la procesa, **then** reenvía ese `Accept` al upstream y devuelve el formato abreviado.
3. **Given** un paquete que no existe upstream, **when** el proxy lo pide, **then** devuelve el mismo status code que el upstream (404) sin inventar una respuesta.

### HU-2 · Soportar paquetes con scope

**Como** desarrollador que instala `@types/node`,
**quiero** que el proxy resuelva paquetes con scope aunque lleguen URL-encodeados,
**para** que las dependencias scoped funcionen igual que las sin scope.

**Criterios de aceptación**

1. **Given** una petición a `GET /npm/@types%2fnode`, **when** el proxy decodifica el nombre, **then** pide `@types/node` al upstream y devuelve su packument.
2. **Given** un scope con varios segmentos válido, **when** se procesa, **then** se normaliza y se reenvía correctamente.

### HU-3 · Reescribir `dist.tarball` y descargar tarballs con verificación

**Como** cliente `npm` que va a bajar un tarball,
**quiero** que la URL de descarga del packument apunte al proxy y que el proxy me sirva el archivo verificado,
**para** que todo lo que instale pase por el firewall con su integridad confirmada.

**Criterios de aceptación**

1. **Given** un packument cuyo `dist.tarball` apunta a `registry.npmjs.org`, **when** el proxy lo sirve, **then** el `dist.tarball` del JSON que recibe el cliente apunta al proxy (`SUPPLY_SHIELD_PROXY_BASE_URL`).
2. **Given** el cliente pidiendo el tarball al proxy, **when** el proxy lo descarga del upstream con `stream_upstream`, **then** los bytes servidos son idénticos a los del upstream y el SHA-512 coincide con el `integrity` del packument.
3. **Given** un tarball cuyo hash no coincide, **when** se descarga, **then** la descarga se corta (reusa D1-1) y el cliente no recibe un archivo inválido.

### HU-4 · Convertir la metadata al modelo común

**Como** detector de vulnerabilidades o de typosquatting,
**quiero** recibir un `UpstreamMetadata` con versiones, fechas, scripts y mantenedores,
**para** aplicar mis reglas sin conocer el formato específico de npm.

**Criterios de aceptación**

1. **Given** un packument válido, **when** se convierte, **then** el resultado es un `UpstreamMetadata` con `PackageRef` (ecosistema `npm`), una tupla de `PackageRelease` (versión + `published_at`), `scripts` y `maintainers`.
2. **Given** un packument sin `time` ni `maintainers`, **when** se convierte, **then** los campos opcionales quedan vacíos sin error.

### HU-5 · Instalación real a través del proxy

**Como** desarrollador del equipo,
**quiero** que `npm install`, `npm ci` y `pnpm install` funcionen contra el proxy,
**para** usar el firewall en un proyecto real sin cambiar mi flujo.

**Criterios de aceptación**

1. **Given** un proyecto con `express` + `@types/node` y el `registry` apuntando al proxy, **when** se corre `npm install`, **then** la instalación completa sin errores.
2. **Given** el mismo proyecto con `package-lock.json`, **when** se corre `npm ci`, **then** respeta el `integrity` del lockfile y la instalación completa.

---

## Requisitos funcionales

| ID  | Requisito |
| --- | --------- |
| RF-1 | Exponer `GET /npm/{paquete}` que reenvía la petición a `registry.npmjs.org` preservando el `Accept` (formato completo y `vnd.npm.install-v1+json`). |
| RF-2 | Decodificar nombres con scope (`@scope%2fnombre` → `@scope/nombre`) antes de reenviar al upstream. |
| RF-3 | Reescribir cada `dist.tarball` del packument para que apunte a `{SUPPLY_SHIELD_PROXY_BASE_URL}/npm/...` antes de devolverlo al cliente. |
| RF-4 | Exponer la ruta de tarball que usa `stream_upstream` (D1-1) para descargar del upstream y servir el archivo verificando su SHA-512 contra el `integrity`. |
| RF-5 | Mapear el packument a `UpstreamMetadata` (`PackageRef` con ecosistema `npm`, tupla de `PackageRelease` con `version` y `published_at`, `scripts`, `maintainers`). |
| RF-6 | Devolver el mismo status code del upstream cuando el paquete no existe (404) o hay error, sin inventar respuestas. |
| RF-7 | Añadir `SUPPLY_SHIELD_PROXY_BASE_URL` a `Settings` y documentarla en `.env.example`. |
| RF-8 | Registrar las rutas del adaptador npm en `create_app()` (o un router dedicado) sin acoplarlas a las de PyPI. |

## Requisitos no funcionales

| ID  | Requisito |
| --- | --------- |
| RNF-1 | **Integridad**: los tarballs se sirven sin modificar sus bytes (ADR 0002/0005); el hash se verifica con D1-1. |
| RNF-2 | **Seguridad**: toda salida usa la allowlist de D1-1; los nombres de paquete se validan antes de reenviar. |
| RNF-3 | **Memoria**: los tarballs se streamean; nunca se carga un packument o tarball completo en RAM más de lo necesario. |
| RNF-4 | **Concurrencia**: solo E/S asíncrona; nada bloqueante en corrutinas. |
| RNF-5 | **Tipado**: `mypy --strict` sin errores ni `type: ignore` injustificados; sin `Any`. |
| RNF-6 | **Calidad**: cobertura de la rama nueva, global ≥ 80 %; ruff sin avisos (línea 100). |
| RNF-7 | **Observabilidad**: cada petición y reescritura de tarball queda en `structlog` (nunca `print`), con contexto (paquete, versión, host). |
| RNF-8 | **Testeable sin red**: pruebas unitarias con `respx` en `tests/unit/`; la prueba e2e con `npm` real va en `tests/integration/` con `@pytest.mark.integration`. |
| RNF-9 | **Configuración**: todo sale de `Settings`; cero `os.getenv` dispersos ni valores hardcodeados. |

---

## Orden de ejecución

| Paso | Qué se hace | Requisitos | Estado |
| --- | --- | --- | --- |
| 1 | `Settings`: `SUPPLY_SHIELD_PROXY_BASE_URL` + `.env.example` | RF-7 | ✅ completado |
| 2 | Rutas FastAPI esqueleto + reenvío del packument (formato completo y abreviado) | RF-1, RF-6, RF-8 | ✅ completado |
| 3 | Decodificación de scopes | RF-2 | ✅ completado |
| 4 | Reescritura de `dist.tarball` + ruta de tarball con `stream_upstream` | RF-3, RF-4 | ✅ completado |
| 5 | Mapeo packument → `UpstreamMetadata` | RF-5 | ✅ completado |
| 6 | Tests unitarios con `respx` (metadata, scopes, reescritura, mapeo) | RNF-8 | ✅ completado |
| 7 | Test e2e con `npm install` / `npm ci` / `pnpm install` en `tests/integration/` | HU-5 | ✅ completado |
| 8 | Docs: ADR de la decisión de ruta + `.env.example` | DoD | ✅ completado |

**Precondición:** D1-1 (cliente upstream + `stream_upstream`) y D4-1 (`UpstreamMetadata` y modelos) deben estar disponibles. D1-1 está en `feat/d1-1-upstream-client` (sin mergear); D4-1 está en `main`. Se recomienda bifurcar `feat/back-01-npm-proxy` desde `feat/d1-1-upstream-client`.

**Verificación por paso:** `uv run ruff check` + `uv run ruff format --check` + `uv run mypy` tras cada paso; `make check` completo antes de cerrar el PR.

## Estructura de código

Cada archivo con una responsabilidad; sin espagueti, siguiendo `docs/architecture.md`:

```text
src/supply_shield/proxy/npm/
├── __init__.py
├── routes.py        # RF-1/2/8: rutas FastAPI (packument + tarball)
├── packument.py     # RF-1/3/5: fetch del packument, reescritura de tarball, mapeo a UpstreamMetadata
└── tarball.py       # RF-4: descarga del tarball con stream_upstream (D1-1)

tests/unit/proxy/npm/
├── test_packument.py   # metadata, formatos, scopes, reescritura, mapeo
└── test_tarball.py     # descarga verificada, integridad

tests/integration/proxy/npm/
└── test_npm_install.py # e2e: npm install / npm ci reales contra el proxy
```

- Código, nombres y declaraciones en **inglés**; docstrings y comentarios en **español**; documentación del equipo en **español**.
- `routes.py` no parsea packuments; `packument.py` no sirve HTTP; `tarball.py` no conoce rutas: responsabilidades separadas.

---

## Fuera de alcance

- Bloqueos y políticas (eso es D1-3, semana 4).
- Autenticación por proyecto (D2-1): el proxy npm funciona sin token por ahora.
- Adaptador de PyPI (D2-2).
- Caché de packuments (D3-2 existe, pero usarla es opcional aquí).
- Interfaz web, otros ecosistemas.

## "Listo cuando" (resumen de aceptación de la tarea)

- [x] `npm install`, `npm ci` y `pnpm install` de un proyecto real (express) funcionan a través del proxy.
- [x] `npm ci` respeta el `integrity` del lockfile.
- [x] Tests unitarios con `respx` sin red; `make check` en verde (113 tests, 97 % cobertura).
- [x] Documentación/ADR del área actualizados (ADR 0006, `.env.example`).
- [ ] Aprobación de alguien de otra área.

## Historial

| Fecha | Evento |
| --- | --- |
| 2026-10-29 | Plan creado a partir del template; dependencias D1-1 y D4-1 verificadas como satisfechas. |
