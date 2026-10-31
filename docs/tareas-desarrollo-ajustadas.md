# Supply-Shield: tareas del MVP por desarrollador

Equipo de 5 personas, **7 semanas** de desarrollo. 

## Qué hace el MVP al terminar

- Funciona como proxy de **npm** y **PyPI** para `npm`, `pnpm`, `pip` y `uv`.
- Bloquea:
  - paquetes con vulnerabilidades críticas;
  - malware conocido;
  - typosquatting;
  - versiones recién publicadas;
  - dependency confusion.
- Las políticas se configuran con tres modos: bloquear, advertir o solo registrar.
- Genera el SBOM (CycloneDX) de cada proyecto y lo vuelve a revisar cuando sale una vulnerabilidad nueva.
- Se instala con `docker compose`, trae métricas y deja resultados medibles para el documento.

**No incluye:** interfaz web, otros ecosistemas (Maven, Go), análisis del código de los paquetes ni políticas de licencias.

## Reglas para todo el equipo

- **Todos trabajan en el core de seguridad.** Cada persona tiene al menos una tarea de detección o de bloqueo.
- **No hay un responsable único de calidad.** Cada uno escribe los tests de su tarea, documenta su parte y escribe el ADR (registro de decisión) de su área en la semana 1.
- **Cada PR lo revisa alguien de otra área**, para que todos conozcan el sistema completo.
- **Una tarea está lista** cuando cumple todos sus puntos de "Listo cuando", tiene tests, la CI está en verde y alguien la aprobó.
- **Las piezas se conectan por los modelos compartidos (D3-1).** Si una tarea necesita algo que todavía no existe, se programa contra esa interfaz con un *mock* y no se espera a la otra persona.
- **La base de datos entra en la semana 4** (D5-5). Antes de eso se usan archivos YAML y caché en memoria.
- **La semana 7 no tiene tareas nuevas:** es para corregir lo que encuentre el pentest, estabilizar y preparar la demo.

**Cómo leer cada tarea**
- **Semana:** en qué semana se trabaja.
- **Tamaño:** S = hasta 2 días, M = 3 a 5 días, L = 1 a 2 semanas.
- **Depende de:** qué tarea debe estar lista (o al menos su interfaz) para empezar.
- **Issue:** el issue actual de GitHub que cubre, si existe.

---

## Dev 1 · Proxy npm y protección del proxy

#### D1-1 · Cliente upstream y descarga segura
**Semana:** 1 · **Tamaño:** M · **Depende de:** D4-1 · **Issue:** BACK-01 · **Código:** `proxy/upstream.py`

**Qué hacer**
- Crear un cliente `httpx` compartido con pool de conexiones, HTTP/2, timeouts y reintentos con backoff.
- Permitir solo los hosts upstream configurados (`registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`).
- Limitar el tamaño máximo de respuesta.
- Crear un helper que reenvíe el archivo al cliente byte a byte mientras calcula su hash, y lo compare con el hash esperado.

**Listo cuando**
- Un archivo servido por el proxy es idéntico al original.
- Si el hash no coincide, la descarga se corta.
- Una respuesta que pasa el límite de tamaño se corta.
- Una petición a un host no permitido se rechaza.

#### D1-2 · Proxy de npm
**Semana:** 2–3 · **Tamaño:** L · **Depende de:** D1-1 · **Issue:** BACK-01 · **Código:** `proxy/npm/`

**Qué hacer**
- Servir la metadata del paquete en sus dos formatos: completa y abreviada (`Accept: application/vnd.npm.install-v1+json`).
- Soportar paquetes con scope (`@scope/nombre`, que llega como `@scope%2fnombre`).
- Reescribir `dist.tarball` para que apunte al proxy, y descargar los tarballs con D1-1.
- Convertir la metadata al modelo común `UpstreamMetadata` (versiones, fechas de publicación, scripts y mantenedores), que usarán los detectores.

