# Estado de scrapers · diagnóstico 2026-05-21

Tras la auditoría, los 8 scrapers marcados `NUNCA_OK` reportan HTTP 200 con 0 filas insertadas. La causa raíz **NO** es uniforme. Cada uno requiere una acción distinta.

## Cuadro consolidado

| Scraper | Fuente probada | HTTP real | Causa raíz | Acción |
|---|---|---|---|---|
| `noticias` (SEGOB) | `www.gob.mx/segob/archivo/prensa` | 200 + `<title>Challenge Validation</title>` | **Anti-bot** (Imperva/Akamai/similar) en gob.mx | Usar cloudscraper, Playwright headless, o feed alternativo |
| `noticias` (Presidencia) | `www.gob.mx/presidencia/archivo/prensa` | 200 + Challenge | Mismo anti-bot gob.mx | Igual |
| `noticias` (DOF) | `dof.gob.mx/sumario.xml` | TIMEOUT | Servidor lento o blocked | Aumentar timeout · probar HTTP en vez de HTTPS · usar RSS alternativo |
| `noticias` (SCJN) | `www.scjn.gob.mx/multimedia/comunicados` | **200 + 55KB OK** | Funciona | Mantener como fuente principal |
| `diputados` | `sitl.diputados.gob.mx/` (raíz) | 404 File not found | URL raíz no existe | Cambiar a URLs atómicas tipo `sitl.diputados.gob.mx/LXVI_leg/estadistico_votacionnplxvi.php?votaciont=N` (esas SÍ responden 200) |
| `gaceta_pendientes` | `gaceta.diputados.gob.mx/Gaceta/Votaciones.html` | 404 | URL específica cambió | Investigar nueva ruta en gaceta.diputados.gob.mx |
| `votos` | tabla `votos` (DEPRECATED, vacía) | — | Tabla obsoleta | **Eliminar workflow + script** (los votos reales están en `votos_individuales` con 128K rows) |
| `compranet` | `api.datos.gob.mx/v2/contratacionesabiertas` | **TIMEOUT > 25s** | API OCDS de SHCP caída | Banner "datos en transición" ya aplicado en UI · esperar reactivación de SHCP |
| `conagua_presas` | `sih.conagua.gob.mx/principales.html` | 200 + Challenge | **Anti-bot** CONAGUA | Usar cloudscraper o cambiar a fuente alternativa CONAGUA SMN datos abiertos |
| `inegi` | `inegi.org.mx/ (raíz) | 400 Bad Request | URL incompleta · servidor exige path específico | Buscar endpoint API: INEGI publica catálogos en formato JSON pero requiere clave de servicio |
| `inegi_geo_municipios` | similar a `inegi` | — | Probable mismo problema | Igual |

## 3 patrones distintos de falla

### Patrón A: ANTIBOT (gob.mx y CONAGUA)

Servidor responde HTTP 200 con HTML `<title>Challenge Validation</title>` (~1.9 KB). El scraper Python (`requests` + BeautifulSoup) no puede pasar el JavaScript challenge.

**Soluciones técnicas:**
- `pip install cloudscraper` → reemplazar `requests.get(...)` por `cloudscraper.create_scraper().get(...)` (resuelve algunos challenges JS automáticamente)
- Playwright/Selenium en GitHub Actions (más pesado, pero garantiza JS execution)
- Cambiar a fuente alternativa sin anti-bot

**Detección en código** (recomendado agregar a todos los scrapers de gob.mx):
```python
if 'Challenge Validation' in resp.text[:500]:
    return {'status': 'blocked_antibot', 'rows': 0, 'msg': 'gob.mx anti-bot challenge'}
```

### Patrón B: URLs CAMBIADAS (SITL, Gaceta, INEGI)

El servidor responde, pero la URL específica del scraper devuelve 404 o 400. La estructura del sitio cambió desde la última vez que el scraper funcionó.

**Acción:** ir al sitio en navegador, encontrar la nueva URL del listado, actualizar el scraper. Las URLs atómicas (`?votaciont=N`, `?dipt_id=N`) sí funcionan, así que se puede reconstruir el listado iterando IDs en lugar de scrapear el índice.

### Patrón C: API CAÍDA (CompraNet OCDS)

`api.datos.gob.mx` responde con timeout > 25 segundos. La API OCDS de SHCP ha estado intermitente durante meses según reportes públicos. No es problema del scraper.

**Acción ya aplicada:** banner amarillo "Datos en transición" en `index.html` para que los usuarios sepan que los contratos pueden no estar al día. Cuando SHCP reactive la API, el scraper volverá a llenar `contratos_publicos` automáticamente.

## Prioridad sugerida

1. **`votos`** — eliminar workflow + script (5 min, sin riesgo)
2. **`noticias`** — agregar detector ANTIBOT + mejorar logs (10 min)
3. **`diputados` y `gaceta_pendientes`** — reescribir con URLs nuevas (1 hora cada uno, requiere navegar manualmente y mapear endpoints)
4. **`compranet`** — esperar API SHCP (banner ya comunica al usuario)
5. **`conagua_presas`** + gob.mx noticias — requiere cloudscraper/Playwright (2-3 horas + testing)
6. **`inegi`, `inegi_geo_municipios`** — registrar token API INEGI o usar endpoint correcto (1 hora)

## Conclusión

El problema "verde mentiroso" (status=ok + 0 rows) es ahora **visible y diagnosticable** gracias a la vista `public.scraper_health` (creada en FIX-17) que distingue NEVER_OK / DEGRADED / STALE / SILENT_FAIL / IDLE / OK.

Para la próxima sesión: priorizar **votos (eliminar)** y **noticias (antibot detector + cloudscraper)** porque son los que más impactan la experiencia visible del usuario.

---
**Generado:** 2026-05-21
**Auditor:** Sesión Claude
**Evidencia:** `net._http_response` IDs 1217-1231 muestran respuestas reales de cada fuente.
