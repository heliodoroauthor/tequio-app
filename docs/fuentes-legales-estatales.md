# Fuentes legales por entidad federativa

**Fecha:** 2026-05-28
**Auditor:** Sesión Cowork (CERO INVENCIÓN)
**Status:** En desarrollo — extender al replicar pipeline en otros estados

---

## 🟢 Funcionando con pipeline `scripts/cargar_ley_estatal.py`

### Nuevo León — HCNL ✅

- **Portal:** https://www.hcnl.gob.mx/trabajo_legislativo/leyes/
- **Patrón URL PDF:** `https://www.hcnl.gob.mx/trabajo_legislativo/leyes/pdf/<NOMBRE EN MAYÚSCULAS CON ACENTOS>.pdf?<fecha>`
- **Ejemplo:** `LEY%20DE%20GOBIERNO%20MUNICIPAL%20DEL%20ESTADO%20DE%20NUEVO%20LEÓN.pdf`
- **Nota:** los acentos se conservan en la URL (encodeados `%C3%9A` para `Ú`, etc.). NO normalizar a ASCII.
- **Cobertura en sesión 2026-05-28:** 38 leyes cargadas / 248 totales (15%)
- **Estructura PDF:** mayoría usa `ARTÍCULO N.-`, algunas viejas usan `ARTICULO 1o.-` o `ARTICULO PRIMERO.-` (parser v2 cubre los 3)

**Workflow probado:**
```bash
curl -sL "<index_url>" > /tmp/idx.html
# Hacer fuzzy match contra los hrefs de PDFs
python3 scripts/cargar_ley_estatal.py <ley_id> <url_pdf> "<nombre>" "Nuevo Leon"
```

---

## 🔴 NO scrapeable directamente

### Jalisco
- `congresoweb.congresojal.gob.mx` → redirect a IP raw que no responde
- `congresoweb.congresojal.gob.mx/infolej/` → 403 Forbidden
- `periodicooficial.jalisco.gob.mx` → 200 pero sin listado navegable de leyes
- `transparencia.info.jalisco.gob.mx/...` → 404
- `info.jalisco.gob.mx/sites/.../leyes/` → 404
- `www.diputados.gob.mx/LeyesBiblio/marco/ent_14.htm` → 404
- `ordenjuridico.gob.mx/Estatal/JALISCO/` → 404
- **Status:** Bloqueado. Posible solución futura: scrape con Playwright (renderiza JS) o solicitar API oficial al Congreso.

### CDMX
- `congresocdmx.gob.mx/leyes-locales-50-1.html` → 200 pero HTML sin links a PDFs (SPA o lazy-load)
- `congresocdmx.gob.mx/marco-legal-50-1.html` → 200 igual sin contenido scrapeable
- `www.diputados.gob.mx/LeyesBiblio/marco/ent_9.htm` → 404
- **Status:** Bloqueado. Considerar reverse-engineering del XHR que carga el listado.

### Monterrey (municipal)
- `monterrey.gob.mx/reglamentos.aspx` → SPA Quasar/Vue, 955 bytes de HTML inicial sin contenido
- **Status:** Pivot: usar leyes estatales NL que regulan a los municipios (ya cargadas).

---

## 📋 Pendiente investigar (no probado en sesión)

Entidades con más de 200 leyes en DB (todas con shells/parser pobre):
- Aguascalientes (Municipal): 437
- Baja California: 422
- Yucatán: 291
- Estado de México: 223
- Sonora: 210
- Veracruz: 206
- Coahuila: 200

**Próximos pasos sugeridos:**
1. Probar portales de cada congreso estatal (mayoría usa CMS Joomla/Wordpress con PDFs)
2. Si el portal del Congreso es SPA, intentar Periódico Oficial del Estado
3. Como fallback final, usar `ordenjuridico.gob.mx` que centraliza algunos estados

---

## Bug conocido: Edge Function `load-leyes-chunks`

No usable para cargas nuevas. Ver `docs/bugs/BUG-load-leyes-chunks-chunk_idx.md`.

El pipeline `scripts/cargar_ley_estatal.py` usa el workaround (`leyes_chunks_replace_all` SECURITY DEFINER).