**Listo cuando**
- `npm install`, `npm ci` y `pnpm install` de un proyecto real (p. ej. express + @types/node) funcionan a través del proxy.
- `npm ci` respeta el `integrity` del lockfile.

#### D1-3 · Aplicar los bloqueos en el proxy
**Semana:** 4 · **Tamaño:** M · **Depende de:** D1-2, D2-2, D3-6 · **Issue:** BACK-01 · **Código:** `proxy/enforcement.py`

**Qué hacer**
- Antes de devolver la metadata, pedir una decisión al motor de evaluación (D3-6) para cada versión.
- Quitar de la metadata las versiones bloqueadas. En npm, si `latest` queda bloqueada, apuntarla a la versión permitida más reciente.
- Si alguien pide directamente el archivo de una versión bloqueada, responder **403** con un mensaje legible: regla, motivo, ID del advisory y cómo pedir una excepción.
- Agregar el encabezado `X-Supply-Shield-Decision` a todas las respuestas.
- Funciona igual para npm y para PyPI.

**Listo cuando**
- Al pedir un rango que incluye una versión vulnerable, npm y pip eligen solos una versión segura.
- Al forzar una versión bloqueada, el usuario ve el motivo en la terminal.
- En modo "advertir", el paquete se instala y la advertencia queda en el log.

#### D1-4 · Protección del proxy y modelo de amenazas
**Semana:** 5 · **Tamaño:** M · **Depende de:** D1-2, D2-2 · **Issue:** nuevo · **Código:** `proxy/validation.py`, `docs/threat-model.md`

**Qué hacer**
- Validar los nombres de paquete con una regex por ecosistema.
- Bloquear path traversal, incluso codificado (`../`, `%2e%2e`, `%2f`).
- Evitar SSRF (usando la lista de hosts de D1-1).
- Poner límites de tamaño y tiempo en todas las rutas.
- Aplicar rate limit por token.
- Agregar encabezados de seguridad.
- Escribir el modelo de amenazas del proxy (STRIDE).

**Listo cuando**
- Hay al menos un test negativo por cada vector.
- `bandit` no reporta hallazgos.
- El modelo de amenazas está revisado por Dev 5, que lo usa como guía del pentest.

#### D1-5 · Medición de latencia
**Semana:** 6 · **Tamaño:** M · **Depende de:** D1-3, D5-7 · **Issue:** nuevo (valida INV12) · **Código:** `bench/`, `docs/results/latencia.md`

**Qué hacer**
- Escribir escenarios en k6 o Locust que comparen la instalación a través del proxy contra la instalación directa.
- Medir con caché fría y con caché caliente, subiendo la concurrencia.
- Documentar el entorno de prueba (máquina, red, versiones).

**Listo cuando**
- Hay una tabla con p50, p95 y p99 en `docs/results/`.
- Se reproduce con `make bench`.

---

## Dev 2 · Proxy PyPI, acceso por proyecto y ataque controlado

#### D2-1 · Autenticación por proyecto
**Semana:** 1 · **Tamaño:** S · **Depende de:** D3-1 · **Issue:** nuevo · **Código:** `api/auth.py`, `config/projects.yaml`

**Qué hacer**
- Definir los proyectos y sus tokens en YAML. Los tokens se guardan como hash, nunca en texto plano.
- Aceptar el token por Basic o Bearer auth, que es como lo envían npm (`.npmrc`) y pip (`pip.conf`).
- Llenar el `RequestContext` con el proyecto y, si viene el encabezado `X-Build-Id`, con el build.
- En la semana 4, al llegar la base de datos (D5-5), mover los tokens allí.

**Listo cuando**
- Sin token, o con uno inválido, se responde 401.
- El token nunca aparece en logs.
- Los ejemplos de `.npmrc` y `pip.conf` funcionan.

#### D2-2 · Proxy de PyPI
**Semana:** 2–3 · **Tamaño:** L · **Depende de:** D1-1 · **Issue:** BACK-01 · **Código:** `proxy/pypi/`

