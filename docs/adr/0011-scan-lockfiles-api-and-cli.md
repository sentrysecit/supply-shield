# 0011. Escaneo de lockfiles por API y CLI con un pipeline compartido (D5-6)

- **Estado**: Propuesto
- **Fecha**: 2026-11-11

## Contexto

D5-6 pide que `POST /api/v1/scan` reciba un lockfile y devuelva el SBOM CycloneDX y la
decisión por paquete, y que `supply-shield scan <lockfile>` haga lo mismo desde la terminal
con exit 1 si algo queda bloqueado (para CI), guardando el SBOM en la base de datos. Ya
existen los bloques necesarios en `main`: los parsers de lockfiles (D5-1, `sbom/lockfiles/`),
el generador CycloneDX (D5-3, `sbom/cyclonedx.py`), el motor de políticas puro
(`policy/engine.evaluate`, ADR 0007), los analizadores offline (typosquatting D4-4,
dependency confusion D5-4) y la capa de datos (D5-5, `db/models.py` con las tablas
`projects`, `builds`, `sboms`, `decisions`).

## Decisión

**Un solo pipeline compartido.** `src/supply_shield/scan.py` define
`scan_lockfile_entries(entries, context, policy, analyzers) -> ScanReport`: corre los
analizadores registrados sobre cada paquete (en paralelo con `asyncio.gather`; un
analizador que falla marca la decisión como `degraded` en lugar de tumbar el escaneo),
aplica `policy.evaluate` por paquete y genera el SBOM con `generate_sbom` incluyendo los
hallazgos como vulnerabilidades. La API (`api/scan.py`) y el CLI (`cli.py`) llaman a ese
mismo pipeline; solo difieren en transporte y en persistencia.

**API.** `POST /api/v1/scan?filename=<nombre>` recibe el lockfile crudo en el cuerpo
(bytes), lo escribe en un archivo temporal con el nombre saneado (`Path(filename).name`,
sin path traversal) y lo parsea con `parse_lockfile`; un `LockfileError` es 400. Requiere
token de proyecto (`get_request_context`, D2-1) y devuelve
`{sbom, results, blocked, stored}`. El SBOM y las decisiones se guardan en la base de
datos (get-or-create de `Project`, `Build` si viene `X-Build-Id`, filas `Sbom` y
`Decision`); si el motor no está inicializado o la BD falla, el escaneo **no** falla:
responde con `stored: false` y deja constancia en el log.

**CLI.** `supply-shield scan <lockfile> [--project P] [--format text|json]` (entry point
`[project.scripts]` en `pyproject.toml`). Carga la política y los analizadores locales
(typosquatting desde las listas y dependency confusion desde `config/projects.yaml`; si
falta una fuente, advierte y sigue sin ese detector). Imprime la decisión por paquete y
el SBOM; exit 0 si nada bloquea, 1 si algo queda BLOCK, 2 si el lockfile no existe o no
parsea.

**Analizadores incluidos.** Solo los que no necesitan metadata upstream ni OSV (esos
llegan con D2-2/D3-3): typosquatting y dependency confusion. El pipeline recibe la lista
de analizadores por inyección, así que cuando lleguen se agregan sin tocar API ni CLI.

## Alternativas consideradas

- **CLI que llama a la API en vez de evaluar localmente**: exigiría un servidor corriendo
  para usar el escaneo en CI local; evaluar localmente con el mismo pipeline da el mismo
  resultado sin esa dependencia.
- **Persistencia obligatoria (fallar si la BD está caída)**: un escaneo es de solo
  lectura para el proxy; tumbarlo por la BD escondería hallazgos. Se persiste
  best-effort y se marca `stored`.
- **Aceptar el lockfile como JSON con nombre y contenido**: el cuerpo crudo + `filename`
  es lo que un CI puede subir directo (`curl --data-binary @uv.lock`) sin construir JSON.

## Consecuencias

- La decisión de un paquete escaneado es exactamente la que tomaría el proxy, porque
  ambos usan `policy.evaluate` con la misma política y los mismos analizadores.
- Fácil de extender: nuevos detectores se registran en `app.state.analyzers` /
  `build_analyzers` y entran solos en API y CLI.
- El SBOM generado incluye los hallazgos como `vulnerabilities`, así que el artefacto
  sirve para auditoría (NTIA) y para el re-escaneo periódico (D3-8).
- Trabajo pendiente: con D3-3 (OSV) y los parsers de metadata de registro llegarán los
  analizadores de vulnerabilidades y heurísticas al escaneo.
