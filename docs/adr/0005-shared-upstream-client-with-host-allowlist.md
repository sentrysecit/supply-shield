# 0005. Cliente upstream compartido con allowlist de hosts y descarga verificada en streaming

- **Estado**: Aceptado
- **Fecha**: 2026-10-26

## Contexto

Toda la salida de Supply-Shield hacia los registros públicos pasa por una única pieza: el cliente que habla con `registry.npmjs.org`, `pypi.org` y `files.pythonhosted.org`. De esa pieza dependen tres riesgos concretos:

- **SSRF**: los metadatos que devuelve un registro incluyen URLs de descarga. Si el cliente las siguiera sin validar, un registro malicioso o una respuesta manipulada podría llevar el proxy hacia hosts internos, servicios cloud o cualquier destino arbitrario.
- **Integridad**: el ADR [0002](0002-registry-mirror-instead-of-mitm-proxy.md) exige servir los artefactos byte a byte. Una descarga corrompida o manipulada en tránsito no puede llegar al cliente con hashes que ya no coinciden.
- **Recursos**: sin límite de tamaño ni streaming, un archivo anormalmente grande agota memoria y disco, y los reintentos sin backoff saturen el registro upstream.

Además, los adaptadores npm y PyPI, el motor de evaluación y la generación de SBOM consultarán la red con necesidades similares (pool de conexiones, timeouts, reintentos). Si cada módulo crea su propio cliente, la configuración se dispersa y se pierde la reutilización de conexiones.

## Decisión

Usaremos **un único cliente `httpx.AsyncClient` compartido** para toda la salida hacia upstream, definido en `supply_shield/proxy/upstream.py`, con pool de conexiones, HTTP/2, timeouts y reintentos con backoff exponencial y número máximo de intentos.

- **Allowlist estricta de hosts**: solo se permite conectar a `registry.npmjs.org`, `pypi.org` y `files.pythonhosted.org`. Toda otra URL se rechaza **antes de abrir conexión**, sin importar el esquema, las credenciales o si resuelve a una IP privada.
- **Descarga en streaming, byte a byte**: el artefacto se reenvía al cliente mientras se calcula su hash en el flujo y se compara con el hash esperado. Si no coinciden, la descarga se corta de inmediato.
- **Límite máximo de tamaño por respuesta**: al superarlo se aborta la descarga, sin cargar el archivo completo en memoria.
- **Configuración centralizada**: hosts permitidos, límite de tamaño, timeouts y reintentos se parametrizan desde `Settings` (prefijo `SUPPLY_SHIELD_`) y se documentan en `.env.example`.

## Alternativas consideradas

- **Denylist de IPs privadas**: requiere mantener la lista al día, y es evadible con DNS rebinding (el hostname pasa la lista y resuelve a una IP interna después de la validación). La allowlist positiva falla por defecto: lo que no está declarado, no sale.
- **Cliente `httpx` propio en cada módulo**: más simple a corto plazo, pero dispersa timeouts y reintentos, impide reutilizar el pool de conexiones y multiplica los puntos donde olvidar la validación de hosts.
- **Descargar el archivo completo y hashear en memoria**: simplifica el código, pero un archivo gigante o malicioso puede agotar la RAM del proxy. El streaming permite cortar en el primer byte que rompe el hash o el límite de tamaño.
- **Proxy corporativo o *egress* controlado a nivel de red**: más fuerte que una allowlist en la aplicación, pero exige infraestructura que el MVP no tiene y no elimina la verificación de integridad; queda como defensa en profundidad futura.

## Consecuencias

- Añadir o quitar un registro upstream requiere cambiar la allowlist (configuración o código) y ningún módulo puede salir a la red por su cuenta: todo pasa por este cliente.
- Los adaptadores `proxy/npm` y `proxy/pypi` heredan pool, timeouts y reintentos sin reimplementarlos; programarán contra la interfaz de este módulo y de los modelos compartidos (D4-1), con *mock* mientras esa interfaz no exista.
- La regla de integridad del ADR 0002 queda verificable en un solo punto: si el hash no coincide, se corta antes de que el cliente reciba el resto del archivo.
- El hash se calcula en un solo paso sobre el flujo, por lo que una reanudación o reintento debe recalcularlo desde cero: no se conservan descargas parciales.
- Trabajo pendiente: si más adelante el proxy consulta OSV.dev u otras fuentes, deberán decidir si comparten este cliente o crean uno propio con su propia allowlist.