**Qué hacer**
- Servir el índice simple en HTML (PEP 503) y en JSON (PEP 691), eligiendo el formato según el encabezado `Accept`.
- Servir la metadata PEP 658 y respetar las versiones *yanked*.
- Normalizar los nombres (`Django` = `django`, `my_pkg` = `my-pkg`).
- Reescribir las URLs de los archivos para que apunten al proxy, conservando el `#sha256=`.
- Convertir la metadata a `UpstreamMetadata`, tomando las fechas de publicación de la JSON API de PyPI.

**Listo cuando**
- `pip install`, `pip install --require-hashes` y `uv pip install` funcionan a través del proxy, tanto con wheels como con sdists.

#### D2-3 · Entorno de ataque controlado con paquetes cebo
**Semana:** 4–5 · **Tamaño:** L · **Depende de:** D1-3 · **Issue:** SEC-03 · **Código:** `tests/e2e/`, `docker-compose.e2e.yml`

**Qué hacer**
- Montar un `docker compose` de pruebas con el proxy, **Verdaccio** (npm local) y **pypiserver** (PyPI local).
- Publicar solo en esos registros locales estos paquetes cebo:
  - un typosquat de un paquete popular;
  - un paquete con `postinstall` sospechoso;
  - un nombre interno de la "empresa" (dependency confusion);
  - una versión recién publicada.
- Crear un job de CI que instale con `npm`, `pnpm`, `pip` y `uv`, y verifique qué se bloquea y qué pasa.
- **Nunca publicar cebos en npm ni en PyPI reales**, porque viola sus términos de uso.

**Listo cuando**
- El job de e2e está en verde en CI.
- Cada regla de bloqueo tiene al menos un caso que se bloquea y otro que pasa.

#### D2-4 · SBOM de lo descargado por cada build
**Semana:** 5 · **Tamaño:** S · **Depende de:** D1-3, D2-1, D5-3 · **Issue:** BACK-04 · **Código:** `sbom/observed.py`

**Qué hacer**
- Registrar cada archivo que sirve el proxy, asociado a su proyecto y build.
- Generar con D5-3 el SBOM "observado" de ese build.
- Exponerlo en `GET /api/v1/builds/{id}/sbom`.

**Listo cuando**
- Después de un `npm ci` contra el proxy con `X-Build-Id`, el endpoint devuelve todos los paquetes que se descargaron.

#### D2-5 · Guía de clientes y ejemplo de CI
**Semana:** 6 · **Tamaño:** S · **Depende de:** D1-3, D5-6 · **Issue:** nuevo · **Código:** `docs/clientes.md`, `examples/github-actions.yml`

**Qué hacer**
- Documentar cómo configurar `npm`, `pnpm`, `yarn`, `pip`, `uv` y `poetry` contra el proxy.
- Crear un workflow de GitHub Actions de ejemplo que instale a través del proxy y corra `supply-shield scan`.

**Listo cuando**
- Alguien del equipo que no participó en la tarea la sigue desde cero y le funciona.

#### D2-6 · Comparación con herramientas existentes
**Semana:** 6–7 · **Tamaño:** S · **Depende de:** D5-6 · **Issue:** nuevo · **Código:** `docs/results/comparacion.md`

**Qué hacer**
- Elegir de 3 a 5 proyectos de prueba con dependencias vulnerables conocidas.
- Correr sobre ellos `npm audit`, `pip-audit`, OSV-Scanner y Supply-Shield.
- Comparar qué detecta cada uno y en qué momento: antes de instalar o después.

**Listo cuando**
- La tabla comparativa está en `docs/results/` con los comandos para reproducirla.

---

## Dev 3 · Vulnerabilidades y motor de evaluación

#### D3-1 · Modelos compartidos
**Semana:** 1 · **Tamaño:** S · **Depende de:** D4-1 (puede empezar en paralelo) · **Issue:** BACK-06 · **Código:** `core/models.py`

