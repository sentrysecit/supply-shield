# Supply-Shield: contexto del proyecto

Supply-Shield es un cortafuegos de dependencias. Se interpone entre las herramientas con las que un equipo instala paquetes (`npm`, `pnpm`, `yarn`, `pip`, `uv`, `poetry`) y los registros públicos (npm y PyPI). Cada paquete se evalúa antes de entregarse. Si la política lo permite, el archivo llega intacto. Si no, la instalación se detiene con un motivo legible.

Es un proyecto académico. El sistema es el objeto de estudio: hay que construirlo, medir qué tan bien detecta y comparar ese resultado con las herramientas que el equipo ya usa hoy.

## El problema

Casi todo el software se arma con paquetes de terceros. Esos paquetes se descargan, muchas veces de forma automática, desde registros abiertos. Quien publica en npm o en PyPI puede poner ahí una versión nueva en minutos, y el siguiente `npm install` o `pip install` de un proyecto la trae junto con el resto.

Eso abre varios caminos de ataque que no pasan por una vulnerabilidad del código propio:

- **Una vulnerabilidad ya publicada.** El paquete es legítimo, pero esa versión tiene un fallo conocido. El equipo la instala porque el rango de versiones todavía la acepta.
- **Malware publicado como si fuera un paquete normal.** El registro ya lo marcó, y aun así sigue descargable hasta que alguien lo quite.
- **Un nombre casi igual al de un paquete popular** (*typosquatting*). Una letra cambiada, un guion de más o un carácter que se ve igual bastan para que alguien instale el paquete equivocado.
- **Un nombre interno pedido al registro público** (*dependency confusion*). Si la organización usa un paquete privado llamado como uno que también existe —o puede existir— en npm o PyPI, el instalador puede traer el público.
- **Una versión que acaba de publicarse.** Nadie del equipo la revisó. Varios incidentes de la cadena de suministro entran justo en esa ventana.
- **Código que se ejecuta al instalar.** En npm, los scripts `preinstall`, `install` y `postinstall`. En PyPI, un paquete que solo ofrece el código fuente y puede correr lógica al construirse.

Las herramientas habituales (`npm audit`, `pip-audit`, OSV-Scanner) revisan un proyecto que **ya tiene** sus dependencias resueltas o instaladas. Avisan después. Supply-Shield mira el paquete **en el momento de la descarga**, para que una versión peligrosa no llegue a la máquina ni al pipeline.

## Qué es, en la práctica

Supply-Shield habla el protocolo del registro. El equipo lo configura como si fuera npm o PyPI:

- en npm, pnpm y yarn, con `registry=` en `.npmrc`;
- en pip, uv y poetry, con `index-url` en `pip.conf` o el equivalente de cada herramienta.

A partir de ahí, el cliente no tiene que cambiar la forma de instalar. Pide metadata y archivos como siempre. Supply-Shield consulta el registro real, decide y responde.

Es un **espejo del registro** (*registry mirror*): el cliente apunta ahí a propósito. No intercepta el HTTPS de la máquina ni instala una autoridad de certificación propia. Así solo ve tráfico de paquetes, la configuración es la que npm y pip ya documentan, y un fallo del cortafuegos no se convierte en la capacidad de suplantar cualquier sitio. La cobertura depende de que los entornos de desarrollo y los pipelines usen esa configuración. Quien apunte directo a `registry.npmjs.org` o a `pypi.org` queda fuera.

Cada proyecto del equipo se identifica con un token (Basic o Bearer, que es como envían el secreto `.npmrc` y `pip.conf`). Si el cliente manda un identificador de build, todo lo descargado en esa corrida queda agrupado. Las políticas, las excepciones y el inventario son por proyecto, no globales a ciegas.

## Qué se evalúa en cada paquete

Para cada paquete y versión, Supply-Shield junta estas señales. Ninguna decide sola: la política dice qué hacer con cada una.

