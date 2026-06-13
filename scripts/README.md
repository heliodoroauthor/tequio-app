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
