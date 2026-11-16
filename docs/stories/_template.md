# {ID} · {Título de la tarea}

- **Rama:** `{tipo}/{issue}-{descripcion}`
- **Issue:** {BACK-XX}
- **Módulos:** `src/supply_shield/...` (uno o varios)
- **Tamaño:** {S | M | L} · **Semana:** {1-7}
- **Depende de:** {D{X-Y} o "ninguna"} → {¿satisfecha? si/no, breve}
- **ADR del área:** [{NNNN}](../adr/{NNNN}-titulo.md) (si aplica)
- **Estado:** {plan | en desarrollo | implementado; pendiente de revisión}

## Contexto

{1-2 párrafos: qué problema resuelve esta pieza, por qué importa, dónde encaca
en la arquitectura. Citar ADRs o `docs/architecture.md` si aplica.}

---

## Historias de usuario

### HU-1 · {Resumen}

**Como** {rol},
**quiero** {capacidad},
**para** {beneficio}.

**Criterios de aceptación (Given / When / Then)**

1. **Given** {precondición}, **when** {acción}, **then** {resultado verificable}.
2. ...

> Repetir un bloque HU-N por cada historia.

---

## Requisitos funcionales

| ID  | Requisito |
| --- | --------- |
| RF-1 | {comportamiento concreto, verificable} |
| RF-2 | ... |

## Requisitos no funcionales

| ID  | Requisito |
| --- | --------- |
| RNF-1 | **{categoría}**: {restricción} |
| RNF-2 | ... |

> Categorías habituales: Integridad, Seguridad, Memoria, Concurrencia, Tipado,
> Calidad, Observabilidad, Testeable, Configuración.

---

## Orden de ejecución

| Paso | Qué se hace | Requisitos | Estado |
| --- | --- | --- | --- |
| 1 | {primer paso} | RF-X | pendiente |
| 2 | {segundo paso} | RF-Y | pendiente |
| ... | | | |

**Precondición:** {dependencias que deben estar listas antes de empezar, o "ninguna"}.

**Verificación por paso:** `uv run ruff check` + `uv run ruff format --check`
+ `uv run mypy` tras cada paso; `make check` completo antes de cerrar el PR.

## Estructura de código

Cada archivo con una responsabilidad; sin espagueti, siguiendo `docs/architecture.md`:

```text
src/supply_shield/
└── {paquete}/
    └── {modulo}.py   # RF-X: {responsabilidad}

tests/unit/{paquete}/
└── test_{modulo}.py  # {qué cubre}
```

- Código, nombres y docstrings en **inglés**; documentación del equipo en **español**.
- {Notas de separación de responsabilidades si hay varios módulos.}

---

## Fuera de alcance

- {lo que esta tarea NO hace, aunque parezca cercano}

## "Listo cuando" (resumen de aceptación de la tarea)

- [ ] {criterio 1, copiado del enunciado de la tarea}
- [ ] {criterio 2}
- [ ] ...
- [ ] Tests unitarios con `respx` sin red; `make check` en verde.
- [ ] Documentación/ADR del área actualizados.
- [ ] Aprobación de alguien de otra área.

## Historial

| Fecha | Evento |
| --- | --- |
| {AAAA-MM-DD} | {qué se hizo o decidió} |
