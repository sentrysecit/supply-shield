# Plan de Pruebas · D1-2 · Proxy de npm

**Historia:** D1-2 — Proxy de npm  
**Rama:** `feat/back-01-npm-proxy`  
**Fecha:** 2026-11-10  
**Autora:** Liceth Usma  
**Estado del plan:** activo

---

## Alcance

Este plan cubre únicamente las **brechas de calidad** identificadas sobre la implementación ya entregada de D1-2. Los tests existentes (`test_packument.py`, `test_tarball.py`, `test_metadata.py`, `test_npm_install.py`) y el `make check` en verde (113 tests, 97 %) son la línea base. Este documento no reemplaza esos tests: los complementa.

### Funcionalidad en alcance

| Módulo | Archivo | Estado base |
|--------|---------|-------------|
| Rutas FastAPI (packument + tarball) | `proxy/npm/routes.py` | Implementado |
| Fetch y reescritura de packument | `proxy/npm/packument.py` | Implementado |
| Descarga verificada de tarballs | `proxy/npm/tarball.py` | Implementado |
| Mapeo a `UpstreamMetadata` | `proxy/npm/packument.py` | Implementado |
| Configuración `proxy_base_url` | `config.py` | Implementado |

### Fuera de alcance

- Bloqueos y políticas (D1-3).
- Adaptador PyPI (D2-2).
- Caché de packuments (D3-2).
- Escenarios de autenticación (D2-1).

---

## Criterios de entrada / salida

### Entrada

- [x] Implementación code-complete en rama `feat/back-01-npm-proxy`.
- [x] `make check` verde (lint + typecheck + security + cobertura ≥ 80 %).
- [x] Tests de integración (`npm install`, `npm ci`, `pnpm install`) ejecutables localmente.

### Salida

- [ ] Cobertura de las brechas listadas ≥ 80 % en las líneas afectadas.
- [ ] Cero hallazgos P0/P1 abiertos.
- [ ] `make check` verde con los tests nuevos.
- [ ] Ninguna fila "GAP" en la matriz de cobertura sin nota de aceptación de riesgo.

---

## Descomposición de escenarios faltantes

### Área 1 · Verificación de integridad en la ruta npm (HU-3 criterio 3)

**Requisito:** cuando el hash del tarball no coincide con el `integrity` del packument, la descarga se corta y el cliente no recibe un archivo inválido.

La capa D1-1 (`stream_upstream`) maneja la corrupción, pero **ningún test existente a nivel npm verifica que esa excepción se traduzca en un corte de stream visible para el cliente**.

| # | Escenario | Categoría | P/F |
|---|-----------|-----------|-----|
| S1 | Tarball con bytes alterados → `StreamingResponse` se corta antes de enviar todos los bytes | Error / integridad | — |
| S2 | El cliente recibe menos bytes que el tamaño declarado por el upstream | Error | — |

### Área 2 · Allowlist de hosts en rutas de tarball (RNF-2)

**Requisito:** "toda salida usa la allowlist de D1-1". Los tests de allowlist existentes solo cubren la ruta de packument. Las rutas `get_tarball` y `get_scoped_tarball` tienen el mismo código de escape pero **ningún test verifica que un host no permitido produzca 403 en la ruta de tarball**.

| # | Escenario | Categoría | P/F |
|---|-----------|-----------|-----|
| S3 | Host no en allowlist → `get_tarball` devuelve 403 | Seguridad | — |
| S4 | Host no en allowlist → `get_scoped_tarball` devuelve 403 | Seguridad | — |

### Área 3 · Validación del nombre de paquete (RNF-2)

**Requisito:** "los nombres de paquete se validan antes de reenviar". La implementación actual solo hace `unquote()`, sin rechazar caracteres adversariales.

| # | Escenario | Categoría | P/F |
|---|-----------|-----------|-----|
| S5 | Nombre con `../` → proxy devuelve 400 sin reenviar al upstream | Seguridad / path traversal | — |
| S6 | Nombre con null byte (`%00`) → proxy devuelve 400 | Seguridad | — |
| S7 | Nombre válido con caracteres Unicode permitidos por npm (`ñ`, acentos) → reenvía correctamente | Edge case | — |

