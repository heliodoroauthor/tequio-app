## Lecciones de Phase 2 (12-jun-2026) — issue #2

### Bug del tooling Edit: null bytes en archivos grandes

Durante Phase 2 (migración embeddings Gemini → e5-base local) el tooling de
edición de archivos del agente inyectó **null bytes** (`\x00`) en archivos
grandes (>200 líneas) de forma silenciosa. Síntoma: el archivo se ve OK al
abrirlo, `wc -l` reporta líneas correctas, pero `python3 -m py_compile` o
`node --check` fallan con sintaxis cortada en mitad de línea. En CI el
script entra a `main()`, ejecuta hasta cierto punto, y **exit 0 sin output**
— el workflow lo marca verde aunque escribió 0 filas.

**Workaround obligatorio**: archivos grandes (>100 líneas o >5KB) **NUNCA** se
parchean con el tool Edit directo. Patrones aceptables:

```bash
# A) Python en heredoc (la forma usada hoy)
python3 <<'PYEOF'
path = '/.../archivo.py'
with open(path) as f: src = f.read()
src = src.replace('VIEJO', 'NUEVO')
with open(path, 'w') as f: f.write(src)
PYEOF

# B) sed para reemplazos simples
sed -i 's/VIEJO/NUEVO/g' archivo.py
```

**Verificación post-edit obligatoria**:

```bash
# Detectar null bytes
grep -c $'\x00' archivo.py
# Validar sintaxis
python3 -m py_compile archivo.py   # o node --check archivo.js
```

Si aparecen null bytes:

```bash
tr -d '\000' < archivo.py > /tmp/clean.py && mv /tmp/clean.py archivo.py
```

### Armadura para "silent exit" en CI

El bug anterior reveló un agujero más amplio: 9 batches del workflow
reportaron "success" después de escribir CERO filas. El script tenía un
check interno (`if processed > 0 and embedded == 0: sys.exit(5)`) pero nunca
se ejecutó porque `main()` jamás corrió. Status verde mentiroso.

**Regla**: status verde debe significar **datos escritos**, no
"proceso terminado". Workflows que escriben datos necesitan:

1. **Script imprime línea-resumen al final** (`=== SUMMARY ... ===`
   con `embedded=N processed=M skipped=K`).
2. **Workflow asserta sobre esa línea**: si no aparece, fallar; si
   `processed > 0 AND embedded == 0`, fallar también (defensa redundante).

Ver `embeddings-backfill-e5.yml` y `embeddings-trickle-e5.yml` para la
implementación canónica (step "ARMADURA - Assert SUMMARY printed").

### Prefijos asimétricos de e5

`intfloat/multilingual-e5-base` requiere prefijos distintos según el rol:

- **Corpus** (chunks, leyes, senadores almacenados en `embedding`):
  `passage: <texto>`
- **Queries** (preguntas del usuario en runtime):
  `query: <texto>`

Sin esos prefijos la calidad degrada **en silencio** (no falla, solo
recupera peor). El backfill (`backfill_embeddings_e5.py`) usa
`passage:`. El embedder de queries (cuando el servidor propio esté
arriba) **DEBE** usar `query:`. Ver issue #2 checklist día-servidor.

### Bug del "stale-file upload" (13-jun-2026) — antipatron mortal

**Sintoma**: commit a GitHub via API devuelve HTTP 200 + URL de commit,
pero el archivo subido contiene el ESTADO ANTERIOR del repo, no la edicion
intentada. Resultado: regresion silenciosa que revierte commits previos
SIN warning.

**Caso concreto**: commit `9807b52` intentaba arreglar el icono X rojo de
votaciones. Resultado real: revirtio el commit `37aa77d` previo Y no aplico
el fix de icono. Doble regresion. HTTP 200 OK en ambos pasos.

**Causa raiz**: secuencia rota de comandos en una sola llamada bash:

```bash
cp index.html /outputs/index.html              # paso 1: copia archivo stale
python3 <<'PYEOF'                              # paso 2: intenta edit
... old_string = """..."""
... new_string = """..."""
... print(f'patched. nb: {open(p,"rb").read().count(b"\x00")}')  # SyntaxError!
PYEOF
curl -X PUT ... base64(archivo) ...            # paso 3: sube archivo stale
```

Lo que paso: el `python3 <<EOF` tuvo SyntaxError (backslash en f-string).
El bloque python NUNCA ejecuto. El archivo en `/outputs/index.html` quedo
exactamente como el `cp` lo dejo — STALE. El curl PUT subio ese stale,
**recibiendo HTTP 200**.

**Reglas para evitar repetir**:

1. **NUNCA encadenar cp + python + curl en una sola llamada bash**.
   Si python falla en setup phase, bash sigue al siguiente comando.

2. **VERIFICAR el needle DESPUES del edit, ANTES del upload**:
   ```bash
   grep -c "NUEVA_LINEA_DISTINTIVA" /tmp/edited.py
   # Debe ser >= 1, si 0 → ABORT
   ```

3. **Verificar diff intencional**:
   ```bash
   diff -u /original.py /tmp/edited.py | head
   # Si vacio → edit no aplico → ABORT
   ```

4. **F-strings con \x00**: usar literales fuera del f-string:
   ```python
   # MAL:
   print(f'nb: {data.count(b"\x00")}')   # SyntaxError
   # BIEN:
   NULL_BYTE = b'\x00'
   print(f'nb: {data.count(NULL_BYTE)}')
   ```

5. **Detectar regresion** post-commit: hacer `git show HEAD --stat` y
   confirmar que el archivo NUEVO contiene lo intencionado, no algo viejo.

Refs task #73, commits malos `9807b52` y mas. Commits que arreglaron:
`781c91c`, `67c062e`.
