# AUDITORIA Tequio.app · Status 2026-05-21

**Branch:** `main`  · **Supabase:** `mhsuihwjgtzxflesbnxv`
**Auditoria completa (93 hallazgos, 9 fases):** ver `tequio-auditoria-2026-05-21.md` en archivos locales del autor.

---

## Fixes aplicados en esta sesion

| # | Fix | Hallazgo | Commit / Evidencia |
|---|---|---|---|
| 1 | Desactivar `patch-index-cero-invencion` | H1.1-02 | Edge Function v3: 410 Gone + verify_jwt=true |
| 2 | Reescribir hero Testigo Civico | H2.9-01, H2.9-02 | `fcf4dc96` — copy honesto, badges sin claim probatorio |
| 3 | Marcar DEMO Gasto Publico + Derechos Consultados | H2.1-01, H2.1-02 | `fcf4dc96` — badge `DEMO cifras de ejemplo` |
| 4 | Fix typo `showPage('accion')` -> `'acciones'` | H1.2-05 | `fcf4dc96` |
| 5a | Eliminar `panel/test.html` | H1.2-06 | `c3c0f795` |
| 5b | Eliminar `canasta.html` huerfana | H1.2-04 | `cfeb1a54` |
| 5c | Eliminar `index.html.SAFE-20260514-2308` | H1.1-07 | `01efe8be` |
| 6 | RPC `match_legal_documents` -> `jurisprudencia_scjn` | H2.6-01 | SQL aplicado. Pendiente: backfill embeddings |
| 7 | gh-proxy con shared secret + CORS restringido | H1.1-03, H2.6-04 | Edge Function v3 + vault `GH_PROXY_INTERNAL_SECRET` |
| 8 | Timestamps en macros Dolar/Tasa | H1.3-03, H2.1-06 | `fcf4dc96` |
| 9 | INFLACION -> INFLACION MENSUAL | H1.3-07 | `fcf4dc96` |
| 10 | Banner periodo de receso legislativo | H2.2-04 | `fcf4dc96` |
| 11 | 3 citas legales Generador | H2.7-01, 02, 03 | `c48c75a4` — iniciativa-ciudadana, queja-imss, carta-diputado |
| 12 | TTS silencioso por default Modo Emergencia | H2.8-01 | `5aa5c63d` — consent gate + fallback a texto |
| 13 | Cleanup tablas `_tmp_*` y `_audit_*` | H1.1-12 | 27 tablas eliminadas |

## Pendientes (proxima sesion)

- Backfill embeddings 757 leyes + 20 tesis SCJN (H2.6-01b) — requiere `scripts/backfill_embeddings.py`
- 63 diputados con `partido=NULL` (H2.2-01) — verificar SITL antes de UPDATE
- Scraper `votaciones_senado` (H2.2-02) — implementacion nueva
- 8 scrapers NUNCA OK (H1.1-01) — diagnostico individual
- Banner "datos en transicion" Noticias/Contratos (H2.1-04) — UI change adicional
- Render `tequio_sources` en respuestas IA (H2.6-02)
- PII pre-filtro chat IA (H2.6-03)
- Clasificador de riesgo legal previo (H2.6-06)
- Frontend split (index.html monolito 1.8MB) (H1.1-06)
- Fases 2.3-2.5, 2.10-2.19, 3-7 sin auditar

## Deploy Vercel

Todos los commits arriba estan en `main`. Si Vercel sigue con rollback manual activo, los commits aparecen como Staged hasta que se promueva. Click en **"Undo Rollback"** del banner amarillo en Vercel para auto-promover.

---

**Generado:** 2026-05-21
**Total commits a `main`:** 6 (fcf4dc96, c3c0f795, cfeb1a54, 01efe8be, 5aa5c63d, c48c75a4)
**Total cambios Supabase:** 3 Edge Functions redeploy, 1 RPC reescrita, 27 tablas eliminadas, 1 vault secret nuevo