### Área 4 · Test e2e con `@types/node` (HU-5 criterio 1)

**Requisito:** "npm install de un proyecto real con express + @types/node". El test actual solo usa `express`. Un paquete scoped en la misma instalación ejercita el flujo `GET /npm/@types/node` y el tarball scoped de forma integrada.

| # | Escenario | Categoría | P/F |
|---|-----------|-----------|-----|
| S8 | `npm install` de `express` + `@types/node` completa sin errores a través del proxy | E2E / happy path | — |

### Área 5 · Observabilidad: logs emitidos con contexto correcto (RNF-7)

**Requisito:** "cada petición y reescritura de tarball queda en structlog con contexto (paquete, versión, host)". Los logs fueron añadidos en esta corrección pero **ningún test unitario verifica que se emitan con los campos esperados**.

| # | Escenario | Categoría | P/F |
|---|-----------|-----------|-----|
| S9 | Petición de packument → se emite evento `npm.packument.request` con campo `package` | Observabilidad | — |
| S10 | Reescritura de tarballs → se emite evento `npm.packument.tarballs_rewritten` con campo `count` | Observabilidad | — |
| S11 | Tarball resuelto → se emite evento `npm.tarball.resolved` con campos `package`, `filename`, `url` | Observabilidad | — |

### Área 6 · Concurrencia (RNF-4)

**Requisito:** "solo E/S asíncrona; nada bloqueante en corrutinas". No hay test que ejerza múltiples peticiones simultáneas contra el proxy.

| # | Escenario | Categoría | P/F |
|---|-----------|-----------|-----|
| S12 | 10 peticiones concurrentes al mismo packument → todas responden 200 sin deadlock | Concurrencia | — |
| S13 | Descargas simultáneas de tarballs distintos → bytes correctos en cada respuesta | Concurrencia | — |

---

## Matriz de cobertura (requisitos → tests)

| Req | Descripción | Tests existentes | Tests nuevos | Estado |
|-----|-------------|-----------------|--------------|--------|
| RF-1 | Packument en formato completo y abreviado | `test_packument.py:49,57` | — | Cubierto |
| RF-2 | Decodificación de scopes | `test_packument.py:84` | — | Cubierto |
| RF-3 | Reescritura de `dist.tarball` | `test_tarball.py:47,64,93` | — | Cubierto |
| RF-4 | Tarball con `stream_upstream` + SHA-512 | `test_tarball.py:106,132` | S1, S2 | Parcial |
| RF-5 | Mapeo a `UpstreamMetadata` | `test_metadata.py` (6 tests) | — | Cubierto |
| RF-6 | Status code del upstream propagado | `test_packument.py:75` | — | Cubierto |
| RF-7 | `proxy_base_url` en Settings | `config.py` (mypy) | — | Cubierto |
| RF-8 | Router registrado sin acoplar a PyPI | `test_packument.py:49` | — | Cubierto |
| RNF-1 | Bytes del tarball intactos | `test_tarball.py:106` | S1, S2 | Parcial |
| RNF-2 | Allowlist de hosts | `test_packument.py:94,103` | S3, S4, S5, S6 | Parcial |
| RNF-3 | Streaming (sin cargar en RAM) | — | S12, S13 | GAP ¹ |
| RNF-4 | Solo E/S asíncrona | — | S12, S13 | Parcial |
| RNF-5 | mypy strict sin errores | make check | — | Cubierto |
| RNF-6 | Cobertura ≥ 80 %; ruff sin avisos | make check | — | Cubierto |
| RNF-7 | Logs structlog con contexto | — | S9, S10, S11 | GAP |
| RNF-8 | Tests unitarios con respx; e2e con `@pytest.mark.integration` | Ambas carpetas | S8 | Parcial |
| RNF-9 | Config solo de Settings | config.py | — | Cubierto |
| HU-5 | npm install + npm ci + pnpm install | `test_npm_install.py` (3 tests) | S8 | Parcial |

> ¹ RNF-3 (memoria): aceptamos el riesgo de no automatizar un test de memoria en este sprint; verificación manual con `memory_profiler` si se detecta regresión de rendimiento.

---

## Priorización (Riesgo × Esfuerzo)

