# 0011. Cuarentena de los nombres que entran a la lista de paquetes populares

- **Estado**: Propuesto
- **Fecha**: 2026-11-10

## Contexto

El detector de typosquatting nunca marca un nombre que esté en `data/popular/` ([ADR 0008](0008-typosquatting-detector.md)). Eso convierte a la lista en un ancla de confianza: quien logre meter un nombre en ella lo deja exento del detector.

Entrar es posible. Las dos fuentes ordenan por descargas ([ADR 0004](0004-popular-package-sources.md)) y las descargas se pueden inflar. El ADR 0008 lo reconoce como límite: "un nombre popular nunca se marca, aunque sea un typosquat que logró colarse en el top". La única defensa era que una persona revisara el PR semanal, que puede cambiar cientos de nombres: en el último refresco de PyPI entraron 259.

## Decisión

Al refrescar las listas, cada nombre que **entra** se compara con la lista **anterior** (la que ya está en el repo) usando el mismo detector (`score_name`). Si su parecido con un paquete que ya era popular alcanza para bloquear (`high` o `critical`, los umbrales de `Settings`), el nombre queda **en cuarentena**:

- no se escribe en la lista, así que el detector lo sigue marcando como hasta ahora;
- su lugar lo toma el siguiente nombre del ranking, para que la lista conserve 5 000 nombres;
- aparece en el cuerpo del PR semanal y en el resumen del job, con el paquete al que se parece, el puntaje y las técnicas.

Para aprobar un nombre en cuarentena se agrega a `data/typosquatting/allowlist.yaml` con su razón, por PR. El siguiente refresco lo admite. No hay un archivo de aprobaciones aparte: la lista de permitidos ya significa "parecido legítimo", ya exige una razón y `score_name` ya la respeta.

Otras reglas:

- Los nombres que ya estaban en la lista no se revisan. Pares como `request`/`requests` conviven en el top y se quedan.
- Sin lista anterior (primera generación) no hay cuarentena.
- La cuarentena no hace fallar el script: el PR debe abrirse igual, sin los nombres retenidos. Si después de apartarlos no quedan 5 000 nombres, el script falla y no toca los archivos, como con cualquier descarga corta.
- Sin la lista de permitidos el script falla: no puede saber qué entrantes ya se aprobaron.

La lógica es una función pura, `admit_newcomers` (`analyzers/typosquatting/newcomers.py`). `scripts/fetch_popular.py` la usa y escribe el reporte con `--report`.

**Medición.** Al repetir el último refresco real (lista de octubre de 2026 contra el ranking de noviembre) entraron 259 nombres a PyPI y 6 quedaron en cuarentena (2,3 %); en npm la fuente no había cambiado. Los seis son paquetes legítimos que difieren en una letra de otro popular: `pymoo`/`pyomo`, `pystow`/`pystoi`, `pyshex`/`pysher`, `types-boto3-sts`/`types-boto3-ses`, `types-aiobotocore-ecs`/`types-aiobotocore-ec2` y `mypy-boto3-efs`/`mypy-boto3-ecs`. Revisar 6 nombres con su parecido a la vista es un trabajo que sí se hace; revisar 259 sin contexto, no.

## Alternativas consideradas

- **Solo revisión humana del PR** (lo que había): depende de que alguien lea cientos de nombres cada semana y reconozca el que imita a otro.
- **Fallar el job cuando hay un entrante sospechoso**: no se abriría el PR y la lista se quedaría vieja hasta que alguien mire el job. Un atacante podría congelar la lista con un solo nombre.
- **Dejar entrar el nombre y solo avisar en el PR**: si el aviso se pasa por alto, el typosquat queda exento. La cuarentena falla hacia el lado seguro.
- **Un archivo de aprobaciones aparte**: duplicaría el formato, el cargador y el proceso de la lista de permitidos para decir lo mismo.
- **Revisar toda la lista, no solo los entrantes**: los 5 000 nombres ya contienen cientos de parecidos legítimos. Habría que justificar cada uno a mano antes de empezar, sin que eso detenga un ataque nuevo.
- **Comparar con un umbral más bajo (`medium`)**: retendría variantes numeradas y prefijos aislados (`py-requests`, `node-express`), que el detector tampoco bloquea. Más ruido para el revisor sin cerrar un caso que hoy bloquee.

## Consecuencias

- Un nombre que imita a un paquete popular ya no entra a la lista sin una aprobación explícita, con razón y revisada por PR. La debilidad del ADR 0008 deja de depender de la atención de quien revisa.
- Cada refresco pide aprobar a mano unos pocos nombres legítimos. Las familias con muchos paquetes hermanos (`types-boto3-*`, `mypy-boto3-*`) son las que más aparecen.
- Un paquete legítimo en cuarentena sigue fuera de la lista: el detector lo marca y sus propios typosquats no se detectan hasta que se aprueba. Es lo mismo que pasaba antes de que entrara al top.
- Los entrantes no se comparan entre sí. Si un paquete legítimo y su imitación entran en el mismo refresco, ninguno se retiene.
- Lo que ya está en la lista queda como está. Los seis nombres de la medición entraron antes de esta decisión.
- La aprobación llega con un refresco de retraso: la entrada en la lista de permitidos debe estar en `main` antes de que corra el job.
- La cuarentena usa los pesos por defecto del detector. Si D4-6 los vuelve configurables, `admit_newcomers` debe recibirlos.
