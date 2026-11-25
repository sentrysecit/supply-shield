# 0011. Escaneo de lockfiles por API y CLI con un pipeline compartido (D5-6)

- **Estado**: Aceptado (revisado en PR #63)
- **Fecha**: 2026-11-11 (actualizado 2026-11-14 tras review)

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
(bytes) y lo escribe en un archivo temporal de **nombre fijo**; el `filename` solo elige
el parser, validado contra un allowlist estricto (`package-lock*.json`, `pnpm-lock.yaml`,
`requirements.txt`, `poetry.lock`, `uv.lock`) — nunca entra en una ruta (path injection,
detectado por CodeQL). Un nombre no soportado o un `LockfileError` es 400. Requiere
token de proyecto (`get_request_context`, D2-1) y devuelve
`{sbom, results, blocked, stored}`. Un `X-Build-Id` de más de 255 caracteres es 400
(las columnas `Build.build_id`/`Sbom.build_id` son `String(255)`). El SBOM y las decisiones
se guardan en la base de datos (get-or-create de `Project`, `Build` si viene `X-Build-Id`,
filas `Sbom` y `Decision`); la persistencia es **best-effort de verdad**: si el motor no
está inicializado *o cualquier error de sesión ocurre* (BD caída, columna desbordada…),
el escaneo **no** falla: responde 200 con `stored: false` y deja constancia en el log.

**CLI.** `supply-shield scan <lockfile> [--project P] [--format text|json]` (entry point
`[project.scripts]` en `pyproject.toml`). Usa la **misma política efectiva** que el
servidor: carga la base del archivo y, si la base de datos es alcanzable (con timeout de
5 s), suma las excepciones vigentes aprobadas por la API de administración vía
`PolicyHolder` — así una excepción aprobada también aplica al escaneo en CI. Imprime la
decisión por paquete y el SBOM; exit 0 si nada bloquea, 1 si algo queda BLOCK, 2 si el
lockfile no existe o no parsea.

**Analizadores incluidos.** Solo typosquatting. **Dependency confusion queda fuera del
escaneo a propósito**: se detecta en el proxy en el momento de la resolución (nombre
interno pedido al registro público); un lockfile ya resolvió sus URLs y los parsers no
conservan `resolved`, así que marcar nombres internos ahí era falso positivo (visto en
la review de PR #63). Queda como trabajo pendiente: conservar `resolved` en los parsers
para distinguir origen público/privado. El pipeline recibe la lista de analizadores por
inyección, así que cuando lleguen OSV (D3-3) y las heurísticas con metadata les basta
agregarlos.

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

- La decisión de un paquete escaneado es la que tomaría el proxy: misma política efectiva
  (archivo + excepciones de la BD) y `policy.evaluate`.
- El CLI y la API no divergen en política aunque haya excepciones aprobadas por admin.
- Fácil de extender: nuevos detectores se registran en `app.state.analyzers` /
  `build_analyzers` y entran solos en API y CLI.
- El SBOM generado incluye los hallazgos como `vulnerabilities`, así que el artefacto
  sirve para auditoría (NTIA) y para el re-escaneo periódico (D3-8).
- Trabajo pendiente: con D3-3 (OSV) y los parsers de metadata de registro llegarán los
  analizadores de vulnerabilidades y heurísticas al escaneo.