**Qué hacer**
- Crear los modelos Pydantic y los protocolos que usa todo el equipo:
  - `PackageRef`: ecosistema, nombre normalizado, versión y `purl`;
  - `UpstreamMetadata`: versiones, fechas, scripts y mantenedores;
  - `RequestContext`: proyecto, build y cliente;
  - `Finding`: regla, severidad, fuente, detalle y referencias;
  - `Analyzer`: protocolo con `async analyze(pkg, ctx) -> list[Finding]`;
  - `PolicyDecision`: permitir, advertir o bloquear, con sus razones.
- **Es lo primero, porque desbloquea a todos.**

**Listo cuando**
- Los 5 aprobaron el PR.
- Hay tests de normalización de nombres y de construcción de `purl`.

#### D3-2 · Caché en memoria
**Semana:** 1 · **Tamaño:** S · **Depende de:** D3-1 · **Issue:** BACK-05 · **Código:** `cache/`

**Qué hacer**
- Crear una interfaz `Cache` con una implementación en memoria (LRU con tiempo de vida).
- Usar tiempos de vida distintos por tipo de dato: metadata (minutos), hallazgos (horas) y decisiones (minutos).
- Permitir invalidar por paquete o por advisory.

**Listo cuando**
- Hay tests de expiración e invalidación.
- D5-7 puede cambiarla por Redis sin tocar el código que la usa.

#### D3-3 · Detector de vulnerabilidades (OSV.dev)
**Semana:** 2 · **Tamaño:** M · **Depende de:** D3-1 · **Issue:** BACK-02 · **Código:** `analyzers/vulnerabilities/`

**Qué hacer**
- Consultar OSV.dev con `querybatch` y obtener el detalle con `/v1/vulns/{id}`, con timeouts y backoff.
- Calcular la severidad desde el vector CVSS v3.1 o v4. Si no hay CVSS, usar la severidad del ecosistema (GHSA), y si tampoco hay, marcarla como `UNKNOWN`.
- Incluir los aliases (CVE y GHSA) y la versión que corrige el problema.

**Listo cuando**
- Detecta vulnerabilidades reales conocidas (p. ej. `lodash@4.17.20`, `requests@2.19.0`), probado con respuestas grabadas.
- Si OSV no responde, se lanza un error controlado, no un 500.

#### D3-4 · Detector de malware conocido
**Semana:** 2 · **Tamaño:** S · **Depende de:** D3-3 · **Issue:** nuevo · **Código:** `analyzers/vulnerabilities/malware.py`

**Qué hacer**
- Tomar los advisories `MAL-` de OSV y generar un hallazgo con la regla `known_malware` y severidad crítica, sin importar el CVSS.

**Listo cuando**
- Detecta un paquete malicioso real de npm y otro de PyPI que estén en OSV.

#### D3-5 · Qué hacer si un detector falla
**Semana:** 3 · **Tamaño:** S · **Depende de:** D4-3 · **Issue:** SEC-06 · **Código:** `policy/failure_modes.py`

**Qué hacer**
- Definir por regla qué pasa cuando su detector falla o tarda demasiado:
  - **bloquear** (*fail-closed*): malware y dependency confusion;
  - **dejar pasar con alerta** (*fail-open*): vulnerabilidades y typosquatting.
- Hacerlo configurable en la política.
- Marcar la decisión como "tomada en modo degradado".

**Listo cuando**
- Simulando la caída de OSV, cada regla se comporta según lo definido.

#### D3-6 · Motor de evaluación
**Semana:** 3–4 · **Tamaño:** M · **Depende de:** D3-3, D4-3, D5-2 (usa mocks del typosquatting hasta que llegue D4-4) · **Issue:** nuevo · **Código:** `core/evaluator.py`

**Qué hacer**
- Crear `evaluate_package(pkg, ctx)`, que:
  - corre todos los detectores en paralelo, cada uno con su timeout;
  - aplica la política (D4-3) y las reglas de falla (D3-5);
  - guarda el resultado en la caché (D3-2).
