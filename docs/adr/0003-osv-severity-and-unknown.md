# 0003. Severidad de vulnerabilidades desde OSV y tratamiento de UNKNOWN

- **Estado**: Aceptado
- **Fecha**: 2026-10-19

## Contexto

Supply-Shield tiene que decidir, antes de entregar un paquete de npm o de PyPI, si ese paquete tiene vulnerabilidades conocidas. Esa decisión la van a usar el motor de evaluación, la política y el proxy, así que la fuente de los datos y la forma de traducirlos a una severidad no pueden quedar implícitas en el cliente HTTP.

Hay tres datos que una fuente puede dar, y no siempre vienen juntos:

- un vector CVSS (v3.1 o v4) con puntaje numérico;
- una severidad cualitativa del ecosistema, como la que publica GitHub en un GHSA (`CRITICAL`, `HIGH`, `MODERATE`, `LOW`);
- nada utilizable.

Inventar un puntaje cuando la fuente no lo da haría que un paquete limpio y un paquete sin información se vean iguales, o que un fallo de la fuente se interprete como "no hay vulnerabilidades".

## Decisión

Usaremos **OSV.dev** como única fuente de vulnerabilidades conocidas y de malware publicado.

- La búsqueda se hace con `POST /v1/querybatch` y el detalle de cada advisory con `GET /v1/vulns/{id}`.
- Si OSV no responde a tiempo o devuelve un error, el detector lanza un error controlado. No devuelve una lista vacía: una lista vacía significa "este paquete no tiene hallazgos", y un corte de red no puede significar eso.
- El hallazgo guarda los alias (CVE, GHSA y el id de OSV) y las versiones que la fuente marca como corregidas.

La severidad de un hallazgo de vulnerabilidad se calcula en este orden, y se detiene en el primer dato usable:

1. **CVSS v3.1** (o v3.0 si v3.1 no viene), a partir del puntaje base del vector.
2. **CVSS v4**, solo cuando no hay vector v3, usando las mismas bandas numéricas.
3. **Severidad del ecosistema** publicada por GHSA, si no hay ningún CVSS. `MODERATE` se guarda como `medium`.
4. **`UNKNOWN`** cuando no hay CVSS ni severidad de ecosistema, o cuando el vector no se puede leer.

Las bandas, las de la escala cualitativa de CVSS v3, son:

| Puntaje base | Severidad |
| --- | --- |
| 9.0 – 10.0 | `critical` |
| 7.0 – 8.9 | `high` |
| 4.0 – 6.9 | `medium` |
| 0.1 – 3.9 | `low` |
| 0.0, o vector ilegible sin otro dato | `unknown` |

`UNKNOWN` no es una severidad baja. Significa que el firewall no pudo calificar el advisory. Qué hacer con ese hallazgo (bloquear, advertir o solo registrarlo) lo decide la política por regla, no el detector. El valor por defecto recomendado para `UNKNOWN` es **advertir**: el paquete se instala, el equipo ve el aviso y puede subir el modo a bloquear cuando tenga datos. No se bloquea por defecto, porque un advisory sin puntaje no demuestra por sí solo un riesgo crítico, y no se deja pasar en silencio, porque entonces la falta de datos desaparecería del registro.

Esta escala no se aplica a malware conocido. Un advisory `MAL-` de OSV es siempre `critical`, aunque traiga o no un CVSS.

## Alternativas consideradas

- **NVD como fuente principal**: cubre CVE, pero no el id de OSV, llega más tarde que los advisory de GitHub y PyPI, y no trae los registros `MAL-` con los que se marca malware de paquetes. Seguiría haciendo falta otra fuente.
- **Solo la API de GitHub Advisory**: cubre GHSA de npm con muy buen detalle, y deja fuera parte de PyPI y el conjunto de malware que OSV ya normaliza. Obliga a mantener dos clientes.
- **Volcado local de OSV como camino principal**: mejora la latencia y sobrevive a una caída de la API, a cambio de servir datos atrasados. Queda como respaldo opcional (D3-10), no como la fuente de la que sale la decisión en condiciones normales.
- **Tratar `UNKNOWN` como `low`, o como `critical`**: lo primero esconde advisories sin puntaje; lo segundo bloquearía instalaciones por falta de datos, no por un riesgo demostrado. La política puede subir ese modo más adelante, por proyecto.

## Consecuencias

- El detector de vulnerabilidades y el de malware comparten el cliente de OSV. El malware no reutiliza la escala CVSS.
- Un corte de OSV no se convierte en un 500 genérico ni en un "paquete limpio". El motor de evaluación aplica el modo de falla de cada regla: las vulnerabilidades dejan pasar con alerta, y el malware y la dependency confusion bloquean.
- Preferir CVSS v3.1 cuando también existe v4 mantiene una sola escala para la política. Un advisory que solo trae v4 se califica con bandas pensadas para v3; el número no significa exactamente lo mismo, y el ADR lo deja explícito para no reinterpretarlo después.
- La caché de hallazgos puede invalidarse por id de advisory porque el hallazgo guarda esos ids. Cuando llegue la copia local de OSV, se usa como respaldo del mismo cálculo, no como otra escala.
