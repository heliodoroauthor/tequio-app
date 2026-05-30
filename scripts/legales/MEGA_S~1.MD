# Mega-sweep municipios — meta 2,478

## Para qué

Llenar `public.leyes` con normatividad de **los 2,478 municipios de México** sin necesidad de chat asistido.

## Por qué un workflow y no chat

Una sesión de chat:

- Sandbox con timeout de 45 s por comando bash → cada batch grande se cae
- DNS bloqueado para muchos dominios `.cdmx.gob.mx`, `.gob.mx` desde sandbox
- WebSearch tiene rate limits (~10 queries/min sostenible)
- Conservadoramente: **~15 ciudades/hora** sostenible → **~165 horas** para 2,478

GitHub Actions runner Ubuntu 22.04:

- 6h por job, hasta 20 jobs en paralelo
- DNS reside, sin restricciones
- DuckDuckGo via `duckduckgo-search` (sin rate limit estricto)
- Conservadoramente: **~80 ciudades/hora** → **~30 horas** dividido en 30 noches

## Setup

1. **Subir catálogo INEGI**. Bajar de [INEGI Catálogo Único de Localidades](https://www.inegi.org.mx/app/ageeml/) el CSV de municipios. Guardarlo en `data/catalogo_inegi_municipios.csv` con columnas:
   ```
   cve_ent,nom_ent,cve_mun,nom_mun
   01,Aguascalientes,001,Aguascalientes
   01,Aguascalientes,002,Asientos
   ...
   32,Zacatecas,058,Zacatecas
   ```

2. **Configurar secrets en GitHub**. En `Settings → Secrets → Actions`:
   - `SUPABASE_URL` = `https://mhsuihwjgtzxflesbnxv.supabase.co`
   - `SUPABASE_ANON` = `sb_publishable_grJAWIMoSr5b8YfaB7HVlw_OesNipBz`

3. **Versionar al repo**:
   ```bash
   git add scripts/legales/mega_sweep_municipios.py
   git add scripts/legales/parser_v4.py
   git add scripts/legales/data/catalogo_inegi_municipios.csv
   git add .github/workflows/mega-sweep-municipios.yml
   git commit -m "feat: mega-sweep 2478 municipios autónomo"
   git push origin main
   ```

## Cómo correrlo

### Manual (1 estado específico)

```
GitHub → Actions → Mega-sweep municipios → Run workflow
  estado: 09 (CDMX)
  max_per_municipio: 8
```

### Automático

El cron `0 3 * * *` corre cada noche y rota entre los 32 estados (cve_ent
del día del mes mod 32). En ~30 noches debe haber tocado todos.

## Anti-duplicación

`mega_sweep_municipios.py` lee primero `public.leyes?entidad=eq.<X>&ambito=eq.municipal`
con `count=exact`. Si ya hay `>= max_per_municipio` docs, hace SKIP. Eso significa
que correrlo dos veces en el mismo estado no duplica trabajo.

## Limpieza

- Shells que parser no logra procesar → `DELETE` antes de continuar
- Fails 404/403 → `DELETE`
- OCR fallback automático para PDFs escaneados (Xerox D35 style)

## Output esperado por noche

| Estado     | Municipios | Tiempo estimado | Docs cargados |
|------------|-----------:|----------------:|--------------:|
| Aguascalientes  | 11    | 30 min  | 60-90     |
| Baja California | 7     | 25 min  | 40-60     |
| ...        | ...        | ...             | ...           |
| Oaxaca     | 570        | 6h+ (multi-job) | 2,000-4,000   |

Estados grandes (Oaxaca 570, Puebla 217, Veracruz 212) requieren múltiples jobs o
dividir por región dentro del estado.

## Monitoreo

Después de cada job:

```bash
curl -s -X POST -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
  -H "Content-Type: application/json" \
  $URL/rest/v1/rpc/dashboard_kpis_globales -d '{}' | jq '.leyes_municipal'
```

Y el log del job (`Actions → run → logs`) muestra cuántos cargaron.

## Limitaciones honestas

- ~30% de municipios pequeños no tienen sitio web o sus PDFs están detrás de WAFs
  (Radware, Cloudflare bot detection). Coverage realista: ~70% = ~1,700 municipios.
- Si después de 30 noches sigue sin cubrir un estado, considerar:
  - Catálogo OJ Nacional Federal (gob.mx/orden-juridico-nacional)
  - Periódicos Oficiales por estado
  - Catálogos por estado (ej: zacatecas tiene `catalogoestatal.zacatecas.gob.mx`)
