# Plan de ejecución: 2,478 municipios — estado por estado

Estado al 30 may 2026: **63 ciudades cubiertas con docs municipales** (563 docs total).

Meta: **2,478 municipios** (100% del catálogo INEGI).

## Filosofía del plan

1. **No atacar en orden alfabético.** Atacar por **ROI** (esfuerzo bajo + cobertura alta primero).
2. **Una fuente principal por estado** cuando exista (OJ Nacional, congreso estatal, catálogo estatal). Cae a Google Search + dominio municipal solo cuando no haya.
3. **Schedule realista**: 1 estado por noche de GitHub Action. Total ~8 semanas para 80% coverage.
4. **Revisión semanal** del progreso vía KPI `leyes_municipal`.

## Inventario por estado

| #  | Estado            | Municipios | Estado actual                | Estrategia recomendada              | Estimado horas |
|----|-------------------|-----------:|------------------------------|-------------------------------------|---------------:|
| 01 | Aguascalientes    | 11         | ✅ 207 docs (OJ Nacional)    | Done                                |             0 |
| 02 | Baja California   | 7          | Tijuana, Mexicali partial    | Google site:X.gob.mx                |             2 |
| 03 | BCS               | 5          | La Paz                       | Google + portal estatal             |             2 |
| 04 | Campeche          | 13         | Carmen, Capital, Calakmul    | Google por municipio                |             4 |
| 05 | Coahuila          | 38         | 0                            | OJ Nacional Coah                    |             8 |
| 06 | Colima            | 10         | Capital                      | Google site:<muni>.gob.mx           |             3 |
| 07 | Chiapas           | 124        | Tuxtla                       | OJ Nacional + Google                |            16 |
| 08 | Chihuahua         | 67         | Juárez                       | Portal `chihuahua.gob.mx/transparencia` |        12 |
| 09 | CDMX              | 16         | 11 alcaldías ✅              | Manuales `data.consejeria.cdmx.gob.mx` | 4 |
| 10 | Durango           | 39         | Pueblo Nuevo, Canatlán       | Portal `durango.gob.mx`             |             8 |
| 11 | Guanajuato        | 46         | León (100 docs!)             | `normatividadestatalymunicipal.guanajuato.gob.mx` | 10 |
| 12 | Guerrero          | 80         | Acapulco                     | OJ Nacional + Google                |            14 |
| 13 | Hidalgo           | 84         | Pachuca                      | Periódico Oficial Hidalgo           |            14 |
| 14 | Jalisco           | 125        | GDL, Tonalá, Zap, Tlaq, Tlaj | Portal `periodicooficial.jalisco.gob.mx` |   16 |
| 15 | Edomex            | 125        | 10+ munis                    | Periódico Oficial Edomex            |            16 |
| 16 | Michoacán         | 113        | Morelia                      | Google + `congresomich.gob.mx`      |            15 |
| 17 | Morelos           | 36         | Cuernavaca                   | OJ Nacional Mor                     |             6 |
| 18 | Nayarit           | 20         | Tepic                        | Google site:<muni>.gob.mx           |             4 |
| 19 | NL                | 51         | Monterrey                    | `hcnl.gob.mx` HCNL                  |             8 |
| 20 | Oaxaca            | 570        | Oax-Juárez, Salina Cruz      | `sisplade.oaxaca.gob.mx` + Google   |            48 |
| 21 | Puebla            | 217        | 0 (WAF)                      | Google + Periódico Oficial          |            22 |
| 22 | Querétaro         | 18         | Capital                      | Google site:<muni>.gob.mx           |             4 |
| 23 | Quintana Roo      | 11         | Cancún                       | Portal `qroo.gob.mx`                |             3 |
| 24 | SLP               | 58         | Capital                      | Periódico Oficial SLP               |             9 |
| 25 | Sinaloa           | 20         | Culiacán                     | Google site:<muni>.gob.mx           |             4 |
| 26 | Sonora            | 72         | Hermosillo                   | Boletín Oficial Sonora              |            12 |
| 27 | Tabasco           | 17         | Villahermosa (Centro)        | Google site:<muni>.gob.mx           |             4 |
| 28 | Tamaulipas        | 43         | Reynosa                      | Google + Periódico Oficial          |             8 |
| 29 | Tlaxcala          | 60         | Tlaxcala                     | `publicaciones.tlaxcala.gob.mx`     |             9 |
| 30 | Veracruz          | 212        | Xalapa                       | Gaceta Oficial Ver + Google         |            22 |
| 31 | Yucatán           | 106        | Mérida                       | Diario Oficial Yucatán              |            14 |
| 32 | Zacatecas         | 58         | Capital                      | `catalogoestatal.zacatecas.gob.mx`  |             9 |
|    | **TOTAL**         | **2,478**  | **63 ciudades**              |                                     |     **~340h** |

