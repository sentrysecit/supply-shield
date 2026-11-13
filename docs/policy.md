# Política de bloqueo

La política decide qué hace el proxy con cada versión de paquete a partir de los hallazgos de los detectores: **bloquearla**, **advertir** o **solo registrarla**. Se escribe en YAML ([`config/policy.yaml`](../config/policy.yaml)), se valida con Pydantic al arrancar y se aplica con una función pura (`supply_shield.policy.evaluate`). Las decisiones de diseño están en el [ADR 0007](adr/0007-policy-engine.md).

## Cómo se decide

Para cada hallazgo (`Finding`) que llega de un detector:

1. **Excepción vigente.** Si una excepción vigente cubre el hallazgo, el hallazgo solo se registra y la decisión explica qué excepción se aplicó, quién la pidió y hasta cuándo rige.
2. **Regla efectiva.** Se toma la configuración de la regla del hallazgo (`finding.rule`), con los ajustes del proyecto que hace la petición encima. Si la política no conoce la regla, se usa `default_rule`.
3. **Modo.**
   - Severidad `unknown`: se aplica el modo `unknown` de la regla. No se compara con el umbral ([ADR 0003](adr/0003-osv-severity-and-unknown.md)).
   - Severidad igual o mayor que `severity_threshold`: se aplica `mode`.
   - Severidad por debajo del umbral: solo se registra.

La decisión del paquete es la del hallazgo **más estricto**: basta un hallazgo en `block` para bloquear, aunque los demás solo adviertan.

| Modo de la regla | Acción del proxy (`DecisionAction`) | Qué ve el usuario |
| --- | --- | --- |
| `block` | `block` | La versión desaparece de la metadata; pedirla directo da 403 con el motivo. |
| `warn` | `warn` | El paquete se instala; la advertencia queda en el log y en la decisión. |
| `log` | `allow` | El paquete se instala sin aviso; el hallazgo queda en la decisión para auditoría. |

`PolicyDecision.findings` siempre trae **todos** los hallazgos, también los registrados y los exceptuados. `PolicyDecision.reasons` trae una línea legible por cada hallazgo que bloquea o advierte (primero los bloqueos) y por cada excepción aplicada. Esas líneas son las que el proxy muestra en el 403 (D1-3). Si una excepción venció, el motivo lo dice, para que el equipo entienda por qué su paquete volvió a bloquearse.

Las severidades se ordenan `low < medium < high < critical`.

## Política por defecto

Es la misma en el código (`DEFAULT_RULES`) y en `config/policy.yaml`; una prueba falla si se separan. Si el archivo no existe (por ejemplo, en la imagen Docker sin un volumen montado), se aplica esta misma política.

