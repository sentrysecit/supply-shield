# AGENTS.md

Guía para asistentes de código que trabajen en este repositorio.

## Resumen del proyecto

Supply-Shield es un *dependency firewall*: un proxy de registros de paquetes (npm y PyPI) que funciona como **registry mirror** (los clientes configuran `registry=` o `index-url` apuntando al proxy; no hay MITM de HTTPS). Para cada paquete solicitado consulta vulnerabilidades en OSV.dev, detecta typosquatting, aplica políticas de bloqueo, cachea resultados y genera SBOMs en CycloneDX.

## Stack

- Python 3.13 gestionado con **uv** (`pyproject.toml` + `uv.lock`). No uses `requirements.txt`, `setup.py` ni Poetry.
- Layout `src/`, paquete `supply_shield`.
- FastAPI + Uvicorn, httpx (async), pydantic-settings, structlog, prometheus-client, rapidfuzz, packaging, cyclonedx-python-lib.
- Desarrollo: pytest, pytest-asyncio, pytest-cov, respx, ruff, mypy, bandit, pip-audit, pre-commit, commitizen.

## Comandos

**Ejecuta `make check` antes de dar por terminada cualquier tarea**; debe pasar en verde. O bien, check.ps1 (Windows) o `uv run ...` de los equivalentes listados en el [README](README.md).

| Comando          | Qué hace                                             |
| ---------------- | ---------------------------------------------------- |
| `make install`   | `uv sync` + instalación de hooks                     |
| `make run`       | API local con recarga                                |
| `make format`    | `ruff check --fix` + `ruff format`                   |
| `make lint`      | `ruff check` + `ruff format --check`                 |
| `make typecheck` | `mypy` estricto                                      |
| `make test`      | pruebas unitarias                                    |
| `make cov`       | pruebas con cobertura (mínimo 80 %)                  |
| `make security`  | bandit + pip-audit                                   |
| `make check`     | lint + typecheck + security + cov (igual que la CI)  |

Sin `make` (Windows), usa los equivalentes `uv run ...` listados en el [README](README.md).

## Convenciones de código

- Código, nombres, docstrings y mensajes de commit en **inglés**; documentación del equipo y comentarios dentro del código (docstrings) en **español**.
- Todo tipado: `mypy --strict` con cero errores. Evita `Any` y los `# type: ignore` sin justificar.
- Docstrings breves en módulos, clases y funciones públicas.
- Ruff es linter y formateador (línea de 100). No uses black ni isort.
- E/S asíncrona: usa `httpx.AsyncClient`; nada de llamadas bloqueantes dentro de corrutinas.
- La configuración sale **solo** de `supply_shield.config.Settings` (prefijo `SUPPLY_SHIELD_`). Nada de `os.getenv` disperso ni secretos en el código. Toda variable nueva se documenta en `.env.example`.
- Logs con `structlog`, nunca `print`.
- Pruebas: unitarias en `tests/unit/` sin red (simula HTTP con `respx`); las que usan servicios reales van en `tests/integration/` con `@pytest.mark.integration`.
- Dependencias: agrégalas con `uv add` / `uv add --dev` y explica por qué. No escribas versiones a mano.
- Commits: Conventional Commits. No hagas commit directo en `main`.
- Las decisiones de arquitectura se registran como ADR en `docs/adr/`.

## Reglas que no se rompen

- **El proxy nunca debe modificar los bytes de un artefacto descargado.** Los tarballs, wheels y sdists se sirven exactamente como los entrega el registro upstream: sin recomprimir, reempaquetar ni reescribir. Los hashes de integridad que ve el cliente deben coincidir con los del upstream. Solo se puede decidir entre servir el artefacto intacto o bloquearlo.
- No desactives ni debilites los chequeos de calidad (ruff, mypy, bandit, cobertura) para que una tarea pase.
- No hagas commit de `.env` ni de ningún secreto.
