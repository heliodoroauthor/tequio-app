-- Migration 2026-06-13: derivar resultado de votaciones_diputados desde totales
-- 
-- Contexto: SITL no publica un campo "Aprobado/Rechazado" en el HTML, solo
-- conteos por grupo parlamentario. La conclusion se deriva matematicamente
-- de la mayoria simple (SI > NO).
--
-- Esta migracion:
-- 1) Backfill: rellena los 264 rows con resultado=NULL usando mayoria simple
-- 2) Trigger: cualquier insert/update futuro tambien lo deriva si viene NULL
--
-- Limitacion conocida: reformas constitucionales requieren 2/3 (no mayoria
-- simple). Para esos casos raros un PATCH manual puede sobrescribir.

-- 1) BACKFILL
UPDATE votaciones_diputados
SET resultado = CASE
    WHEN COALESCE(total_si, 0) > COALESCE(total_no, 0) THEN 'aprobada'
    WHEN COALESCE(total_no, 0) >= COALESCE(total_si, 0)
         AND (COALESCE(total_si, 0) + COALESCE(total_no, 0)) > 0
         THEN 'rechazada'
    ELSE NULL  -- sin votos registrados, dejar NULL
END
WHERE resultado IS NULL;

-- 2) TRIGGER: derivar en INSERT/UPDATE si NULL
CREATE OR REPLACE FUNCTION derive_resultado_votacion()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.resultado IS NULL AND (COALESCE(NEW.total_si, 0) + COALESCE(NEW.total_no, 0)) > 0 THEN
    NEW.resultado := CASE
      WHEN COALESCE(NEW.total_si, 0) > COALESCE(NEW.total_no, 0) THEN 'aprobada'
      ELSE 'rechazada'
    END;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_derive_resultado_votacion ON votaciones_diputados;
CREATE TRIGGER trg_derive_resultado_votacion
  BEFORE INSERT OR UPDATE ON votaciones_diputados
  FOR EACH ROW EXECUTE FUNCTION derive_resultado_votacion();

-- Verify
SELECT
  COUNT(*) FILTER (WHERE resultado IS NULL) AS pendientes,
  COUNT(*) FILTER (WHERE resultado = 'aprobada') AS aprobadas,
  COUNT(*) FILTER (WHERE resultado = 'rechazada') AS rechazadas
FROM votaciones_diputados;
