# 0006. Autenticación por proyecto con tokens hasheados en YAML

- **Estado**: Aceptado
- **Fecha**: 2026-10-31

## Contexto

El proxy necesita saber a qué proyecto pertenece cada petición para: aplicar políticas por proyecto, asociar el SBOM al proyecto y build correctos, y aplicar rate limit por token. Los clientes (npm y pip) envían credenciales al registro configurado en `.npmrc` (`registry=` + `_authToken`) o `pip.conf` (`index-url` con usuario y contraseña), lo que se traduce en un header `Authorization` con esquema Bearer o Basic.

## Decisión

Cada proyecto tiene un token de acceso. Los tokens se guardan como **hash pbkdf2-hmac-sha256** (con salt aleatorio y 100 000 iteraciones) en `config/projects.yaml`. La autenticación acepta el token por **Basic o Bearer** auth, verifica contra los hashes, y construye un `RequestContext` con el nombre del proyecto y el `build_id` del header `X-Build-Id`.

El acceso a los tokens se abstrae tras el protocolo `TokenStore`, con implementación `YamlTokenStore`. Cuando llegue la base de datos (D5-5, semana 4) se reemplaza por una implementación sobre PostgreSQL sin tocar el código que la usa.

## Alternativas consideradas

- **JWT/OAuth2**: aporta expiración y scopes, pero exige un emisor de tokens, rotación de claves y más complejidad operativa para el MVP.
- **mTLS (certificados de cliente)**: robusto, pero requiere distribuir y rotar certificados en cada pipeline y máquina, lo que complica el arranque rápido.
- **API key en header custom**: no se integra de forma nativa con npm y pip, que envían credenciales por Basic/Bearer.
- **Hash simple (sha256 sin salt ni iteraciones)**: suficiente para tokens de alta entropía, pero pbkdf2 protege además contra tokens débiles y permite ajustar el coste.

## Consecuencias

- La configuración del cliente es estándar: `.npmrc` con `registry` y `_authToken`, o `pip.conf` con `index-url`.
- El token nunca se registra en logs: las entradas de log registran solo el nombre del proyecto.
- Cambiar o revocar un token requiere editar el YAML y recargar. En la semana 4, esto pasa a la base de datos y se gestiona por la API de administración (D4-5). Ver [ADR 0010](0010-project-tokens-admin-api.md): la API ya crea y revoca tokens, pero la autenticación todavía no los lee.
- La verificación recorre todos los proyectos (O(n) con pbkdf2 por cada uno); con pocos proyectos es despreciable, pero no escala a miles. La versión con base de datos indexará por hash.