| Escenario | Riesgo | Esfuerzo | Prioridad | Decisión |
|-----------|--------|----------|-----------|----------|
| S3, S4 · Allowlist en tarball routes | ALTO | Bajo (< 0.5 h) | DO FIRST | Escribir ahora |
| S5, S6 · Validación de nombre adversarial | ALTO | Medio (1 h) | DO SECOND | Escribir ahora |
| S1, S2 · Hash mismatch en ruta npm | ALTO | Medio (1 h) | DO SECOND | Escribir ahora |
| S8 · e2e express + @types/node | MEDIO | Bajo (0.5 h) | DO SECOND | Escribir ahora |
| S9–S11 · Verificar logs emitidos | MEDIO | Bajo (0.5 h) | DO SECOND | Escribir ahora |
| S7 · Unicode en nombre de paquete | MEDIO | Bajo (0.3 h) | DO SECOND | Escribir ahora |
| S12, S13 · Concurrencia | MEDIO | Alto (3 h) | DO THIRD | Próximo sprint |
| RNF-3 · Memory profiling | BAJO | Alto (4 h) | DEFER | Aceptar riesgo |

---

## Estimación de esfuerzo

```
Nuevos tests unitarios:
  S1–S2 (hash mismatch)      2 tests × 1.0 h = 2.0 h
  S3–S4 (allowlist tarball)  2 tests × 0.3 h = 0.6 h
  S5–S7 (validación nombre)  3 tests × 0.4 h = 1.2 h
  S9–S11 (logs structlog)    3 tests × 0.3 h = 0.9 h

Nuevos tests de integración:
  S8 (e2e @types/node)       1 test  × 0.5 h = 0.5 h

Setup de fixtures adicionales:          0.5 h
Revisión post-escritura (CI + mypy):    0.5 h
Buffer (20 %) de verificación:          1.2 h

Total estimado:                          7.4 h
Capacidad disponible (sprint):          10.0 h
Utilización:                            74 %  ✓
Buffer explícito:                        2.6 h (26 %)
```

**Diferidos (próximo sprint):**
  S12–S13 (concurrencia)     2 tests × 3.0 h = 6.0 h

---

## Detalle de los tests a escribir

### Unit · `tests/unit/proxy/npm/test_tarball.py` — Hash mismatch (S1, S2)

```python
@respx.mock
async def test_get_tarball_aborts_on_hash_mismatch(client):
    """El stream se corta si los bytes del upstream no coinciden con el integrity."""
    corrupted = b"bytes-alterados"
    # integrity declarada apunta a TARBALL_BYTES, pero upstream devuelve corrupted
    respx.get("https://registry.npmjs.org/express").respond(200, json=PACKUMENT)
    respx.get("https://registry.npmjs.org/express/-/express-4.18.2.tgz").respond(
        200, content=corrupted
    )
    response = await client.get("/npm/express/-/express-4.18.2.tgz")
    # Stream cortado → cliente no recibe los bytes válidos completos
    assert response.content != TARBALL_BYTES
    # El status puede ser 200 (stream ya inició) o 502; en ambos casos los datos son inválidos
```

> **Nota de implementación:** `stream_upstream` lanza `UpstreamIntegrityError` tras acumular todos los bytes y comparar el hash; el `StreamingResponse` ya se inició, así que el cliente verá la conexión cortada o bytes incompletos. El test debe verificar que `response.content != TARBALL_BYTES` y que la longitud no coincide con el tamaño esperado.

### Unit · `tests/unit/proxy/npm/test_packument.py` — Allowlist en tarball routes (S3, S4)

```python
@respx.mock
async def test_get_tarball_returns_502_on_disallowed_host():
    settings = Settings(_env_file=None, upstream_hosts=("pypi.org",))
    _, test_client = _app_with_settings(settings)
    async with test_client:
        response = await test_client.get("/npm/express/-/express-1.0.0.tgz")
    assert response.status_code in (403, 502)
    assert not respx.calls
```

### Unit · `tests/unit/proxy/npm/test_routes_validation.py` — Validación de nombre (S5, S6, S7)

Crear archivo nuevo. La función `decode_package_name` actualmente solo llama `unquote()`. Para cubrir S5–S6, primero hay que añadir validación en `packument.py`:

