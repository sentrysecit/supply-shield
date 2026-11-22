# 0012. Umbral de bloqueo del typosquatting elegido con una medición sobre ataques reales

- **Estado**: Propuesto
- **Fecha**: 2026-11-13

## Contexto

El [ADR 0008](0008-typosquatting-detector.md) dejó los pesos y los umbrales del detector como provisionales: bloquea desde 0,85 (`high`), con `critical` en 0,95, `medium` en 0,70 y `low` en 0,60. D4-6 pide medir precisión, recall y F1 con un dataset etiquetado y elegir el umbral por defecto con base en esos resultados.

No había un dataset listo:

- **Backstabber's Knife Collection** solo se entrega a pedido.
- **El dataset de Datadog** no dice qué paquetes son typosquats.
- **OpenSSF malicious-packages** tampoco clasifica por tipo de ataque, pero muchos avisos dicen en el texto que el nombre es un typosquat.

## Decisión

**Se mantienen los umbrales (0,60 / 0,70 / 0,85 / 0,95) y los pesos.** La medición está en [`docs/results/typosquatting.md`](../results/typosquatting.md) y se repite con `eval/typosquatting/`.

**Cómo se mide.** El dataset tiene typosquats y paquetes legítimos.

- **Typosquats:** paquetes de OpenSSF cuyo aviso dice "typosquat", con el paquete imitado revisado a mano en `eval/typosquatting/labels.csv`. Los que no imitan a un paquete concreto no cuentan.
- **Legítimos:** las mismas fuentes que `data/popular/` después del puesto 5 000, más una muestra reproducible de todo el registro. Se excluye cualquier nombre que OpenSSF o Datadog marquen como malicioso.

Las fuentes están fijadas a commits y versiones. El detector se mide tal como corre en el servicio.

**Por qué 0,85.**

- **El F1 es plano entre 0,83 y 0,95**, de 18,2 % a 18,6 % con ambos ecosistemas. El máximo (0,86 a 0,90) supera a 0,85 por un typosquat y 17 legítimos, menos que el ruido de las etiquetas.
- **Con F1 empatado se prefiere el recall**, y 0,85 es el umbral que más typosquats bloquea dentro de ese rango. Un typosquat que pasa ejecuta código en la máquina que lo instala; un legítimo bloqueado se libera con una excepción o la lista de permitidos.
- **Bajar a 0,80** bloquea 16 typosquats más y 325 legítimos más.
- **Subir a 0,95** deja pasar 12 typosquats de una edición para ahorrar 107 legítimos.

**Resultado con 0,85.**

| Medida | Valor |
| --- | --- |
| Recall (503 typosquats) | 19 % |
| Recall en alcance (paquete imitado en la lista) | 30 % (npm 6 %, PyPI 54 %) |
| FP en la lista popular | 0 de 10 000 |
| FP en los puestos 5 001 a ~15 000 | 1,9 % |
| FP en una muestra al azar del registro | 0,5 % |

**El peso del número final (`numbered_variant`) no sube.** El ADR 0008 lo dejó como pregunta abierta. Bloquear con él detectaría 2 typosquats y bloquearía 74 legítimos.

## Alternativas consideradas

- **El umbral con mejor F1 (0,90):** cambia un typosquat por 17 legítimos, una diferencia que el dataset no puede resolver. Ante el empate se prefirió el recall.
- **Un umbral por ecosistema** (0,76 en npm, 0,95 en PyPI): el de npm sale de 10 a 23 aciertos y no es estable. Además, agregaría dos variables de configuración sin una ganancia medible.
- **Recalibrar pesos con este dataset:** los aciertos se concentran en pocas campañas, como 34 imitaciones de `requests` y 26 de `chai-as-promised`. Ajustar pesos a ellas sería sobreajustar. Los cambios de técnica que sí sugiere la medición van como trabajo pendiente.
- **Medir contra todo el malware de OpenSSF:** la mayoría no imita a ningún paquete (spam, *dependency confusion*, versiones comprometidas). Un detector por nombre no está hecho para eso, y el recall no diría nada útil.

## Consecuencias

- El umbral deja de ser provisional y queda respaldado por números que se pueden repetir. Si cambian los pesos, las técnicas o las listas, se vuelve a correr `eval/typosquatting/evaluate.py` y se actualiza el informe.
- **El recall bajo es estructural, no de umbral:**
  - El 35 % de los typosquats imita a un paquete fuera de la lista popular.
  - En npm, 120 de los 156 que imitan a uno de la lista son *combosquats* (`chalk-core`, `typescipt-cli`), que el detector no compara.
  - Quedan como propuestas medibles: comparar las palabras del nombre, separar la señal de quitar o poner un `@scope` (54 de 163 falsos positivos de npm y ningún acierto) y ampliar la lista de comparación. Cada una tiene que medirse con este mismo arnés antes de entrar.
- Los falsos positivos se concentran en paquetes legítimos justo fuera del top 5 000 (1,9 %). La mitigación sigue siendo la lista de permitidos y las excepciones con vencimiento.
- **Los límites de la medición están en el informe:** el sesgo de época de las etiquetas, las campañas repetidas, los negativos presuntos y la revisión manual hecha por el asistente de código. Una persona del equipo debería auditar una muestra de `labels.csv`.