**Vulnerabilidades conocidas.** La fuente es [OSV.dev](https://osv.dev), que reúne avisos de npm, PyPI y otras bases (incluidos CVE y GHSA). La severidad sale, en este orden, del vector CVSS v3, de CVSS v4 si no hay v3, o de la severidad cualitativa del aviso de GitHub. Si no hay ningún dato usable, la severidad queda en `UNKNOWN`: no es “riesgo bajo”, es “no se pudo calificar”. Por defecto eso genera una advertencia. Bloquear o callar ese caso lo decide la política.

**Malware conocido.** Los avisos de OSV cuyo identificador empieza por `MAL-` se tratan siempre como críticos, aunque traigan o no un puntaje CVSS.

**Typosquatting.** El nombre se compara con un corpus de paquetes populares de npm y de PyPI. El detector combina parecido de edición (errores de tecleo), confusión de separadores (`-`, `_`, `.`), caracteres que se ven iguales (`rn` y `m`, `0` y `o`, `1` y `l`), un nombre popular con un prefijo o sufijo agregado, y la omisión o el cambio del scope de npm (`@organizacion/paquete`). También cruza ecosistemas: un nombre famoso de npm publicado en PyPI, y al revés. Devuelve un puntaje entre 0 y 1 y el paquete legítimo con el que se confunde. Un paquete que ya está en el top, o en una lista de permitidos, no se marca a sí mismo.

**Versión recién publicada (*cooldown*).** La versión tiene menos de N días en el registro. El umbral es configurable.

**Paquete nuevo.** El proyecto del paquete tiene pocos días de vida y pocas versiones. Sirve para no tratar igual a una librería consolidada y a un nombre que apareció ayer.

**Scripts de instalación.** En npm, el paquete declara `preinstall`, `install` o `postinstall`. En PyPI, solo ofrece el paquete fuente (sdist), sin wheel. Es una señal de que instalar puede ejecutar código, no una prueba de que el paquete sea malicioso.

**Dependency confusion.** Cada proyecto declara sus scopes y nombres internos. Si alguien pide uno de esos nombres al registro público, el hallazgo es crítico.

Si un detector no responde a tiempo, el silencio no cuenta como “paquete limpio”. Cada regla define qué pasa en ese caso. Por defecto, malware conocido y dependency confusion **bloquean** cuando su detector falla (*fail-closed*): el costo de dejar pasar es demasiado alto. Vulnerabilidades y typosquatting **dejan pasar y dejan alerta** (*fail-open*): parar todo el equipo porque OSV no contestó sería peor que registrar la degradación. La decisión queda marcada como tomada en modo degradado.

## Cómo se bloquea

La política es un archivo de reglas. Para cada tipo de hallazgo define un umbral de severidad, qué hacer con `UNKNOWN` y un modo:

- **bloquear:** el paquete no se entrega;
- **advertir:** se entrega, y la advertencia queda registrada;
- **registrar (*audit*):** se entrega y solo queda constancia, sin frenar ni destacar.

Puede haber ajustes por proyecto y excepciones por paquete (`purl`) o por aviso. Cada excepción lleva justificación, autor y fecha de vencimiento. Una excepción vencida deja de aplicar. Crear una excepción nueva desbloquea el paquete en la siguiente petición, sin reiniciar el servicio. La política activa también se puede recargar en caliente.

Un bloqueo ocurre de dos maneras, porque el cliente puede llegar por dos caminos:

1. **En la metadata.** Las versiones bloqueadas se quitan de la lista que ve el instalador. El resolver elige solo otra versión que sí cumple el rango y la política. En npm, si la versión marcada como `latest` queda bloqueada, esa etiqueta pasa a la versión permitida más reciente.
2. **En el archivo.** Si alguien pide directamente el tarball, el wheel o el sdist de una versión bloqueada —por ejemplo fijándola en el lockfile—, la respuesta es **403** con un texto que npm y pip muestran en la terminal: regla, motivo, identificador del aviso y cómo pedir una excepción.

Las respuestas llevan además el encabezado `X-Supply-Shield-Decision`, para que un pipeline pueda leer la decisión sin interpretar el cuerpo.

El archivo, cuando se entrega, es **el mismo byte a byte** que publicó el registro. Supply-Shield no lo reempaqueta ni lo reescribe. Los hashes de integridad que ya traen el lockfile y la metadata siguen siendo válidos. Si el hash calculado durante la descarga no coincide con el esperado, la descarga se corta y ese archivo no se sirve.

## Qué queda registrado

Bloquear es solo la mitad. El equipo necesita saber qué entró, qué se frenó y qué cambió después.

**Inventario (SBOM).** Supply-Shield genera un SBOM en [CycloneDX](https://cyclonedx.org/) 1.6. Cada componente lleva su identificador de paquete (`purl`), sus hashes y, cuando el lockfile lo trae, si la dependencia es directa o transitiva y de quién depende. El inventario incluye las vulnerabilidades que se encontraron. Hay dos orígenes:

- el **lockfile** del proyecto (`package-lock.json`, `pnpm-lock.yaml`, `requirements.txt` con versiones fijadas, `poetry.lock`, `uv.lock`), que se puede escanear por API o con el comando `supply-shield scan` antes de instalar o dentro de la CI;
- el **tráfico real**: cada archivo que el proxy entregó a un proyecto en un build. Después de un `npm ci` con identificador de build, se puede pedir el SBOM de lo que de verdad se descargó.

El escaneo por consola termina con error si algo queda bloqueado, para que la CI falle igual que fallaría un test.

**Memoria.** Decisiones, eventos, excepciones, proyectos y SBOMs se guardan. Un job vuelve a evaluar los inventarios guardados cuando aparecen avisos nuevos. Un paquete que era aceptable la semana pasada puede dejar de serlo sin que nadie vuelva a instalarlo. Ese hallazgo nuevo genera un evento.

**Aviso.** Los bloqueos, las decisiones tomadas en modo degradado y los hallazgos del re-escaneo pueden salir por webhook (JSON genérico, Slack o Discord). Si el webhook falla, el proxy sigue entregando paquetes: la notificación no está en el camino de la instalación.

**Operación.** Hay una API de administración, protegida con un token distinto del de los proyectos, para ver decisiones, consultar SBOMs, administrar excepciones y leer o recargar la política. Hay métricas (latencia, decisiones por regla, aciertos de caché, errores al hablar con el registro) y un registro de auditoría de cada decisión. Ese registro no guarda tokens.

Los resultados de metadata, de detectores y de decisiones se cachean con tiempos de vida distintos, para no consultar OSV ni el registro en cada petición. Más adelante la caché compartida vive en Redis y los archivos ya descargados se guardan en disco, identificados por su hash. Un archivo cuyo hash no coincide nunca se reutiliza desde ahí.

## Qué entra en el MVP

Al terminar, el sistema:

1. Funciona como registro intermedio de **npm** y de **PyPI**, probado con `npm`, `pnpm`, `pip` y `uv`.
2. Evalúa las siete señales de arriba: vulnerabilidades, malware conocido, typosquatting, versión recién publicada, paquete nuevo, scripts de instalación y dependency confusion.
3. Aplica una política con los tres modos, excepciones con vencimiento y una estrategia por regla para cuando el detector falla.
4. Bloquea tanto en la metadata como con un 403 en el archivo directo.
5. Genera SBOMs CycloneDX a partir de lockfiles y del tráfico de cada build, con las vulnerabilidades encontradas.
6. Guarda decisiones, SBOMs y excepciones, y vuelve a escanear los inventarios para avisar de avisos nuevos.
7. Ofrece API de administración, métricas y log de auditoría.
8. Se levanta con Docker Compose, con una guía para configurar los clientes y un ejemplo de GitHub Actions.
9. Deja evidencia medible para el documento del semillero.

Queda fuera, y así se declara como límite del trabajo:

- una interfaz web;
- otros ecosistemas (Maven, Go, Cargo);
- análisis estático o dinámico del código del paquete;
- verificación de firmas o de procedencia (npm provenance, PEP 740);
- políticas de licencias;
- alta disponibilidad o varios nodos.

Tampoco se publican paquetes cebo en npm ni en PyPI reales. Las pruebas de ataque usan registros locales (Verdaccio y un índice PyPI de prueba) que solo existen en el entorno de desarrollo.

## Qué tiene que poder demostrar el documento

El valor del proyecto no es solo que el proxy responda. El documento necesita medidas, hechas en un entorno descrito y con un procedimiento repetible:

- **Latencia.** Cuánto agrega el cortafuegos frente a ir directo al registro, con caché fría, con caché caliente y con más concurrencia. Interesan los percentiles 50, 95 y 99.
- **Calidad del detector de typosquatting.** Precisión, recall y F1 sobre un conjunto etiquetado: casos maliciosos reales (colecciones públicas de paquetes maliciosos) contra paquetes populares y una muestra de nombres legítimos. Con esa curva se elige el umbral por defecto, en lugar de fijarlo a ojo.
- **Comparación con lo que ya existe.** Los mismos proyectos, pasados por `npm audit`, `pip-audit`, OSV-Scanner y Supply-Shield. La tabla responde qué detecta cada uno y, sobre todo, **cuándo**: antes de que el paquete se instale o después.
- **Pentest.** Alguien que no haya escrito el proxy intenta saltarse un bloqueo (mayúsculas, separadores, codificación de la URL, pedir el archivo sin pasar por la metadata), abusar del proxy para llamar a un host interno, envenenar la caché y tirar el servicio. Cada hallazgo se corrige o se justifica.

La demo de cierre recorre un solo guion: un proyecto instala dependencias, el cortafuegos frena un typosquat, una vulnerabilidad crítica y una versión recién publicada, y al final se ven el SBOM y las métricas.

## Decisiones que ya definen el producto

Estas elecciones están cerradas. Cambiarlas implica un registro de decisión nuevo, no reescribir la historia.

- **Espejo de registro, no interceptación de HTTPS.** La cobertura se logra configurando el cliente. A cambio, no hace falta una CA propia y el sistema solo observa tráfico de paquetes.
- **OSV.dev como única fuente de vulnerabilidades y de malware publicado.** Si OSV no responde, eso es un error del detector, no una lista vacía de hallazgos. Una lista vacía significa “este paquete no tiene avisos”.
- **`UNKNOWN` no se disfraza de severidad baja ni de crítica.** La política, no el detector, elige bloquear, advertir o registrar. El recomendado es advertir.
- **El archivo servido no se modifica.** La integridad que ve el cliente es la del registro upstream.
- **Las decisiones de diseño viven en el repositorio**, en `docs/adr/`, escritas en español y revisadas como cualquier otro cambio.

## Estado de hoy

El proyecto está en construcción. Ya existen la aplicación base, los chequeos de salud, los modelos compartidos con los que se va a hablar todo el sistema (paquete, hallazgo, decisión) y una caché en memoria. Todavía no hay un camino que reciba la petición de un paquete, lo evalúe y lo bloquee o lo entregue. El proxy, los detectores y el motor de políticas son el trabajo que sigue.

## Glosario

| Término | Significado en este proyecto |
| --- | --- |
| Registro (*registry*) | El servicio público del que salen los paquetes. Aquí, npm y PyPI. |
| Espejo de registro | Un servicio que habla el mismo protocolo que el registro y al que el cliente apunta de forma explícita. |
| Paquete y versión | La unidad que se evalúa. “Lodash 4.17.20” y “lodash 4.17.21” son decisiones distintas. |
| `purl` | Identificador universal de un paquete: ecosistema, nombre y versión. Las excepciones y el SBOM lo usan para no depender del nombre tal como lo escribió alguien. |
| Hallazgo (*finding*) | Una señal concreta: qué regla disparó, con qué severidad, de qué fuente y contra qué paquete. |
| Aviso (*advisory*) | El informe público de una vulnerabilidad o de un malware. OSV, CVE y GHSA son identificadores de ese informe. |
| Política | Las reglas que convierten hallazgos en permitir, advertir o bloquear. |
| Excepción | Un permiso temporal y justificado para un paquete o un aviso que la política bloquearía. |
| SBOM | Inventario de los componentes de un software, en este proyecto en formato CycloneDX. |
| Lockfile | El archivo que fija las versiones exactas que un proyecto instaló (`package-lock.json`, `uv.lock`, etc.). |
| Cooldown | Espera mínima desde que una versión se publicó hasta que el cortafuegos acepta entregarla. |
| Typosquatting | Publicar un paquete con un nombre fácil de confundir con el de otro, más usado. |
| Dependency confusion | Conseguir que el instalador baje de un registro público un nombre que la organización reserva para uso interno. |
| Fail-open / fail-closed | Qué se hace si el detector no pudo correr: dejar pasar con alerta, o bloquear. |
| Modo degradado | La decisión se tomó sin alguna señal, porque su detector falló. Queda escrita como tal. |
