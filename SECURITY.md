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

- Confirmaremos la recepción de tu reporte cuando nos sea posible.
- Revisaremos la información recibida según su relevancia, disponibilidad y prioridades, y te informaremos sobre los posibles siguientes pasos cuando corresponda.
- Si se requiere una corrección o divulgación, podremos coordinar contigo las acciones pertinentes, de acuerdo con nuestra capacidad y los avances de la revisión. También podremos reconocer tu contribución en el aviso correspondiente, si así lo deseas.

## Alcance

Nos interesa especialmente cualquier fallo que permita:

- saltarse las políticas de bloqueo del proxy,
- alterar los bytes de un artefacto servido por el proxy,
- envenenar la caché de resultados o de metadatos,
- filtrar credenciales o secretos de configuración.
