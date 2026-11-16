# D1-1 · Cliente upstream y descarga segura

- **Rama:** `feat/d1-1-upstream-client`
- **Issue:** BACK-01
- **Módulos:** `src/supply_shield/proxy/upstream.py` y `src/supply_shield/proxy/download.py`
- **Tamaño:** M (3 a 5 días) · **Semana:** 1
- **Depende de:** D4-1 (modelos compartidos) → **satisfecha**: D4-1 está en `main` (PR #8) y en
  esta rama; se programa contra los modelos reales de `supply_shield.core`, sin *mock*.
- **Hash de integridad:** SHA-512.
- **ADR del área:** [0005](../adr/0005-shared-upstream-client-with-host-allowlist.md)
- **Estado:** implementado; pendiente de revisión (PR).

## Contexto

Todo lo que el proxy sirva sale por aquí: un cliente `httpx` compartido hacia los registros
upstream (`registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`). Esta pieza es la base
de confianza del sistema: si la descarga no es íntegra, acotada y solo hacia hosts permitidos,
el resto del firewall no tiene sentido.

---

## Historias de usuario

### HU-1 · Descarga íntegra byte a byte

**Como** desarrollador que instala un paquete a través del proxy,
**quiero** que el archivo que llega a mi cliente sea idéntico al del registro upstream,
**para** que los hashes de integridad que verifica npm/pip sigan siendo válidos.

**Criterios de aceptación (Given / When / Then)**

1. **Given** un artefacto upstream válido, **when** el proxy lo reenvía, **then** los bytes
   recibidos por el cliente coinciden byte a byte con los originales (mismo SHA-512).
2. **Given** un artefacto cuyo SHA-512 calculado no coincide con el esperado, **when** se detecta
   la diferencia durante el streaming, **then** la descarga se corta de inmediato y la conexión
   no entrega el resto del archivo.
3. **Given** una interrupción o error upstream, **when** se reintenta, **then** el hash se
   recalcula sobre el flujo completo (nunca se sirve una descarga a medias o mezclada).

### HU-2 · Solo salgo hacia los registros oficiales

**Como** responsable de seguridad,
**quiero** que el cliente solo pueda hablar con la allowlist de hosts upstream,
**para** que una URL maliciosa embebida en un metadato no pueda llevar el proxy a mi red
interna o a servicios cloud (SSRF).

**Criterios de aceptación**

1. **Given** una petición a `registry.npmjs.org`, `pypi.org` o `files.pythonhosted.org`,
   **when** se resuelve el host, **then** la petición sale normalmente.
2. **Given** una petición a cualquier otro host (incluidos IPs privados, `localhost` y
   metadatos cloud), **when** se resuelve el host, **then** se rechaza sin abrir conexión
   alguna hacia ese destino.
3. **Given** una URL con credenciales, redirección o esquema distinto de HTTP(S),
   **when** se evalúa, **then** se rechaza.

### HU-3 · Descargas acotadas

**Como** operador del proxy,
**quiero** un límite máximo de tamaño por respuesta,
**para** que un archivo anormalmente grande no agote memoria, disco ni la banda del equipo.

**Criterios de aceptación**

1. **Given** una respuesta que supera el límite configurado, **when** se está descargando
   en streaming, **then** se aborta antes de superar el límite y no se acumula en memoria.
2. **Given** el límite configurado en `SUPPLY_SHIELD_*`, **when** se cambia por entorno,
   **then** el nuevo valor se aplica sin tocar código.

### HU-4 · Fallos upstream manejados

**Como** usuario ejecutando `npm install`,
**quiero** que el proxy reintente ante fallos transitorios del registro,
**para** que una caída momentánea no rompa mi instalación; pero **sin** esperar infinitamente.

**Criterios de aceptación**

1. **Given** un 5xx o timeout upstream, **when** se reintenta, **then** se aplica backoff
   exponencial con un número máximo de intentos.
2. **Given** agotados los reintentos, **when** se devuelve error, **then** el error es
   tipado y queda registrado en los logs estructurados (sin `print`).
3. **Given** un cliente compartido entre peticiones, **when** hay carga concurrente,
   **then** se reutiliza el pool de conexiones (HTTP/2 incluido).

---

## Requisitos funcionales

| ID  | Requisito |
| --- | --------- |
| RF-1 | Exponer un cliente `httpx.AsyncClient` compartido con pool de conexiones, HTTP/2, timeouts y reintentos con backoff exponencial (máximo de intentos configurable). |
| RF-2 | Restrictir toda salida a la allowlist de hosts upstream: `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`. Cualquier otro destino se rechaza antes de abrir conexión. |
| RF-3 | Validar cada URL de descarga: solo HTTP(S), sin credenciales, sin host fuera de allowlist. |
| RF-4 | Limitar el tamaño máximo de respuesta; al superarlo, abortar la descarga. |
| RF-5 | Reenviar el artefacto al cliente en streaming (byte a byte), calculando su **SHA-512** en el flujo y comparándolo con el hash esperado. |
| RF-6 | Si el hash no coincide: cortar la descarga de inmediato, no entregar el resto del archivo y registrar el evento. |
| RF-7 | Exponer una interfaz compatible con los modelos compartidos D4-1 (`supply_shield.core`: `PackageRef`, `PackageRelease`, `UpstreamMetadata`). D4-1 ya está en `main`; no se programa contra *mock*. |
| RF-8 | Parametrizar hosts permitidos, límite de tamaño, timeouts y reintentos desde `Settings` (`SUPPLY_SHIELD_*`) y documentarlos en `.env.example`. |

## Requisitos no funcionales

| ID  | Requisito |
| --- | --------- |
| RNF-1 | **Integridad**: el proxy nunca modifica los bytes de un artefacto (regla del repo, ADR 0002). |
| RNF-2 | **Seguridad**: sin SSRF; sin fugas de secretos en logs; bandit y gitleaks en verde. |
| RNF-3 | **Memoria**: streaming obligatorio; nunca cargar un artefacto completo en RAM. |
| RNF-4 | **Concurrencia**: solo E/S asíncrona (`httpx.AsyncClient`); nada bloqueante en corrutinas. |
| RNF-5 | **Tipado**: `mypy --strict` sin errores ni `type: ignore` injustificados; sin `Any`. |
| RNF-6 | **Calidad**: cobertura de la rama nueva, global ≥ 80 %; ruff sin avisos (línea 100). |
| RNF-7 | **Observabilidad**: errores y rechazos visibles vía `structlog` (nunca `print`), con contexto (host, motivo del rechazo, hash). |
| RNF-8 | **Testeable sin red**: pruebas unitarias con `respx` en `tests/unit/`; cobertura de los 4 criterios de "Listo cuando". |
| RNF-9 | **Configuración**: todo sale de `Settings`; cero `os.getenv` dispersos ni valores hardcodeados. |

---

## Orden de ejecución

| Paso | Qué se hace | Requisitos | Estado |
| --- | --- | --- | --- |
| 1 | `Settings` del upstream: hosts permitidos, límite de tamaño, timeouts, reintentos + `.env.example` | RF-8 | ✅ |
| 2 | Cliente `httpx` compartido: pool, HTTP/2, timeouts, backoff y allowlist de hosts | RF-1, RF-2, RF-3 | ✅ |
| 3 | Helper de descarga en streaming con SHA-512 y corte por hash | RF-5, RF-6 | ✅ |
| 4 | Límite de tamaño máximo de respuesta | RF-4 | ✅ |
| 5 | Tests unitarios con `respx` que cubren los 4 "Listo cuando" | RNF-8 | ✅ |
| 6 | Docs: `.env.example` completado y ADR 0004 del área | DoD | ✅ |

**Precondición (D4-1) — satisfecha:** los modelos compartidos ya están en `main`
(PR #8, merge `fb4fc6b`) y en esta rama: `core/package_ref.py`, `core/package_release.py`,
`core/upstream_metadata.py`, `core/enums.py` (`Ecosystem`, `Severity`, `DecisionAction`) y la
caché en memoria (`cache/`). El paso 1 **extiende** el `Settings` que D4-1 ya dejó en
`config.py` (campos `cache_*`), sin tocar esos campos.

**Verificación por paso:** `uv run ruff check` + `uv run ruff format --check` + `uv run mypy`
tras cada paso; `make check` completo antes de cerrar el PR.

## Estructura de código

Cada archivo con una responsabilidad; sin espagueti, siguiendo `docs/architecture.md`:

```text
src/supply_shield/
├── config.py              # paso 1: añadir Settings del upstream (conservar los cache_* de D4-1)
├── core/                  # D4-1: modelos compartidos (PackageRef, enums...) — solo se usan
└── proxy/
    ├── upstream.py        # RF-1/2/3: cliente compartido + allowlist (sin streaming)
    ├── download.py        # RF-4/5/6: streaming + SHA-512 + límite (sin allowlist)
    ├── npm/               # adaptador npm (otra tarea, fuera de alcance)
    └── pypi/              # adaptador PyPI (otra tarea, fuera de alcance)

tests/unit/proxy/
├── test_upstream_client.py  # allowlist, timeouts, reintentos
└── test_download.py         # integridad, corte por hash, corte por tamaño
```

- Código, nombres y docstrings en **inglés**; documentación del equipo en **español**.
- `upstream.py` no calcula hashes; `download.py` no valida hosts: responsabilidades separadas.

---

## Fuera de alcance

- Adaptadores npm/PyPI (protocolo del registro).
- Políticas de bloqueo, analizadores y caché.
- Interfaz web, otros ecosistemas, licencias, análisis de código.

## "Listo cuando" (resumen de aceptación de la tarea)

- [x] Un archivo servido por el proxy es idéntico al original.
- [x] Si el hash no coincide, la descarga se corta.
- [x] Una respuesta que pasa el límite de tamaño se corta.
- [x] Una petición a un host no permitido se rechaza.
- [x] Tests unitarios con `respx` sin red; `make check` en verde.
- [x] Documentación/ADR del área actualizados.
- [ ] Aprobación de alguien de otra área.

## Historial

| Fecha | Evento |
| --- | --- |
| 2026-10-26 | Historias, RF/RNF, orden de ejecución y estructura de código creados; ADR 0004 redactado. |
| 2026-10-26 | D4-1 mergeado a `main` (PR #8): precondición satisfecha, sin *mock*; SHA-512 fijado como algoritmo de integridad. |
| 2026-10-27 | Implementación completa (pasos 1-6): `config.py`, `proxy/upstream.py`, `proxy/download.py` + tests; `make check` en verde (cobertura 96.7 %). |