- Los detectores se registran por inyección de dependencias, así cada dev agrega el suyo sin tocar este código.
- En la semana 4, conectar el detector real de typosquatting.

**Listo cuando**
- Con caché caliente, la evaluación tarda menos de 10 ms.
- Si un detector falla, los demás siguen.
- Hay un test de integración con todos los detectores conectados.

#### D3-7 · Logs, métricas y auditoría
**Semana:** 4 · **Tamaño:** M · **Depende de:** D4-1 · **Issue:** nuevo · **Código:** `logging.py`, `api/metrics.py`

**Qué hacer**
- Configurar logs estructurados con `structlog` y un id por petición que se propague por todo el flujo.
- Exponer métricas en `/metrics`: latencia por ruta y ecosistema, decisiones por regla y modo, aciertos de caché y errores upstream.
- Escribir un log de auditoría con cada decisión.

**Listo cuando**
- `/metrics` muestra la latencia p95 y los bloqueos por regla.
- Ningún log contiene tokens.

#### D3-8 · Re-escaneo periódico
**Semana:** 5 · **Tamaño:** M · **Depende de:** D3-6, D5-5 · **Issue:** nuevo · **Código:** `jobs/rescan.py`

**Qué hacer**
- Crear un job programado que vuelva a evaluar los SBOMs guardados contra los advisories nuevos.
- Registrar cada hallazgo nuevo como evento.

**Listo cuando**
- Un advisory nuevo (simulado en el test) que afecta a un SBOM viejo genera un evento y una alerta.

#### D3-9 · Alertas por webhook
**Semana:** 6 · **Tamaño:** S · **Depende de:** D3-7 · **Issue:** nuevo · **Código:** `notifications/`

**Qué hacer**
- Enviar webhooks configurables (JSON genérico, Slack y Discord) para bloqueos, decisiones en modo degradado y hallazgos del re-escaneo.
- Reintentar si falla y no enviar duplicados.

**Listo cuando**
- El mensaje llega a un webhook de prueba.
- Si el webhook falla, el proxy no se afecta.

#### D3-10 · Copia local de OSV (opcional)
**Semana:** 6–7, solo si hay tiempo · **Tamaño:** M · **Depende de:** D3-3 · **Issue:** BACK-02 · **Código:** `analyzers/vulnerabilities/offline.py`

**Qué hacer**
- Descargar periódicamente los volcados de OSV de npm y PyPI.
- Indexarlos y usarlos como respaldo cuando la API no responde.

**Listo cuando**
- Con OSV caído, las vulnerabilidades se siguen detectando.

---

## Dev 4 · Typosquatting, políticas, setup y release

#### D4-1 · Setup base del repositorio
**Semana:** 1 · **Tamaño:** M · **Depende de:** — · **Issue:** SEC-02 · **Código:** raíz del repo

**Qué hacer**
- Proteger `main`: PR obligatorio, 1 aprobación y CI en verde.
- Activar Dependabot, secret scanning y private vulnerability reporting.
- **Terminarlo en los primeros 2–3 días**, porque todos dependen de él.

**Listo cuando**
- `make check` pasa y la CI está en verde.
- `docker compose up` responde 200 en `/healthz`.

#### D4-2 · Lista de paquetes populares
**Semana:** 2 · **Tamaño:** S · **Depende de:** D3-1 · **Issue:** BACK-03 · **Código:** `scripts/fetch_popular.py`, `data/popular/`

**Qué hacer**
- Escribir un script que descargue el top 5 000 de npm y de PyPI, con la fuente documentada.
- Normalizar los nombres y guardar el resultado versionado en el repo.
- Agregar un job semanal que abra un PR cuando la lista cambie.

**Listo cuando**
- El archivo trae fecha y fuente, y carga en menos de 1 s.

#### D4-3 · Motor de políticas
**Semana:** 2 · **Tamaño:** M · **Depende de:** D3-1 · **Issue:** SEC-01 · **Código:** `policy/`, `config/policy.yaml`, `docs/policy.md`

