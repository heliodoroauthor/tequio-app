-- Migration 2026-06-14: limpiar Leyes y Decretos federales
--
-- Bug detectado: pestana FEDERAL mostraba 292 leyes, 6 de ellas problematicas:
-- 1) 4 paginas INDEX de diputados.gob.mx/LeyesBiblio (no son leyes individuales,
--    son catalogos): "Reglamentos de Leyes vigentes", "Reglamentos de Leyes
--    abrogados", "Estatutos Federales vigentes", "Reglamentos Federales vigentes"
-- 2) 2 codigos estatales mal taggeados como federal por scraper:
--    - "Codigo Penal de Coahuila de Zaragoza" (entidad=Coahuila, sin URL)
--    - "Codigo De Procedimientos Civiles" (entidad=Tabasco, sin URL)
--
-- Esta migracion:
-- 1) DELETE los 4 index pages (ids 9271,9272,9273,9274)
-- 2) UPDATE los 2 codigos estatales: ambito federal → estatal
-- 3) Refresca el cache de KPIs (292 → 288 federales)

BEGIN;

-- 1) DELETE index pages
DELETE FROM leyes WHERE id IN (9271, 9272, 9273, 9274);

-- 2) Reclasificar codigos estatales mal taggeados
UPDATE leyes SET ambito = 'estatal' WHERE id IN (2224, 5083);

-- 3) Asserts antes de commit
DO $body$
DECLARE
  v_federales INTEGER;
  v_estatales INTEGER;
  v_indices_quedan INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_federales FROM leyes WHERE ambito = 'federal';
  SELECT COUNT(*) INTO v_estatales FROM leyes WHERE ambito = 'estatal';
  SELECT COUNT(*) INTO v_indices_quedan FROM leyes WHERE id IN (9271, 9272, 9273, 9274);

  RAISE NOTICE 'Federales: % (esperado: 288, antes 292)', v_federales;
  RAISE NOTICE 'Estatales: % (subio en 2 vs antes)', v_estatales;
  RAISE NOTICE 'Index pages quedan: % (esperado: 0)', v_indices_quedan;

  IF v_indices_quedan > 0 THEN
    RAISE EXCEPTION 'FATAL: index pages no se borraron';
  END IF;
  IF v_federales NOT BETWEEN 285 AND 290 THEN
    RAISE EXCEPTION 'FATAL: count federales fuera de rango (% / esperado 288)', v_federales;
  END IF;
END
$body$;

COMMIT;

-- 4) Refrescar cache de KPIs (corre el sync_kpis_fallback workflow despues)
-- Manual UPDATE inmediato del cache:
UPDATE kpis_globales_cache
SET payload = jsonb_set(
  jsonb_set(payload, '{leyes_federales}', '288'::jsonb),
  '{leyes}', to_jsonb((SELECT COUNT(*) FROM leyes))
)
WHERE id = 1;

-- Verificar
SELECT
  payload->>'leyes' AS total,
  payload->>'leyes_federales' AS federales,
  payload->>'leyes_estatal' AS estatales,
  payload->>'leyes_municipal_reglamento' AS municipales
FROM kpis_globales_cache WHERE id = 1;
