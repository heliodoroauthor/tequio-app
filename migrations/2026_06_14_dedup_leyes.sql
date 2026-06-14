-- Migration 2026-06-14: deduplicar leyes por nombre normalizado + entidad
--
-- Bug: scraper rascó la misma ley dos veces con variaciones de acentuación
-- o capitalización, generando filas duplicadas como:
--   "Acuerdo de los Organos de Gobierno"   (sin acento)
--   "Acuerdo de los Órganos de Gobierno"   (con acento)
-- Mismo contenido, mismo gobierno, ambos sin URL.
--
-- Estrategia: dedupe por (lower(unaccent(nombre)) + entidad + ambito).
-- Preferir la row con URL si existe (caso raro pero blindarse).
-- Si ambas tienen URL, conservar el id mas bajo (mas antigua).

-- 1) Verificar unaccent extension instalada
CREATE EXTENSION IF NOT EXISTS unaccent;

-- 2) Preview de duplicados (NO borra todavia)
SELECT
  lower(unaccent(nombre)) AS norm_nombre,
  entidad,
  ambito,
  COUNT(*) AS dupes,
  array_agg(id ORDER BY (url IS NOT NULL) DESC, id ASC) AS ids,
  array_agg(url IS NOT NULL ORDER BY (url IS NOT NULL) DESC, id ASC) AS tiene_url
FROM leyes
GROUP BY 1, 2, 3
HAVING COUNT(*) > 1
ORDER BY dupes DESC, norm_nombre
LIMIT 20;
-- Si este SELECT muestra 0 filas, no hay duplicados. Saltar el DELETE.

-- 3) DELETE de duplicados, conservando el id "ganador" por particion
WITH ranked AS (
  SELECT
    id,
    nombre,
    entidad,
    ambito,
    url,
    ROW_NUMBER() OVER (
      PARTITION BY lower(unaccent(nombre)), COALESCE(entidad, ''), COALESCE(ambito, '')
      ORDER BY
        (url IS NOT NULL) DESC,  -- prefer row con URL
        id ASC                    -- desempate: id mas bajo gana
    ) AS rn
  FROM leyes
)
DELETE FROM leyes
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- 4) Refrescar contadores en kpis_globales_cache
WITH counts AS (
  SELECT
    (SELECT COUNT(*) FROM leyes) AS total,
    (SELECT COUNT(*) FROM leyes WHERE ambito = 'federal') AS federales,
    (SELECT COUNT(*) FROM leyes WHERE ambito = 'estatal') AS estatales,
    (SELECT COUNT(*) FROM leyes WHERE ambito = 'municipal') AS municipales
)
UPDATE kpis_globales_cache
SET payload = payload
  || jsonb_build_object('leyes', (SELECT total FROM counts))
  || jsonb_build_object('leyes_federales', (SELECT federales FROM counts))
  || jsonb_build_object('leyes_estatal', (SELECT estatales FROM counts))
  || jsonb_build_object('leyes_municipal_reglamento', (SELECT municipales FROM counts))
WHERE id = 1;

-- 5) Verificar resultado final
SELECT
  (SELECT COUNT(*) FROM leyes) AS total_real,
  (SELECT COUNT(*) FROM leyes WHERE ambito = 'federal') AS federales,
  (SELECT COUNT(*) FROM leyes WHERE ambito = 'estatal') AS estatales,
  (SELECT COUNT(*) FROM leyes WHERE ambito = 'municipal') AS municipales,
  (payload->>'leyes')::int AS cache_total
FROM kpis_globales_cache WHERE id = 1;