**Qué hacer**
- Escribir la política en YAML y validarla con Pydantic:
  - para cada regla: umbral de severidad, qué hacer con `UNKNOWN` y modo (bloquear, advertir o registrar);
  - ajustes por proyecto;
  - excepciones por paquete o advisory, con justificación, autor y fecha de vencimiento.
- Implementarla como función pura: `evaluate(findings, ctx, policy) -> PolicyDecision`.
- Documentar la política por defecto y por qué se eligieron esos valores (insumo para SEC-01).

**Listo cuando**
- La cobertura del módulo es de 100 %.
- Una excepción vencida deja de aplicarse.

#### D4-4 · Detector de typosquatting
**Semana:** 3–4 · **Tamaño:** L · **Depende de:** D4-2 · **Issue:** BACK-03 · **Código:** `analyzers/typosquatting/`

**Qué hacer**
- Combinar estas técnicas, cada una con su peso, para obtener un puntaje de 0 a 1 y el paquete legítimo con el que se confunde:
  - distancia entre nombres (Damerau-Levenshtein y Jaro-Winkler, con `rapidfuzz`);
  - separadores (`-`, `_`, `.`);
  - letras parecidas (`rn`/`m`, `l`/`I`, `0`/`o`);
  - prefijos y sufijos agregados a un nombre popular;
  - confusión de `@scope` en npm.
- Mantener una lista de nombres permitidos para los parecidos legítimos.

**Listo cuando**
- Detecta casos reales: `crossenv`, `python3-dateutil`, `jeIlyfish`.
- Tarda menos de 5 ms por paquete (p95).
- No marca ningún paquete del top 1 000.

#### D4-5 · API de administración
**Semana:** 5 · **Tamaño:** M · **Depende de:** D5-5 · **Issue:** nuevo · **Código:** `api/admin/`

**Qué hacer**
- Proteger la API con un token de administrador.
- Endpoints para:
  - listar y filtrar decisiones;
  - ver los SBOMs por proyecto o build;
  - crear, listar y revocar excepciones;
  - ver la política activa y recargarla sin reiniciar.

**Listo cuando**
- El OpenAPI está completo y hay tests de autorización.
- Al crear una excepción, el paquete se desbloquea en la siguiente petición.

#### D4-6 · Medición del detector de typosquatting
**Semana:** 6 · **Tamaño:** M · **Depende de:** D4-4 · **Issue:** SEC-05 · **Código:** `eval/typosquatting/`, `docs/results/typosquatting.md`

**Qué hacer**
- Armar un dataset etiquetado:
  - positivos: paquetes maliciosos reales de OpenSSF malicious-packages, Backstabber's Knife Collection o el dataset de Datadog;
  - negativos: el top N y una muestra aleatoria de paquetes legítimos.
- Calcular precisión, recall y F1 según el umbral.
- Elegir el umbral por defecto con base en esos resultados.

**Listo cuando**
- Hay un script o notebook reproducible.
- Las tablas y gráficas están listas para el documento.

#### D4-7 · Release firmado
**Semana:** 6–7 · **Tamaño:** S · **Depende de:** D4-1 · **Issue:** SEC-02 · **Código:** `.github/workflows/release.yml`

**Qué hacer**
- Al crear un tag `v*`:
  - publicar la imagen Docker en GHCR, firmada con cosign;
  - adjuntar el SBOM del propio Supply-Shield;
  - generar el CHANGELOG con commitizen.

**Listo cuando**
- `cosign verify` funciona sobre la imagen publicada.
- El SBOM está adjunto al release.

---

## Dev 5 · SBOM, heurísticas, datos y pentest

#### D5-1 · Lectura de lockfiles
**Semana:** 1 · **Tamaño:** M · **Depende de:** D3-1 · **Issue:** BACK-04 · **Código:** `sbom/lockfiles/`

**Qué hacer**
- Convertir estos lockfiles a una lista de `PackageRef` con su hash e indicando si cada dependencia es directa o transitiva:
  - `package-lock.json` v2 y v3;
  - `pnpm-lock.yaml`;
  - `requirements.txt` con versiones fijadas y hashes;
  - `poetry.lock`;
  - `uv.lock`.

