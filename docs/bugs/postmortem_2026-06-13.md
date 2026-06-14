# Postmortem 2026-06-13 — Crisis de toolchain en sesión pre-lanzamiento

**Fecha**: 13 de junio 2026
**Severidad**: Alta (multiple production crashes + regresion silenciosa)
**Duracion**: ~6 horas de session activa
**Estado**: Resuelto. Todos los incidentes cerrados con commits + documentacion.

## TL;DR

Durante una session de prep pre-mundial (lanzamiento Tequio antes que termine el Mundial 2026), el toolchain del agente causo cuatro incidentes distintos en prod, cada uno con su propio mecanismo de falla. Tres se resolvieron rapido. El cuarto (regresion silenciosa que pasaba HTTP 200) tomo tres iteraciones porque el bug estaba en el flujo de upload, no en el codigo.

## Cronologia

### Incidente 1 - Null bytes tumbaron api/data.js (HTTP 500 FUNCTION_INVOCATION_FAILED)

**Sintoma**: prod devolvio HTTP 500 en `/api/data?vista=*`. Vercel runtime logs mostraban `FUNCTION_INVOCATION_FAILED` sin stack trace util.

**Causa raiz**: el tool `Edit` del agente inyecta null bytes (`\x00`) silenciosamente en archivos >200 lineas. `api/data.js` (4471 bytes de null bytes), `ai-proxy.js` (320), `asistente-ia.html` (330). Vercel parsea hasta el primer null byte y muere.

**Resolucion**: commit `556b66e` — `tr -d '\000'` via Python heredoc sobre los tres archivos. ~5 min downtime.

**Leccion**: el tool Edit NO se debe usar en archivos grandes. Workaround obligatorio = Python heredoc. Documentado en `scripts/README.md` Phase 2.

---

### Incidente 2 - Embeddings backfill silent exit (procesados=0)

**Sintoma**: workflow `embeddings-backfill-e5.yml` reporto verde en los 10 shards pero `chunks_embedded` no avanzo de ~585k.

**Causa raiz**: dos bugs concurrentes:
1. **null bytes en script**: `scripts/backfill_embeddings_e5.py` tenia null bytes que cortaban la entrada a `main()`. El script no imprimia nada, ARMADURA v1 (assert SUMMARY line) lo cacho.
2. **race condition con cursor-less + shard filter**: al fixear (1), el script avanzaba pero ~90% de cada fetch eran rows de otros shards (descartados en Python), efectivamente bottleneck.

**Resolucion**:
- (1) reescribir script con Python heredoc (sin null bytes).
- (2) commit `c57b589` — Opcion B: shard por rango de id + cursor. 10x speedup esperado.
- ARMADURA v2: requiere `exit_reason=queue_empty` para verde (no solo "termino sin error").

**Leccion**: status verde de CI debe significar "datos escritos correctamente", no "proceso termino". La distincion es muy facil de perder.

---

### Incidente 3 - 4 discrepancias de UX que la usuaria detecto antes que yo

Durante prueba del usuario en celular:
- WhatsApp preview mostraba "9,101 leyes" cuando la app servia 9,618 → **OG image stale**.
- Panel Camara de Diputados mostraba 600 cuando el home decia 500 → **table tenia titulares + suplentes (1000 rows total)**.
- Panel Senado mostraba 270 cuando oficialmente son 128 → **138 rows MORENA garbage de un scraper viejo**.
- Votaciones mostraba "264/264 aprobadas" → **scraper SITL hardcodeaba 'aprobada' pero SITL no publica resultado en HTML, solo conteos**.

**Resolucion**: 
- OG image: `scripts/generate_og_image.py` nuevo + workflow weekly + workflow_run trigger.
- Diputados: DELETE de los suplentes que entraron como rows separadas + CHECK constraint.
- Senado: DELETE de las 138 garbage MORENA con `tipo_eleccion IS NULL`.
- Votaciones: NULL todos los 264 resultados (Cero Invencion) + frontend que deriva mayoria simple SI>NO + asterisco/tooltip + migracion SQL con trigger.