| Regla | Detector | `mode` | `severity_threshold` | `unknown` |
| --- | --- | --- | --- | --- |
| `vulnerability` | D3-3, OSV.dev | `block` | `critical` | `warn` |
| `known_malware` | D3-4, avisos `MAL-` | `block` | `low` | `block` |
| `typosquatting` | D4-4 ([cómo puntúa](typosquatting.md#puntaje-y-severidad)) | `block` | `high` | `warn` |
| `dependency_confusion` | D5-4 | `block` | `low` | `block` |
| `cooldown` | D5-2 | `block` | `low` | `block` |
| `install_scripts` | D5-2 | `warn` | `low` | `warn` |
| `new_package` | D5-2 | `warn` | `low` | `warn` |
| *(regla desconocida)* `default_rule` | — | `warn` | `low` | `warn` |

### Por qué estos valores

- **`vulnerability`: bloquear solo las críticas.** El MVP promete bloquear "paquetes con vulnerabilidades críticas". Las altas y medias son frecuentes en dependencias transitivas y muchas no son explotables en el contexto del proyecto; bloquearlas por defecto rompería builds sin un riesgo demostrado y empujaría a los equipos a saltarse el proxy. No desaparecen: quedan en la decisión y en el SBOM, y un proyecto puede bajar el umbral a `high`. `unknown` advierte, como recomienda el ADR 0003: un advisory sin puntaje no prueba un riesgo crítico, pero tampoco puede pasar en silencio.
- **`known_malware`: bloquear siempre.** Un aviso `MAL-` describe un paquete publicado con intención maliciosa; no hay versión "poco grave". El umbral `low` y `unknown: block` hacen que cualquier hallazgo de esta regla bloquee, aunque el detector cambie cómo calcula la severidad.
- **`typosquatting`: bloquear desde `high`.** El detector (D4-4) traduce su puntaje de similitud a una severidad ([tabla](typosquatting.md#puntaje-y-severidad)). Los parecidos fuertes bloquean; los débiles solo se registran, porque un falso positivo bloquea un paquete legítimo. El umbral se recalibra con la medición de falsos positivos de D4-6. `unknown` advierte porque un puntaje que no se pudo calcular no es evidencia de ataque.
- **`dependency_confusion`: bloquear siempre.** Pedir al registro público un nombre que la organización declaró interno es exactamente el ataque; D5-4 exige que se bloquee siempre.
- **`cooldown`: bloquear.** Las versiones recién publicadas son la ventana en la que se cuelan las cuentas comprometidas (`event-stream`, `ua-parser-js`) antes de que exista un advisory. Bloquearlas unos días deja que el ecosistema las revise. Si un equipo necesita un parche urgente, pide una excepción por paquete y versión.
- **`install_scripts` y `new_package`: advertir.** Son señales de riesgo, no de ataque: muchos paquetes legítimos (`esbuild`, `node-gyp`, `sharp`) usan scripts de instalación, y todo paquete fue nuevo alguna vez. Bloquearlos por defecto daría demasiados falsos positivos.
- **`default_rule`: advertir.** Si llega un hallazgo de un detector que la política aún no conoce, no se bloquea por una regla que nadie configuró, pero tampoco se ignora.

## Ajustes por proyecto

`projects` cambia campos de una regla solo para un proyecto (el nombre es el de [`config/projects.yaml`](../config/projects.yaml)). Lo que no se escribe se hereda de la regla global.

```yaml
projects:
  backend:
    rules:
      vulnerability:
        severity_threshold: high   # backend también bloquea las altas
  frontend:
    rules:
      install_scripts:
        mode: log
```

Lo mismo vale para `rules`: en el YAML basta con escribir los campos que cambian; el resto toma el valor de fábrica.

## Excepciones

Una excepción deja pasar hallazgos concretos durante un tiempo limitado.

```yaml
exceptions:
  - advisory: GHSA-35jh-r3h4-6jhm
    package: {ecosystem: npm, name: lodash, version: "4.17.20"}
    rule: vulnerability          # opcional
    projects: [frontend]         # opcional; vacío = todos los proyectos
    justification: "La función afectada (template) no se usa; migración en JIRA-123."
    author: "equipo-seguridad"
    expires: 2026-12-31
```

| Campo | Obligatorio | Significado |
| --- | --- | --- |
| `package` | uno de los dos | Ecosistema, nombre y, opcionalmente, versión. Sin versión cubre todas. El nombre se normaliza igual que en el proxy (`Django_Foo` = `django-foo` en PyPI). |
| `advisory` | uno de los dos | Id del advisory (CVE, GHSA, PYSEC, MAL…). Se compara sin distinguir mayúsculas contra `finding.advisory_ids`. |
| `rule` | no | Limita la excepción a una regla. |
| `projects` | no | Limita la excepción a esos proyectos. |
| `justification` | sí | Por qué es aceptable el riesgo. |
| `author` | sí | Quién la pidió o aprobó. |
| `expires` | sí | Último día en que rige, inclusive, contado en **UTC**. |

Si se dan `package` y `advisory`, deben coincidir los dos. Una excepción solo cubre los hallazgos que coinciden: si el mismo paquete tiene otro advisory, ese sigue bloqueando.

**Al vencer, deja de aplicarse sola**, sin reiniciar ni editar el archivo: el motor compara la fecha en cada evaluación.

Las excepciones también se pueden crear, listar y revocar con la [API de administración](admin-api.md#excepciones), sin tocar el archivo ni reiniciar. Se guardan en la base, se suman a las del archivo y se aplican desde la petición siguiente. Esa vía exige además que la excepción no sea más amplia de lo necesario (ver la tabla en esa guía).

Recomendaciones:

- Acota siempre por `advisory` o por versión. Una excepción con solo el nombre del paquete cubre también el malware que aparezca mañana en él.
- Usa vencimientos cortos (semanas, no años) y renueva con una nueva justificación.
- Escribe las versiones entre comillas: `1.0` sin comillas es un número en YAML y la carga falla.

## Validación

La política se valida al arrancar. **Si el archivo existe y es inválido, el servicio no arranca**: es preferible no servir paquetes a servirlos con reglas que nadie escribió. Se rechazan, entre otros:

- nombres de regla que no existen (`vulnerabilty`), en `rules`, en `projects` y en `exceptions`;
- campos desconocidos (`modo: block`);
- modos o severidades inválidos, y `severity_threshold: unknown`;
- excepciones sin `package` ni `advisory`, o sin `justification`, `author` o `expires`;
- una `version` distinta de `1`.

La ruta del archivo se configura con `SUPPLY_SHIELD_POLICY_FILE` (por defecto `config/policy.yaml`).

### Recargar sin reiniciar

Después de editar el archivo, `POST /api/v1/admin/policy/reload` ([API de administración](admin-api.md#política)) lo vuelve a leer y validar. Si es inválido o no existe, responde 422 con el error y **la política anterior sigue activa**: un error de edición no deja al proxy sin reglas ni lo tumba. `GET /api/v1/admin/policy` muestra el sha256 del archivo cargado.

## Uso desde el código

```python
from supply_shield.policy import evaluate, load_policy

policy = load_policy(Path("config/policy.yaml"))
decision = evaluate(findings, context, policy)  # PolicyDecision
decision = evaluate(findings, context, policy, now=t)  # determinista
```

- `evaluate` no hace E/S ni guarda estado. La única entrada implícita es el reloj; pasa `now` (con zona horaria) en pruebas.
- `assess` devuelve el detalle por hallazgo (`FindingOutcome`: regla efectiva, modo, excepción aplicada o vencida), útil para métricas por regla y modo (D3-7) y para la auditoría.
- La aplicación carga la política al arrancar en `app.state.policy_holder` (`PolicyHolder`). La política efectiva, con las excepciones de la API, es `app.state.policy_holder.current`; `revision` sube con cada cambio y debe formar parte de la clave de las decisiones cacheadas (D3-6).
- Un detector nuevo agrega su id a `supply_shield.policy.Rule` y su valor por defecto a `DEFAULT_RULES` (y a `config/policy.yaml`).

## Pendiente en otras tareas

- **D3-5**: qué hacer cuando un detector falla (*fail-open* / *fail-closed*), configurable por regla en esta misma política, y la marca de decisión degradada.
- **D3-6**: el motor de evaluación llama a `evaluate` con los hallazgos de todos los detectores y cachea la decisión.
