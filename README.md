# Supply-Shield

Supply-Shield es un *dependency firewall*: un proxy de registros de paquetes (npm y PyPI) por el que pasan todas las descargas de dependencias del equipo.

Funciona como **registry mirror**: los clientes apuntan `registry=` (en `.npmrc`) o `index-url` (en `pip.conf`) al proxy. No es un proxy HTTPS con MITM. Para cada paquete solicitado, Supply-Shield:

- consulta vulnerabilidades conocidas en [OSV.dev](https://osv.dev),
- detecta posibles casos de typosquatting,
- aplica políticas de bloqueo,
- cachea los resultados,
- y genera SBOMs en formato CycloneDX.

## Estado del proyecto

🚧 **En construcción.** Por ahora el repositorio contiene solo el esqueleto: la aplicación FastAPI con los endpoints de salud (`/healthz`, `/readyz`), las herramientas de calidad, los hooks de Git, la CI y la documentación base. La lógica del proxy, los analizadores y las políticas todavía no están implementados.

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

## Estructura de carpetas

```text
supply-shield/
├── src/supply_shield/
│   ├── main.py          # factory create_app() de FastAPI
│   ├── config.py        # Settings (variables SUPPLY_SHIELD_*)
│   ├── logging.py       # configuración de structlog
│   ├── api/             # endpoints propios del servicio (salud)
│   ├── proxy/           # adaptadores de registro: npm/ y pypi/
│   ├── analyzers/       # vulnerabilities/ (OSV) y typosquatting/
│   ├── policy/          # motor de políticas de bloqueo
│   ├── sbom/            # generación de SBOM CycloneDX
│   └── cache/           # caché de resultados
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
- [docs/adr/](docs/adr/): decisiones de arquitectura.
- [SECURITY.md](SECURITY.md): cómo reportar vulnerabilidades.
- [AGENTS.md](AGENTS.md): guía para asistentes de código.

## Licencia

[MIT](LICENSE)