**Leccion clave del usuario**: "no bro debemos estar listos antes que termine el mundial no despues!" — corte el reflejo de marcar como `[POST-MUNDIAL]` y fixear con datos limpios HOY.

---

### Incidente 4 - REGRESION SILENCIOSA via stale-file upload (commit 9807b52)

**El peor**: HTTP 200 en commit + URL valida + push exitoso, pero el archivo subido revirtio una fix previa Y no aplico la fix nueva. **Doble regresion sin ningun warning**.

**Causa raiz**: en una sola llamada bash con tres pasos:
```bash
cp index.html /outputs/index.html              # 1: archivo viejo
python3 <<'PYEOF'                              # 2: SyntaxError silencioso
... f'nb: {data.count(b"\x00")}'               #    \x00 en f-string crashea
PYEOF
curl -X PUT ... base64(archivo) ...            # 3: sube STALE archivo
```

El paso 2 nunca ejecuto (SyntaxError). El paso 3 subio el archivo del paso 1 (viejo). GitHub respondio HTTP 200 correctamente — desde su perspectiva fue un upload valido.

**Detectado** cuando el usuario reporto "como que no se arreglo. ahora en vez de la X salen estos simbolos ??" — la regresion en UX fue lo unico que delato el bug.

**Resolucion**: 
- commits `781c91c` + `67c062e` aplicaron el fix correcto (verificado con `git show` post-commit).
- Documentado el antipatron en `scripts/README.md` con 5 reglas.

**Leccion**: NUNCA encadenar `cp + python + curl` en una sola llamada. Verificar el needle DESPUES del edit ANTES del upload. f-strings con `\x00` requieren variable intermedia (`NULL_BYTE = b'\x00'`).

---

## Cosas que funcionaron

1. **ARMADURA v2** atrapo el silent exit del embeddings backfill ANTES de que el usuario lo notara.
2. **Cero Invencion como mandato** fuerza buenas decisiones — preferimos mostrar NULL ("EN REVISION") que mentir.
3. **Service Worker version bump** es ritual obligatorio post-fix de HTML. Sin el, el cache aguanta dias.
4. **Usuario probando en celular en paralelo** atrapo bugs que los smoke tests synthetic no veian (OG image cache, badge UX).

## Cosas que estuvieron a punto de fallar pero no

1. La migracion SQL del trigger se rompio con backslash escaping (markdown → editor → `\$\$`). Pivote a dollar-quoting nombrado `$func$` que TAMBIEN se escapo. Solucion final fue solo correr el UPDATE sin trigger. Trigger queda para otro dia.
2. La derivacion mayoria simple para reformas constitucionales (2/3) es matematicamente incorrecta. Para LXVI no parece haber casos visibles (todas las votaciones son aprobadas con SI>>NO), pero es una bomba de tiempo.

## Action items (post-launch)

| Owner | Item | Due |
|---|---|---|
| Tequio | Aplicar trigger SQL en Studio cuando no haya editor markdown interfering | Esta semana |
| Tequio | Investigar 4 senadores extra (PRD/PES legacy) | Post-launch |
| Tequio | Reformas constitucionales en frontend: distinguir 2/3 vs mayoria simple | Pre-eleccion |
| Tequio | Reescribir Edit guard: tooling debe detectar null bytes pre-commit | Continuo |

## Lecciones para el agente

1. **Verificar despues de cada paso destructivo**, no solo al final.
2. **Cuando el usuario dice "no se arreglo"**, NO asumir cache. Re-verificar el codigo en main.
3. **Status verde no es exito de negocio**. Tools que devuelven HTTP 200 pueden estar mintiendo.
4. **Cero Invencion aplica al codigo**: no inventar fixes que no se verificaron en prod.