340 h ÷ 6 h por job ÷ 1 job por noche ≈ **57 noches**. Con paralelismo (3 jobs simultáneos) ≈ **19 noches** = **~3 semanas**.

## Fases por ROI

### Fase 1 — Quick wins (Semana 1)

Estados chicos donde Google Search alcanza, +completar CDMX:

| Estado          | Municipios | Por qué primero                                        |
|-----------------|-----------:|--------------------------------------------------------|
| Baja California | 7          | Sitios bien estructurados                              |
| BCS             | 5          | Sólo 5 municipios                                      |
| Colima          | 10         | Pocos municipios                                       |
| Nayarit         | 20         | Sitios homogéneos                                      |
| Quintana Roo    | 11         | Sitios estables                                        |
| Sinaloa         | 20         | Sitios típicos `.gob.mx`                               |
| Tabasco         | 17         | Sitios típicos                                         |
| CDMX (restante) | 5          | Cerrar las 16 alcaldías                                |
| **Total fase**  | **95**     | **~25 h → 4 noches**                                   |

### Fase 2 — Estados medianos con portales centralizados (Semanas 2-3)

| Estado     | Municipios | Fuente principal                                                |
|------------|-----------:|------------------------------------------------------------------|
| Campeche   | 13         | `periodicooficial.campeche.gob.mx` + Google                      |
| Coahuila   | 38         | `congresocoahuila.gob.mx/transparencia` + OJ Nacional            |
| Durango    | 39         | `congresodurango.gob.mx` + `<muni>.durango.gob.mx`               |
| Guanajuato | 46         | `normatividadestatalymunicipal.guanajuato.gob.mx` (mega!)        |
| Morelos    | 36         | OJ Nacional + Periódico Oficial Mor                              |
| NL         | 51         | `hcnl.gob.mx` (ya probado con Mty)                               |
| Querétaro  | 18         | `municipiode<X>.gob.mx`                                           |
| SLP        | 58         | `periodicooficial.slp.gob.mx`                                    |
| Tamaulipas | 43         | `periodicooficial.tamaulipas.gob.mx`                             |
| Tlaxcala   | 60         | `publicaciones.tlaxcala.gob.mx`                                  |
| Zacatecas  | 58         | `catalogoestatal.zacatecas.gob.mx` (mega!)                       |
| **Total fase** | **460** | **~85 h → 14 noches**                                            |

### Fase 3 — Estados grandes (Semanas 4-6)

| Estado     | Municipios | Notas                                                            |
|------------|-----------:|------------------------------------------------------------------|
| CDMX       | 16         | Ya casi (12/16)                                                  |
| Chiapas    | 124        | OJ Nacional Chi + boletín                                        |
| Chihuahua  | 67         | `transparencia.chihuahua.gob.mx`                                 |
| Hidalgo    | 84         | Periódico Oficial Hgo                                            |
| Jalisco    | 125        | Periódico Oficial Jal — pero rápido (sitios buenos)              |
| Edomex     | 125        | Periódico Oficial Edomex                                         |
| Michoacán  | 113        | `congresomich.gob.mx`                                            |
| Sonora     | 72         | Boletín Oficial Son                                              |
| Yucatán    | 106        | Diario Oficial Yuc                                               |
| **Total fase** | **832** | **~125 h → 21 noches**                                           |

### Fase 4 — Estados con cobertura difícil (Semanas 7-8)

| Estado    | Municipios | Por qué es difícil                                                  |
|-----------|-----------:|---------------------------------------------------------------------|
| Guerrero  | 80         | ~50% municipios sin sitio web                                       |
| Puebla    | 217        | WAF agresivo en pueblacapital.gob.mx                                |
| Oaxaca    | 570        | El monstruo: 570 municipios. ~70% no tienen reglamentos publicados  |
| Veracruz  | 212        | Mucha heterogeneidad, requiere normalización                        |
| **Total fase** | **1,079** | **~115 h → 19 noches**                                          |

### Coverage realista esperada

- **Fase 1-3 (35 noches)**: 1,495 municipios = **60% coverage**
- **Fase 4 completada (54 noches)**: ~2,000 municipios = **80% coverage**
- **20% restante**: municipios chicos sin presencia web. Requiere alternativas:
  - Solicitudes formales por Transparencia (`infomex.org.mx`)
  - Periódicos Oficiales escaneados (OCR pesado)
  - Skip si no hay documentos publicados (no es excluibles si no existen)

## Templates de queries por estado

### Aguascalientes (DONE)
- `site:ags.gob.mx reglamento filetype:pdf`

### Baja California
- `site:tijuana.gob.mx reglamento filetype:pdf`
- `site:mexicali.gob.mx reglamento filetype:pdf`
- `site:ensenada.gob.mx reglamento filetype:pdf`
- `site:tecate.gob.mx reglamento filetype:pdf`
- `site:playasderosarito.gob.mx reglamento filetype:pdf`
- `site:sanquintin.gob.mx reglamento filetype:pdf`
- `site:sanfelipe.gob.mx reglamento filetype:pdf`

