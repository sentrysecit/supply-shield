# 0004. Base de datos PostgreSQL con SQLAlchemy 2 async y Alembic

- **Estado**: Aceptado
- **Fecha**: 2026-10-27

## Contexto

El MVP necesita persistencia para proyectos, tokens, decisiones de política, SBOMs, builds y excepciones. Los datos en YAML de D2-1 (proyectos/tokens) y D4-3 (políticas) deben poder cargarse en la base. Se requiere SQLAlchemy 2 async con migraciones Alembic, y PostgreSQL en docker-compose y CI.

## Decisión

Usaremos PostgreSQL como base de datos relacional con SQLAlchemy 2 async (driver asyncpg) y Alembic para migraciones. Las tablas son:

- **projects**: nombre del proyecto, fecha de creación.
- **tokens**: hash del token de acceso por proyecto (nunca texto plano), fecha de expiración opcional.
- **decisiones**: acción (allow/warn/block), razones, hallazgos, modo degradado, por proyecto y paquete.
- **sboms**: SBOM CycloneDX en JSONB, asociado a proyecto y build opcional.
- **builds**: registro de builds con ID y estado.
- **excepciones**: excepciones de política por paquete/advisory con justificación, autor y vencimiento.

Todas las tablas tienen una clave foránea a `projects.id` con `ON DELETE CASCADE`. Se añaden índices en las claves foráneas y en campos de consulta frecuente (`name`, `build_id`).

## Alternativas consideradas

- **SQLite**: descartado porque no soporta async nativamente con SQLAlchemy 2 y no es apropiado para producción concurrente.
- **MySQL/MariaDB**: descartado porque PostgreSQL tiene mejor soporte para JSONB y es el estándar del equipo.
- **Solo archivos YAML**: descartado porque no permite consultas estructuradas ni transacciones, y no escala para múltiples proyectos.

## Consecuencias

Positivas: consultas estructuradas, transacciones ACID, JSONB para SBOMs, migraciones versionadas con Alembic, datos de YAML cargables.
Negativas: dependencia de PostgreSQL en desarrollo y CI, complejidad añadida de gestionar migraciones.
Riesgos: las pruebas deben usar una base temporal, nunca la de desarrollo.
