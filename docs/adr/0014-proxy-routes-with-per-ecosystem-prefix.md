# 0014. Rutas del proxy con prefijo por ecosistema

- **Estado**: Aceptado
- **Fecha**: 2026-10-31

## Contexto

Supply-Shield sirve dos ecosistemas distintos (npm y PyPI) desde una misma aplicación FastAPI. Cada uno habla un protocolo diferente: npm expone el packument en `GET /{paquete}` y los tarballs en `/{paquete}/-/{nombre}-{versión}.tgz`; PyPI expone el índice simple en `GET /simple/{paquete}/` y los archivos en `/packages/...`.

Hay que decidir dónde monta cada ecosistema dentro del proxy, porque eso determina qué URL pone el cliente en `.npmrc` / `pip.conf`, cómo se reescribe el `dist.tarball` del packument para que apunte al proxy y si las rutas de ambos ecosistemas pueden chocar. Es una decisión costosa de revertir porque cambia la configuración de todos los clientes y la forma de reescribir las URLs de descarga.

## Decisión

Cada ecosistema se sirve bajo su propio prefijo:

- **npm** bajo `/npm/...`: el packument en `GET /npm/{paquete}` y los tarballs en `GET /npm/{paquete}/-/{nombre}-{versión}.tgz`.
- **PyPI** bajo `/pypi/...` (p. ej. `/pypi/simple/{paquete}/`).

El setting `SUPPLY_SHIELD_PROXY_BASE_URL` guarda **solo el host y puerto** (p. ej. `http://proxy:8000`), sin prefijo. Cada adaptador le añade su propio prefijo al reescribir las URLs de descarga:

```
dist.tarball original:  https://registry.npmjs.org/express/-/express-4.18.2.tgz
dist.tarball reescrito: {SUPPLY_SHIELD_PROXY_BASE_URL}/npm/express/-/express-4.18.2.tgz
```

Las rutas de cada adaptador se montan con un router FastAPI con `prefix="/npm"` (y `prefix="/pypi"` para PyPI), de modo que un solo setting sirve para ambos y los adaptadores no se acoplan entre sí.

## Alternativas consideradas

- **npm en la raíz, PyPI bajo `/simple/`**: npm se configuraría con la URL más simple (`registry=http://proxy:8000/`), pero npm y PyPI compartirían la raíz. Añadir otro ecosistema (Maven, Go) colisionaría, y la validación de nombres y path traversal de D1-4 queda menos clara sin un prefijo que delimite el ecosistema.
- **Subdominio o puerto por ecosistema** (`npm.proxy:8000`, `pypi.proxy:8000`): separación total sin prefijos en rutas, pero exige más configuración de infraestructura (DNS/hosts o puertos) y es overkill para el MVP.

## Consecuencias

- Añadir un ecosistema nuevo es montar un router con su prefijo, sin tocar los demás.
- Los clientes deben incluir el prefijo en la URL del registro (`registry=http://proxy:8000/npm/`); se documenta una sola vez en la guía de clientes (D2-5).
- `SUPPLY_SHIELD_PROXY_BASE_URL` no lleva prefijo, así que un solo setting sirve para npm y PyPI; cambiarlo reescribe todas las URLs de descarga a la vez.
- El slash final de la URL del registro es responsabilidad del cliente (npm lo exige); el proxy no lo añade.
