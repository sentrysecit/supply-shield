# Guía de contribución

Gracias por contribuir a Supply-Shield. Esta guía resume cómo trabajamos.

## Preparar el entorno

```bash
uv sync
uv run pre-commit install
cp .env.example .env
```

`pre-commit install` instala de una vez los tres hooks: `pre-commit`, `commit-msg` y `pre-push`.

## Flujo de ramas

- `main` está protegida: no se hace push directo; todo entra por pull request.
- Crea una rama desde `main` por cada issue, con el prefijo del tipo de cambio:

  | Tipo          | Formato                        | Ejemplo                         |
  | ------------- | ------------------------------ | ------------------------------- |
  | Funcionalidad | `feat/<issue>-descripcion`     | `feat/12-npm-packument-adapter` |
  | Corrección    | `fix/<issue>-descripcion`      | `fix/27-cache-ttl-overflow`     |
  | Documentación | `docs/<issue>-descripcion`     | `docs/8-adr-cache-backend`      |
  | Mantenimiento | `chore/<issue>-descripcion`    | `chore/31-bump-ruff`            |

- Mantén los PR pequeños y enfocados en un solo cambio.
- Actualiza tu rama con `main` antes de pedir revisión.

## Mensajes de commit

Usamos [Conventional Commits](https://www.conventionalcommits.org/es/v1.0.0/), en inglés. El hook `commit-msg` los valida con Commitizen.

```text
<type>(<scope opcional>): <descripción en imperativo y minúscula>
```

Tipos más usados: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`.

Ejemplos:

```text
feat(proxy): add npm packument passthrough
fix(policy): reject packages with unknown license
docs(adr): record decision on cache backend
test(analyzers): cover typosquatting distance thresholds
ci: cache uv downloads in the lint job
feat(api)!: rename readiness endpoint

BREAKING CHANGE: /ready is now /readyz
```

Si prefieres un asistente interactivo, usa `uv run cz commit`.

## Hooks y pruebas

Los hooks corren solos, pero puedes lanzarlos a mano:

```bash
uv run pre-commit run --all-files                         # hooks de pre-commit
uv run pre-commit run --all-files --hook-stage pre-push   # pruebas unitarias
```

| Etapa        | Qué corre                                                                         |
| ------------ | --------------------------------------------------------------------------------- |
| `pre-commit` | higiene de archivos, ruff (lint + formato), mypy, bandit, gitleaks, `uv lock`     |
| `commit-msg` | Commitizen (Conventional Commits)                                                 |
| `pre-push`   | `pytest -m "not integration"`                                                     |

Comandos habituales (ver el [README](README.md) para los equivalentes sin `make`):

```bash
make format   # corrige lint y formatea
make test     # pruebas unitarias
make cov      # todas las pruebas con cobertura (mínimo 80 %)
make check    # todo lo que valida la CI
```

Las pruebas que hablan con servicios externos reales van en `tests/integration/` y llevan `@pytest.mark.integration`. Las unitarias no usan red: simula las llamadas HTTP con `respx`.

> El hook `no-commit-to-branch` impide commitear en `main`. Si ejecutas `pre-commit run --all-files` estando en `main`, ese hook fallará: es el comportamiento esperado.

## Dependencias

- Agrégalas con `uv add <paquete>` o `uv add --dev <paquete>`; nunca edites `uv.lock` a mano.
- Justifica en el PR cualquier dependencia nueva.

## Checklist de pull request

- [ ] El título sigue Conventional Commits.
- [ ] El PR enlaza su issue (`Closes #<n>`).
- [ ] `make check` pasa en local.
- [ ] Hay pruebas para el código nuevo o modificado.
- [ ] La documentación está actualizada (README, `docs/`, ADR si hay una decisión de arquitectura).
- [ ] No hay secretos ni datos sensibles en el diff.

## Definition of Done

Un cambio está terminado cuando:

1. **Código**: implementado, tipado y sin avisos de ruff ni mypy.
2. **Pruebas**: cubierto por pruebas, con la cobertura global en 80 % o más.
3. **Documentación**: actualizada donde corresponda.
4. **CI en verde**: todos los jobs pasan.
5. **Revisión**: al menos 1 aprobación de otra persona del equipo.

## Decisiones de arquitectura

Las decisiones relevantes se registran como ADR en [docs/adr/](docs/adr/). Copia [docs/adr/template.md](docs/adr/template.md), numera el archivo de forma consecutiva y ábrelo en un PR.

## Idioma

- Código, nombres, docstrings y mensajes de commit: **inglés**.
- Documentación para el equipo (README, guías, ADR, issues y PR): **español**.