```python
# packument.py — nueva función
_INVALID_NAME_PATTERN = re.compile(r"[/\\.\x00].*\.\.")


def validate_package_name(name: str) -> None:
    """Lanza ValueError si el nombre contiene secuencias peligrosas."""
    if ".." in name or "\x00" in name:
        raise ValueError(f"invalid package name: {name!r}")
```

Y tests:

```python
def test_validate_rejects_path_traversal():
    with pytest.raises(ValueError):
        validate_package_name("../etc/passwd")


def test_validate_rejects_null_byte():
    with pytest.raises(ValueError):
        validate_package_name("express\x00evil")


def test_validate_accepts_unicode_name():
    validate_package_name("ñoño-pkg")  # nombre Unicode válido
```

> **Nota:** `validate_package_name` debe llamarse desde `_serve_packument` y `get_tarball`/`get_scoped_tarball` antes de reenviar, devolviendo 400 si lanza `ValueError`.

### Unit · `tests/unit/proxy/npm/test_logging.py` — Logs con contexto (S9–S11)

```python
import structlog.testing

def test_packument_request_emits_log():
    with structlog.testing.capture_logs() as logs:
        # ... llamar a _serve_packument con respx mockeado
    assert any(e["event"] == "npm.packument.request" and "package" in e for e in logs)

def test_tarballs_rewrite_emits_log():
    with structlog.testing.capture_logs() as logs:
        rewrite_tarballs({"name": "pkg", "versions": {"1.0.0": {"dist": {"tarball": "https://..."}}}}, "http://proxy")
    assert any(e["event"] == "npm.packument.tarballs_rewritten" and e.get("count") == 1 for e in logs)
```

### Integration · `tests/integration/proxy/npm/test_npm_install.py` — Express + @types/node (S8)

```python
def _write_package_json_scoped(project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / "package.json").write_text(
        '{"name":"supply-shield-e2e","version":"1.0.0","dependencies":{"express":"4.18.2","@types/node":"18.19.0"}}'
    )


def test_npm_install_scoped_package_through_proxy(proxy_url, tmp_path):
    """npm install de express + @types/node (scoped) funciona a través del proxy."""
    project = tmp_path / "project"
    _write_package_json_scoped(project)
    registry = f"{proxy_url}/npm/"
    result = _run_npm(f'install --registry "{registry}" --no-audit --no-fund', project)
    assert result.returncode == 0, result.stderr.decode()
    assert (project / "node_modules" / "express").exists()
    assert (project / "node_modules" / "@types" / "node").exists()
```

---

## Riesgos del plan

| Riesgo | Mitigación |
|--------|-----------|
| `stream_upstream` lanza la excepción después de iniciar el `StreamingResponse` → el test S1/S2 no puede verificar HTTP status | Verificar bytes recibidos vs bytes esperados; aceptar que el status sea 200 con datos incompletos |
| La validación de nombres (S5–S6) requiere modificar código de producción, no solo tests | Implementar `validate_package_name` como parte de esta corrección antes de escribir los tests |
| `structlog.testing.capture_logs` puede no capturar logs de código async en algunas versiones | Verificar con la versión instalada; alternativa: mock del logger |
| `@types/node` v18.19.0 tiene cientos de dependencias transitivas → test S8 puede ser lento | Poner `timeout=300` en el test; marcar como `@pytest.mark.slow` si supera 60 s |

---

## Seguimiento diario

| Día | Actividad | Estado |
|-----|-----------|--------|
| 1 | S3–S4 (allowlist tarball) + S9–S11 (logs) | ⬜ pendiente |
| 1 | Implementar `validate_package_name` + S5–S7 | ⬜ pendiente |
| 2 | S1–S2 (hash mismatch) + S8 (e2e @types/node) | ⬜ pendiente |
| 2 | `make check` verde con todos los tests nuevos | ⬜ pendiente |
| 3 | Buffer: verificación de bugs, re-test | ⬜ pendiente |

---

## Historial

| Fecha | Evento |
|-------|--------|
| 2026-11-10 | Plan creado a partir de la verificación de D1-2 contra sus requisitos. Dos brechas corregidas (RNF-7 structlog, HU-5 pnpm). Ocho escenarios adicionales priorizados. |
