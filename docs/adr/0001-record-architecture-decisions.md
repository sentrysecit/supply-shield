# 0001. Registrar las decisiones de arquitectura

- **Estado**: Aceptado
- **Fecha**: 2026-10-08

## Contexto

Supply-Shield es un proyecto académico. Las decisiones de diseño se toman en conversaciones y revisiones que luego son difíciles de reconstruir. Para que el equipo y futuros colaboradores comprendan el *por qué* de las decisiones, conviene registrarlas en un formato ligero y revisable.

## Decisión

Registraremos las decisiones de arquitectura relevantes como *Architecture Decision Records* (ADR), siguiendo el formato ligero propuesto por Michael Nygard.

- Cada ADR es un archivo Markdown en `docs/adr/`, numerado de forma consecutiva: `NNNN-titulo-en-kebab-case.md`.
- Se parte de [template.md](template.md).
- Los ADR se escriben en español y entran por pull request, como cualquier otro cambio.
- Un ADR aceptado no se reescribe: si la decisión cambia, se crea uno nuevo que lo reemplaza y se actualiza el estado del anterior.

## Alternativas consideradas

- **No documentar las decisiones**: se pierde el contexto y se repiten discusiones.
- **Wiki o documento externo**: se desincroniza del código y no pasa por revisión.

## Consecuencias

- El historial de decisiones vive junto al código y se revisa igual que este.
- Escribir un ADR añade un pequeño costo a cada decisión importante.
- Hay que acordar qué se considera "relevante": como guía, todo lo que sea costoso de revertir o afecte a más de un módulo.
