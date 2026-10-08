# Política de seguridad

## Versiones soportadas

Supply-Shield está en construcción y todavía no tiene versiones estables. Solo se corrigen vulnerabilidades sobre la rama `main`.

## Cómo reportar una vulnerabilidad

**No abras un issue público ni un pull request para reportar un problema de seguridad.**

Repórtalo de forma privada mediante GitHub Security Advisories:

1. Ve a la pestaña **Security** del repositorio.
2. Entra en **Advisories** y pulsa **Report a vulnerability**.
3. Completa el formulario con el mayor detalle posible.

Incluye, si puedes:

- una descripción del problema y su impacto,
- los pasos para reproducirlo o una prueba de concepto,
- la versión o el commit afectado,
- cualquier mitigación que conozcas.

## Qué puedes esperar

- Acuse de recibo en un plazo de 5 días hábiles.
- Una evaluación inicial y los siguientes pasos en un plazo de 10 días hábiles.
- Coordinación contigo sobre la corrección y la divulgación; te daremos crédito en el aviso si lo deseas.

## Alcance

Nos interesa especialmente cualquier fallo que permita:

- saltarse las políticas de bloqueo del proxy,
- alterar los bytes de un artefacto servido por el proxy,
- envenenar la caché de resultados o de metadatos,
- filtrar credenciales o secretos de configuración.