**Listo cuando**
- Hay un ejemplo real de cada formato con su test.
- Un archivo roto da un error claro, no un crash.

#### D5-2 · Heurísticas de riesgo
**Semana:** 2 · **Tamaño:** M · **Depende de:** D3-1 · **Issue:** nuevo · **Código:** `analyzers/heuristics/`

**Qué hacer**
- Implementar tres reglas sobre `UpstreamMetadata`, cada una con umbral configurable:
  - `cooldown`: la versión tiene menos de N días publicada;
  - `install_scripts`: el paquete de npm declara `preinstall`, `install` o `postinstall`, o el de PyPI solo ofrece sdist;
  - `new_package`: el paquete tiene menos de N días y pocas versiones.

**Listo cuando**
- Cada regla tiene tests con metadata real.

#### D5-3 · Generador de SBOM
**Semana:** 3 · **Tamaño:** M · **Depende de:** D5-1 · **Issue:** BACK-04 · **Código:** `sbom/cyclonedx.py`

**Qué hacer**
- Generar el SBOM en CycloneDX 1.6 JSON con `cyclonedx-python-lib`, incluyendo:
  - componentes con `purl` y hashes;
  - el árbol de dependencias cuando el lockfile lo trae;
  - los datos de la herramienta;
  - las vulnerabilidades encontradas.

**Listo cuando**
- El SBOM valida contra el esquema oficial.
- Cumple los NTIA minimum elements (verificado en un test).

#### D5-4 · Dependency confusion
**Semana:** 3 · **Tamaño:** S · **Depende de:** D2-1 · **Issue:** nuevo · **Código:** `analyzers/dependency_confusion/`

**Qué hacer**
- Configurar por proyecto una lista de scopes y nombres internos.
- Si se pide uno de esos nombres al registro público, generar un hallazgo `dependency_confusion` crítico.

**Listo cuando**
- Un paquete `@empresa/*` pedido a npm público se bloquea siempre.

#### D5-5 · Base de datos
**Semana:** 4 · **Tamaño:** M · **Depende de:** D3-1 · **Issue:** BACK-06 · **Código:** `db/`, `migrations/`

**Qué hacer**
- Agregar PostgreSQL al `docker compose` y a la CI, con SQLAlchemy 2 async y migraciones con Alembic.
- Crear tablas para proyectos y tokens, decisiones, SBOMs, builds y excepciones.
- Que los datos en YAML (D2-1, D4-3) se puedan cargar a la base.

**Listo cuando**
- Las migraciones suben y bajan sin errores.
- Los tests usan una base temporal.

#### D5-6 · Escaneo de lockfiles por API y consola
**Semana:** 5 · **Tamaño:** S · **Depende de:** D3-6, D5-3 · **Issue:** nuevo · **Código:** `api/scan.py`, `cli.py`

**Qué hacer**
- `POST /api/v1/scan`: recibe un lockfile y devuelve el SBOM y la decisión para cada paquete.
- `supply-shield scan <lockfile>`: lo mismo desde la terminal; termina con código 1 si algo queda bloqueado, para usarlo en CI.
- Guardar el SBOM en la base de datos.

**Listo cuando**
- La CI falla con un lockfile vulnerable y pasa con uno limpio.

#### D5-7 · Redis y caché de archivos
**Semana:** 5 · **Tamaño:** M · **Depende de:** D3-2, D5-5 · **Issue:** BACK-05 · **Código:** `cache/redis.py`, `cache/artifacts.py`

**Qué hacer**
- Implementar la interfaz `Cache` de D3-2 con Redis.
- Guardar en disco los archivos descargados, identificados por su hash.

**Listo cuando**
- Un segundo `npm ci` no vuelve a descargar de internet.
- Reiniciar el proxy no vacía la caché.
- Un archivo con hash incorrecto nunca se sirve desde la caché.

