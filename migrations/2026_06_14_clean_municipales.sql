-- Migration 2026-06-14: limpiar basura en leyes municipales
--
-- Usuario detecto en Leyes > Municipales que el scraper agarro CUALQUIER PDF
-- de portales municipales: oficios, formatos, requisitos, manuales, actas,
-- gacetas individuales, escudos, etc. Muchos URLs son 404 ademas.
--
-- 3,727 municipales en DB. Patrones obvios de basura:
--   - Nombres con codigos puros (Pdf 2025 2 21 171244, Foya0056..., 005-cmsp.pdf)
--   - Documentos operativos (Manual de, Requisitos para, Solicitud, Convocatoria, Visto Bueno)
--   - Documentos administrativos (Oficio, Acta, Constancia, Informe Trimestral)
--   - No-leyes (Escudo, Licencia de Funcionamiento, Estancia Infantil)
--
-- Esta migracion DELETE solo lo OBVIAMENTE no-ley. Conservadora:
-- prefiere falsos negativos (dejar basura) a falsos positivos (borrar legitimas).

-- PASO 1: Preview cuántos van a desaparecer (corre primero solo el SELECT)
SELECT
  COUNT(*) FILTER (WHERE nombre ~* '^pdf\s+\d') AS pdf_juntos,
  COUNT(*) FILTER (WHERE nombre ~* '^foya\d') AS foya_codes,
  COUNT(*) FILTER (WHERE nombre ~* 'eapecsp|intfaismun') AS prefijos_basura,
  COUNT(*) FILTER (WHERE nombre ~* 'manual\s+de') AS manuales,
  COUNT(*) FILTER (WHERE nombre ~* 'requisitos\s+para') AS requisitos,
  COUNT(*) FILTER (WHERE nombre ~* 'visto\s+bueno') AS visto_bueno,
  COUNT(*) FILTER (WHERE nombre ~* 'convocatoria') AS convocatorias,
  COUNT(*) FILTER (WHERE nombre ~* '^acta\s') AS actas,
  COUNT(*) FILTER (WHERE nombre ~* 'oficio') AS oficios,
  COUNT(*) FILTER (WHERE nombre ~* 'escudo\s+(de|del)') AS escudos,
  COUNT(*) FILTER (WHERE nombre ~* 'licencia\s+de\s+funcionami') AS licencias,
  COUNT(*) FILTER (WHERE nombre ~* 'estancia\s+infantil') AS estancias,
  COUNT(*) FILTER (WHERE nombre ~* 'difusion') AS difusiones,
  COUNT(*) FILTER (WHERE nombre ~* 'informe\s+(de|del|trim|cuarto)') AS informes,
  COUNT(*) FILTER (WHERE nombre ~* 'presupuesto\s+(de|del|para)') AS presupuestos,
  COUNT(*) FILTER (WHERE nombre ~* '^[0-9_.\s-]+\.?$') AS solo_numerico,
  COUNT(*) AS total_municipales
FROM leyes
WHERE ambito = 'municipal';

-- PASO 2: DELETE conservador. Borra solo lo OBVIAMENTE basura.
-- Despues de revisar el SELECT, corre esto:
DELETE FROM leyes
WHERE ambito = 'municipal'
  AND (
    nombre ~* '^pdf\s+\d' OR
    nombre ~* '^foya\d' OR
    nombre ~* 'eapecsp|intfaismun' OR
    nombre ~* 'manual\s+de' OR
    nombre ~* 'requisitos\s+para' OR
    nombre ~* 'visto\s+bueno' OR
    nombre ~* 'solicitud\s+(de|para)' OR
    nombre ~* 'convocatoria' OR
    nombre ~* '^acta\s' OR
    nombre ~* 'oficio' OR
    nombre ~* 'escudo\s+(de|del)' OR
    nombre ~* 'licencia\s+de\s+funcionami' OR  -- incluye typo "Funcionamineto"
    nombre ~* 'estancia\s+infantil' OR
    nombre ~* 'difusion' OR
    nombre ~* 'informe\s+(de|del|trim|cuarto|tercero)' OR
    nombre ~* 'presupuesto\s+(de|del|para)' OR
    nombre ~* 'contraloria\s+municipal' OR
    nombre ~* '^[0-9_.\s-]+\.?$' OR  -- solo numerico/codigo
    nombre ~* 'foya\d+|eapecsp|intfaismun|armayo\s+\d|01_escudo|02_escudo' OR
    nombre ~* 'inf\.\s*\d+(to|er)?\.\s*trim' OR  -- "Inf. 4to. Trim."
    nombre ~* 'foya\d{4,}'
  );

-- PASO 3: Refrescar contadores en kpis_globales_cache
UPDATE kpis_globales_cache
SET payload = payload
  || jsonb_build_object('leyes', (SELECT COUNT(*) FROM leyes))
  || jsonb_build_object('leyes_municipal_reglamento', (SELECT COUNT(*) FROM leyes WHERE ambito = 'municipal'))
WHERE id = 1;

-- PASO 4: Verificacion final
SELECT
  (SELECT COUNT(*) FROM leyes WHERE ambito = 'municipal') AS municipales_real,
  (SELECT COUNT(*) FROM leyes) AS total_real,
  (payload->>'leyes')::int AS cache_total,
  (payload->>'leyes_municipal_reglamento')::int AS cache_municipal
FROM kpis_globales_cache WHERE id = 1;
