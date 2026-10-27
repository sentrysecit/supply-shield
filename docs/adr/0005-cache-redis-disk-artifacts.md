# 0005. Caché Redis para metadatos/hallazgos y disco para artefactos

- **Estado**: Aceptado
- **Fecha**: 2026-10-27

## Contexto

D5-7 requiere implementar la interfaz ``Cache`` de D3-2 sobre Redis, y guardar artefactos descargados en disco identificados por hash. Hay que decidir dónde vive cada tipo de dato:

- Metadatos de registro, hallazgos de detectores y decisiones de política: necesitan acceso rápido, TTL por tipo de dato e invalidación por paquete/advisory.
- Artefactos (tarballs, wheels, sdists): son binarios potencialmente grandes, inmutables (identificados por hash), y deben sobrevivir a reinicios del proxy.

## Decisión

**Redis** para la caché de metadatos, hallazgos y decisiones (implementación de ``Cache`` en ``cache/redis.py``). Proporciona TTL nativo, invalidación por conjunto y persistencia entre reinicios con configuración de durabilidad.

**Disco local** para artefactos descargados (implementación en ``cache/artifacts.py``). Almacén direccionado por contenido: ruta derivada del hash, verificación de integridad antes de servir, y operación atómica (archivo temporal + rename). Nunca se sirve un archivo cuyo hash no coincida.

## Alternativas consideradas

- **Redis para todo**: descartado porque Redis no es eficiente para binarios grandes y no ofrece verificación de hash integrada. Además, el requisito dice que los artefactos van en disco.
- **S3 para artefactos**: descartado porque introduce una dependencia externa y costos; el MVP no requiere almacenamiento en la nube. El disco local satisface el criterio "un segundo npm ci no vuelve a descargar".
- **Solo memoria (D3-2)**: insuficiente porque no sobrevive a reinicios del proxy, que es un criterio de aceptación de D5-7.

## Consecuencias

Positivas: Redis da TTL automático y persistencia entre reinicios; disco da almacenamiento barato y verificación de hash para artefactos grandes.
Negativas: dos sistemas de almacenamiento que operar y monitorear; Redis debe configurarse con persistencia (AOF/RDB) para no perder caché en reinicio.
Riesgos: si Redis se reinicia sin persistencia, la caché de metadatos se vacía (aceptable: se repopula desde upstream). Los artefactos en disco persisten independientemente.
