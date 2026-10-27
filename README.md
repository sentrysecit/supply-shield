# Supply-Shield

Supply-Shield es un *dependency firewall*: un proxy de registros de paquetes (npm y PyPI) por el que pasan todas las descargas de dependencias del equipo.

Funciona como **registry mirror**: los clientes apuntan `registry=` (en `.npmrc`) o `index-url` (en `pip.conf`) al proxy. No es un proxy HTTPS con MITM. Para cada paquete solicitado, Supply-Shield:

- consulta vulnerabilidades conocidas en [OSV.dev](https://osv.dev),
- detecta posibles casos de typosquatting,
- aplica políticas de bloqueo,
- cachea los resultados,
- y genera SBOMs en formato CycloneDX.

## Estado del proyecto

🚧 **En construcción.** Hoy están la aplicación FastAPI con `/healthz` y `/readyz`, los modelos compartidos (`core/`), la caché en memoria y las herramientas de calidad (hooks, CI y ADRs). El proxy, los analizadores y las políticas todavía no están implementados: no hay un endpoint que evalúe un paquete.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala y gestiona Python 3.13 por ti)
- [Docker](https://docs.docker.com/get-docker/) con Docker Compose
- Git
- `make` (opcional; en Windows puedes usar los comandos `uv run` equivalentes de más abajo)

## Arranque rápido

```bash
git clone <url-del-repositorio> && cd supply-shield
uv sync
uv run pre-commit install
cp .env.example .env
uv run uvicorn supply_shield.main:create_app --factory --reload
```

La API queda en <http://localhost:8000>; prueba `curl localhost:8000/healthz` o abre <http://localhost:8000/docs>.

Con `make`, los pasos 2 y 3 son `make install` y el paso 5 es `make run`.

## Comandos del día a día

| `make`                  | Equivalente sin `make` (Windows)                                              | Qué hace                                                 |
| ----------------------- | ----------------------------------------------------------------------------- | -------------------------------------------------------- |
| `make help`             | —                                                                             | Lista los comandos disponibles (target por defecto)      |
| `make install`          | `uv sync` y luego `uv run pre-commit install`                                 | Instala dependencias y hooks de Git                      |
| `make run`              | `uv run uvicorn supply_shield.main:create_app --factory --reload`             | Levanta la API con recarga automática                    |
| `make lint`             | `uv run ruff check` y `uv run ruff format --check`                            | Linter y verificación de formato                         |
| `make format`           | `uv run ruff check --fix` y `uv run ruff format`                              | Corrige y formatea el código                             |
| `make typecheck`        | `uv run mypy`                                                                 | Chequeo de tipos estricto                                |
| `make test`             | `uv run pytest -m "not integration"`                                          | Pruebas unitarias                                        |
| `make test-integration` | `uv run pytest -m integration`                                                | Pruebas de integración                                   |
| `make cov`              | `uv run pytest --cov --cov-report=term-missing --cov-report=xml`              | Todas las pruebas con cobertura (mínimo 80 %)            |
| `make security`         | `uv run bandit -c pyproject.toml -r src` y los dos comandos de auditoría (\*) | Análisis estático de seguridad y auditoría de dependencias |
| `make check`            | `lint`, `typecheck`, `security` y `cov`, en ese orden                         | Todo lo que valida la CI                                 |
| `make up`               | `docker compose up -d --build`                                                | Construye y levanta el contenedor                        |
| `make down`             | `docker compose down`                                                         | Detiene el contenedor                                    |
| `make clean`            | —                                                                             | Borra cachés y artefactos generados                      |

(\*) Auditoría de las dependencias bloqueadas en `uv.lock`:

```bash
uv export --locked --no-emit-project --format requirements-txt --output-file requirements-audit.txt
uv run pip-audit --requirement requirements-audit.txt --require-hashes --disable-pip
```

`requirements-audit.txt` es un archivo temporal ignorado por Git; la única fuente de verdad de las dependencias es `pyproject.toml` + `uv.lock`.

> `make test-integration` termina con "no tests collected" (código 5) mientras `tests/integration/` siga vacío.

Todos estos comandos se ejecutan desde la carpeta `supply-shield`.

### Qué hace cada comando

**`uv sync`** lee `pyproject.toml` y `uv.lock`, crea o actualiza `.venv` con Python 3.13 e instala el proyecto. Instala las dependencias de ejecución (FastAPI, httpx, Pydantic, structlog) y las de desarrollo (pytest, ruff, mypy, bandit, pip-audit). No corre tests ni levanta el servidor. Se usa al clonar el repo y cada vez que cambia el lockfile.

**`uv run`** ejecuta un programa de ese `.venv` sin activarlo a mano. `uv run pytest` y `uv run python` usan el intérprete del proyecto.

**`uv run pytest -m "not integration" -q`** corre las pruebas de unidad. Es el mismo conjunto que `make test`, con menos texto.

- `-m "not integration"` deja fuera las pruebas marcadas como `integration`. Esa marca, definida en `pyproject.toml`, es para pruebas que hablan con un servicio externo de verdad. Las de unidad (nombres, `purl`, modelos y caché) no salen a internet.
- `-q` muestra un punto por prueba y el resumen. Si alguna falla, el error sí se imprime.
- Un código de salida distinto de cero significa que algo falló.

**`uv run pytest --cov --cov-report=term-missing -m "not integration"`** corre esas mismas pruebas de unidad y mide cuánto código de `src/supply_shield` ejecutaron, incluidas las ramas de los `if`. La tabla final lista, por archivo, el porcentaje y las líneas que ningún test tocó. El mínimo del proyecto es **80 %**: si la cobertura queda por debajo, el comando falla aunque los tests hayan pasado.

**`make cov`** mide la cobertura de **todas** las pruebas, también las de integración, y además escribe `coverage.xml`. El comando anterior, con `-m "not integration"`, no incluye esas pruebas ni genera el XML.

**`make check`** corre, en este orden, lo mismo que valida la CI:

1. `make lint`: ruff busca errores de estilo y `ruff format --check` comprueba el formato. No modifica archivos. Para corregirlos está `make format`.
2. `make typecheck`: mypy en modo estricto sobre `src` y `tests`.
3. `make security`: bandit revisa `src`, y pip-audit revisa las dependencias fijadas en `uv.lock` (con sus hashes) en busca de vulnerabilidades conocidas.
4. `make cov`: todas las pruebas con cobertura. El mínimo sigue siendo 80 %.

Si un paso falla, `make` se detiene.

**`make run`** levanta la API en <http://localhost:8000> con recarga al guardar. Hoy responde `/healthz`, `/readyz` y `/docs`. No evalúa paquetes.

### Probar un modelo a mano

Este comando construye la referencia de un paquete PyPI con el nombre escrito de forma irregular y muestra cómo queda normalizado:

```bash
uv run python -c "from supply_shield.core import Ecosystem, PackageRef; p = PackageRef.of(Ecosystem.PYPI, 'Django_REST.framework', '3.14.0'); print(p.name, p.purl, p.identity)"
```

`-c` ejecuta esa línea sin crear un archivo. La salida esperada es:

```text
django-rest-framework pkg:pypi/django-rest-framework@3.14.0 pypi:django-rest-framework
```

- `name` es el nombre canónico. En PyPI las mayúsculas y los separadores `-`, `_` y `.` no distinguen paquetes.
- `purl` es la URL del paquete: ecosistema, nombre y versión.
- `identity` es el ecosistema y el nombre, sin versión. La caché la usa para invalidar todas las versiones de un mismo paquete.

No levanta la API ni consulta OSV.

## Estructura de carpetas

```text
supply-shield/
├── src/supply_shield/
│   ├── main.py          # factory create_app() de FastAPI
│   ├── config.py        # Settings (variables SUPPLY_SHIELD_*)
│   ├── logging.py       # configuración de structlog
│   ├── api/             # endpoints propios del servicio (salud)
│   ├── core/            # modelos compartidos: paquete, hallazgo y decisión
│   ├── cache/           # caché en memoria (LRU y tiempo de vida)
│   ├── proxy/           # adaptadores de registro: npm/ y pypi/
│   ├── analyzers/       # vulnerabilities/ (OSV) y typosquatting/
│   ├── policy/          # motor de políticas de bloqueo
│   └── sbom/            # generación de SBOM CycloneDX
├── tests/
│   ├── unit/            # pruebas rápidas, sin red
│   └── integration/     # pruebas con marker "integration"
├── docs/
│   ├── architecture.md  # diagrama de alto nivel
│   └── adr/             # registros de decisiones de arquitectura
├── .github/             # CI, CodeQL, Dependabot y plantillas
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── pyproject.toml       # dependencias y configuración de herramientas
```

## Configuración

Toda la configuración se lee de variables de entorno con prefijo `SUPPLY_SHIELD_` (o de un archivo `.env` local). Consulta [.env.example](.env.example) para ver la lista completa.

## Documentación

- [CONTRIBUTING.md](CONTRIBUTING.md): flujo de trabajo, convenciones de commits y Definition of Done.
- [docs/architecture.md](docs/architecture.md): arquitectura de alto nivel.
- [docs/adr/](docs/adr/): registros de decisiones de arquitectura (ADR).
- [SECURITY.md](SECURITY.md): cómo reportar vulnerabilidades.
- [AGENTS.md](AGENTS.md): guía para asistentes de código.

### Decisiones de arquitectura

Un ADR es un Markdown corto que guarda **por qué** se tomó una decisión de diseño: el problema, la opción elegida, las alternativas descartadas y las consecuencias. Vive junto al código, se escribe en español y entra por pull request. Uno aceptado no se reescribe; si la decisión cambia, se crea otro archivo y el anterior pasa a reemplazado. La plantilla está en [docs/adr/template.md](docs/adr/template.md).

| ADR | Decisión |
| --- | --- |
| [0001](docs/adr/0001-record-architecture-decisions.md) | Las decisiones importantes se registran así. |
| [0002](docs/adr/0002-registry-mirror-instead-of-mitm-proxy.md) | Supply-Shield es un espejo de registro, no un proxy que intercepte HTTPS. |
| [0003](docs/adr/0003-osv-severity-and-unknown.md) | Las vulnerabilidades salen de OSV.dev. La severidad usa CVSS v3, luego v4, luego GHSA, y si no hay dato usable queda `UNKNOWN`. Un aviso `MAL-` es siempre crítico. |

## Licencia

[MIT](LICENSE)
