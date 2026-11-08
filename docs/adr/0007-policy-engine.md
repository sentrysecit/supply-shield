# 0007. Motor de políticas en YAML aplicado con una función pura

- **Estado**: Aceptado
- **Fecha**: 2026-11-05

## Contexto

Los detectores (vulnerabilidades, malware, typosquatting, heurísticas, dependency confusion) producen hallazgos (`Finding`) con una regla y una severidad. Alguien tiene que convertir esa lista en una sola acción del proxy: permitir, advertir o bloquear. Esa conversión tiene que poder ajustarse por regla y por proyecto, aceptar excepciones temporales justificadas y ser fácil de probar y de auditar. El MVP todavía no tiene base de datos (llega en D5-5) ni API de administración (D4-5).

## Decisión

- La política se escribe en **YAML** (`config/policy.yaml`) y se valida con **Pydantic** (`supply_shield.policy.Policy`). Los modelos rechazan campos desconocidos.
- Cada regla tiene tres campos: `mode` (`block`, `warn` o `log`), `severity_threshold` y `unknown`. Un hallazgo con severidad igual o mayor que el umbral recibe `mode`; uno por debajo solo se registra; uno `UNKNOWN` recibe `unknown`, sin compararse con el umbral (ADR 0003). `log` se traduce a `DecisionAction.ALLOW` y conserva el hallazgo en la decisión.
- Gana el **modo más estricto** entre los hallazgos del paquete.
- La política por defecto vive en el código (`DEFAULT_RULES`) y se repite en `config/policy.yaml`; una prueba exige que coincidan. Lo que trae el YAML, en `rules` y en `projects`, se monta encima **campo por campo**.
- Los ids de regla forman un conjunto cerrado (`supply_shield.policy.Rule`). **Un nombre de regla desconocido en el YAML hace fallar la carga.** Un hallazgo en tiempo de ejecución con una regla desconocida recibe `default_rule` (advertir).
- Las **excepciones** se acotan por paquete (con o sin versión), por advisory o por ambos, y opcionalmente por regla y proyectos. Exigen justificación, autor y fecha de vencimiento (inclusive, en UTC). El motor compara la fecha en cada evaluación, así que una excepción vencida deja de aplicarse sola.
- La política se aplica con una **función pura**, `evaluate(findings, ctx, policy, *, now=None) -> PolicyDecision`, sin E/S ni estado. El reloj se inyecta con `now`.
- Una política inválida **detiene el arranque**. Si el archivo no existe, se aplica la de fábrica.

## Alternativas consideradas

- **Open Policy Agent (Rego) u otro lenguaje de políticas**: más expresivo, pero añade un motor y un lenguaje que el equipo no conoce, y no se necesita lógica arbitraria para tres campos por regla.
- **Dos umbrales por regla (advertir desde X, bloquear desde Y)**: más fino, pero el plan pide un umbral y un modo por regla. Se puede agregar más adelante sin romper los archivos actuales.
- **Aceptar cualquier nombre de regla**: permite agregar detectores sin tocar la política, pero un error de tipeo (`vulnerabilty`) dejaría la regla real con su valor por defecto sin avisar. En un firewall es mejor fallar fuerte.
- **Reemplazar en vez de fusionar** los bloques del YAML: más simple, pero obliga a copiar todos los campos para cambiar uno y facilita perder un `block` al editar.
- **Excepciones sin vencimiento**: tienden a quedarse para siempre. Exigir la fecha obliga a revisarlas.
- **Leer el reloj dentro del motor sin poder inyectarlo**: hace las pruebas de vencimiento dependientes de la fecha real.

## Consecuencias

- El motor es trivial de probar: el módulo `policy/` tiene 100 % de cobertura sin mocks.
- Un detector nuevo tiene que agregar su id a `Rule` y su valor por defecto a `DEFAULT_RULES` y al YAML.
- Hoy cambiar la política o una excepción requiere editar el YAML y reiniciar. D4-5 agregará la recarga en caliente y D5-5 moverá las excepciones a la base de datos; ambos reutilizan los mismos modelos.
- El modo de falla de cada detector (D3-5) se agregará como un campo más de la regla.
- Las versiones de una excepción se comparan como texto, no con PEP 440 ni semver: `1.0` y `1.0.0` son distintas. Si no coinciden, la excepción no se aplica y el paquete sigue bloqueado, que es el lado seguro.