#### D5-8 · Pentest al proxy
**Semana:** 6, y las correcciones en la 7 · **Tamaño:** M · **Depende de:** D1-4, D2-3 · **Issue:** SEC-04 · **Código:** `docs/pentest.md`

**Qué hacer**
- Atacar el proxy usando como guía el modelo de amenazas de D1-4:
  - evadir los bloqueos con mayúsculas, separadores, URL encoding o pidiendo el archivo sin pasar por la metadata;
  - SSRF;
  - envenenar la caché;
  - DoS.
- Abrir un issue por cada hallazgo y asignarlo al dueño del código.

**Listo cuando**
- Hay un informe con los hallazgos.
- En la semana 7, cada hallazgo está corregido o tiene una justificación de por qué no se corrige.

---

## Todo el equipo

#### T-1 · Demo final
**Semana:** 7 · **Responsables:** todos

**Qué hacer**
- Preparar un script en el que un proyecto instala dependencias y el firewall bloquea un typosquat, una vulnerabilidad crítica y una versión recién publicada.
- Mostrar al final el SBOM y las métricas.

**Listo cuando**
- `make demo` corre de principio a fin.
- Hay un video de respaldo grabado.

---

## Calendario

| Semana | Dev 1 | Dev 2 | Dev 3 | Dev 4 | Dev 5 |
|---|---|---|---|---|---|
| 1 | D1-1 | D2-1 | D3-1, D3-2 | D4-1 | D5-1 |
| 2 | D1-2 | D2-2 | D3-3, D3-4 | D4-2, D4-3 | D5-2 |
| 3 | D1-2 | D2-2 | D3-5, D3-6 | D4-4 | D5-3, D5-4 |
| 4 | D1-3 | D2-3 | D3-6, D3-7 | D4-4 | D5-5 |
| 5 | D1-4 | D2-3, D2-4 | D3-8 | D4-5 | D5-6, D5-7 |
| 6 | D1-5 | D2-5, D2-6 | D3-9, D3-10* | D4-6, D4-7 | D5-8 |
| 7 | Correcciones del pentest + T-1 | D2-6 + T-1 | D3-10* + T-1 | D4-7 + T-1 | Verificar correcciones + T-1 |

\* Opcional.

**Hitos para revisar avance**
- **Fin de la semana 1:** repo listo, modelos compartidos aprobados y ADRs escritos.
- **Fin de la semana 3:** `npm install` y `pip install` funcionan a través del proxy, todavía sin bloquear.
- **Fin de la semana 4:** primer bloqueo real de punta a punta (una vulnerabilidad crítica bloqueada en npm y en PyPI).
- **Fin de la semana 5:** todas las reglas bloquean, y el e2e con cebos está en verde.
- **Fin de la semana 6:** resultados medidos (latencia, typosquatting, comparación) y pentest hecho.
- **Fin de la semana 7:** demo y release `v1.0.0`.

**Si se atrasan, recorten en este orden:** D3-10, D2-4, D3-9, D3-8 y, por último, D4-5 dejándola solo en lectura.

## Relación con los issues actuales

| Issue actual | Tareas |
|---|---|
| BACK-01 Proxy | D1-1, D1-2, D1-3, D2-2 |
| BACK-02 Vulnerabilidades | D3-3, D3-10 |
| BACK-03 Typosquatting | D4-2, D4-4 |
| BACK-04 SBOM | D5-1, D5-3, D2-4 |
| BACK-05 Caché | D3-2, D5-7 |
| BACK-06 Modelo de datos | D3-1, D5-5 |
| SEC-01 Políticas | D4-3 |
| SEC-02 CI/CD | D4-1, D4-7 |
| SEC-03 Proyecto cebo | D2-3 |
| SEC-04 Pentesting | D5-8 |
| SEC-05 Falsos positivos | D4-6 |
| SEC-06 Fail-open / fail-closed | D3-5 |

Las demás tareas son nuevas y no tienen issue todavía.
