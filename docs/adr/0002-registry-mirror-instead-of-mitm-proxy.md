# 0002. Registry mirror en lugar de proxy HTTPS con MITM

- **Estado**: Aceptado
- **Fecha**: 2026-10-08

## Contexto

Supply-Shield debe ver todas las descargas de dependencias del equipo para analizarlas y, si hace falta, bloquearlas. Hay dos formas de interponerse entre los clientes (npm, pip) y los registros públicos:

1. Un proxy HTTPS transparente que intercepta el tráfico TLS (*man-in-the-middle*).
2. Un *registry mirror*: un servicio que habla el protocolo del registro y al que los clientes apuntan de forma explícita.

## Decisión

Supply-Shield funcionará como **registry mirror**. Los clientes lo configuran como su registro:

- npm: `registry=` en `.npmrc`.
- pip: `index-url` en `pip.conf`.

El proxy implementa la parte necesaria de la API de cada registro y reenvía las solicitudes permitidas al registro upstream.

## Alternativas consideradas

- **Proxy HTTPS con MITM**: exige instalar una autoridad certificadora propia en cada máquina y pipeline, rompe el *certificate pinning*, amplía la superficie de ataque (la clave de la CA permite suplantar cualquier sitio) e intercepta tráfico que no tiene que ver con dependencias. Es más difícil de desplegar, de auditar y de justificar.

## Consecuencias

- La configuración del cliente es explícita, estándar y soportada oficialmente por npm y pip; no se necesita una CA propia.
- El proxy solo ve tráfico de paquetes, lo que reduce el alcance y el riesgo.
- Hay que implementar un adaptador por ecosistema (`proxy/npm`, `proxy/pypi`), y los metadatos que contienen URLs de descarga deben seguir llevando al cliente a través del proxy.
- Un cliente que no esté configurado se salta el firewall: la cobertura depende de distribuir la configuración (y, si se requiere, de bloquear por red el acceso directo a los registros).
- Los artefactos se sirven sin modificar sus bytes, de modo que los hashes de integridad del upstream siguen siendo válidos.