### CDMX
- `site:<alcaldia>.cdmx.gob.mx manual administrativo filetype:pdf`
- `site:data.consejeria.cdmx.gob.mx <alcaldia> reglamento filetype:pdf`

### Estado de México
- `site:periodicooficial.edomex.gob.mx <muni> reglamento filetype:pdf`
- Por cada municipio: `site:<muni>.gob.mx reglamento filetype:pdf`

### Jalisco
- `site:periodicooficial.jalisco.gob.mx <muni> reglamento filetype:pdf`
- `site:<muni>.gob.mx reglamento filetype:pdf`

### Oaxaca (gigante)
- `site:sisplade.oaxaca.gob.mx <muni> reglamento filetype:pdf`
- `site:periodicooficial.oaxaca.gob.mx <muni> filetype:pdf`

### Puebla
- `site:gobiernoabierto.pueblacapital.gob.mx reglamento filetype:pdf`
- `site:periodicooficial.puebla.gob.mx <muni> filetype:pdf`

### Veracruz
- `site:gacetaoficial.veracruz.gob.mx <muni> filetype:pdf`
- `site:<muni>.gob.mx reglamento filetype:pdf`

## Schedule cron sugerido

```yaml
# .github/workflows/mega-sweep-municipios.yml
schedule:
  # Lunes-Viernes a las 03:00 UTC. Sábado/Dom descanso (rate limit recovery)
  - cron: "0 3 * * 1-5"
```

Rotación por día de mes (mod 32):

| Día | Estado            | Día | Estado            |
|-----|-------------------|-----|-------------------|
| 01  | Aguascalientes ✅ | 17  | Morelos           |
| 02  | Baja California   | 18  | Nayarit           |
| 03  | BCS               | 19  | Nuevo León        |
| 04  | Campeche          | 20  | Oaxaca (1ª pasada)|
| 05  | Coahuila          | 21  | Puebla            |
| 06  | Colima            | 22  | Querétaro         |
| 07  | Chiapas           | 23  | Quintana Roo      |
| 08  | Chihuahua         | 24  | SLP               |
| 09  | CDMX              | 25  | Sinaloa           |
| 10  | Durango           | 26  | Sonora            |
| 11  | Guanajuato        | 27  | Tabasco           |
| 12  | Guerrero          | 28  | Tamaulipas        |
| 13  | Hidalgo           | 29  | Tlaxcala          |
| 14  | Jalisco           | 30  | Veracruz          |
| 15  | Edomex            | 31  | Yucatán           |
| 16  | Michoacán         | 32  | Zacatecas         |

Estados grandes (Oaxaca, Puebla, Veracruz) — agregar `2ª pasada` y `3ª pasada` los meses siguientes para llegar a su long-tail.

## Métricas de éxito por fase

| Fase | Semanas | Coverage objetivo | Métrica DB                                |
|------|--------:|-------------------|-------------------------------------------|
| 0    | Hoy     | 63 ciudades       | `leyes_municipal = 563`                   |
| 1    | 1       | 150 ciudades      | `leyes_municipal ≥ 1,200`                 |
| 2    | 3       | 500 ciudades      | `leyes_municipal ≥ 4,000`                 |
| 3    | 6       | 1,300 ciudades    | `leyes_municipal ≥ 10,500`                |
| 4    | 8       | 1,800-2,000       | `leyes_municipal ≥ 14,500`                |
| 5    | 12+     | 2,000-2,200       | Long-tail manual via Transparencia        |

## Dashboard para monitorear

Agregar al panel `/admin/cobertura-municipal.html`:

```sql
SELECT
  entidad,
  COUNT(*) AS docs,
  COUNT(DISTINCT entidad) AS ciudades,
  COUNT(*) FILTER (WHERE chunks_count > 0) AS con_chunks
FROM leyes
WHERE ambito = 'municipal'
GROUP BY entidad
ORDER BY docs DESC;
```

Y un gráfico de barra estado vs municipios cubiertos / total municipios.

## Próximos pasos inmediatos

1. **Hoy**: subir `catalogo_inegi_municipios.csv` con las 2,478 filas reales (bajar de INEGI)
2. **Hoy**: commit + push del workflow + `mega_sweep_municipios.py` + `parser_v4.py`
3. **Hoy**: setup `SUPABASE_URL` y `SUPABASE_ANON` en `Settings → Secrets → Actions`
4. **Hoy**: trigger manual del workflow con `estado=02` (BC) para validar
5. **Mañana noche**: habilitar el cron schedule

Bajar después de 1 semana y revisar:

```sql
SELECT entidad, COUNT(*) FROM leyes WHERE ambito='municipal' GROUP BY entidad ORDER BY 2 DESC;
```
