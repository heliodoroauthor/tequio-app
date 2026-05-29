# scripts/legales — Loaders de leyes mexicanas

Pipeline reusable para cargar leyes federales, estatales y municipales en `public.leyes_chunks`.

## Workflow estándar

1. **Scrape**: extraer lista de PDFs/DOCs del portal del Congreso (ad-hoc por portal)
2. **Match**: fuzzy match (rapidfuzz WRatio ≥0.85) contra shells en `public.leyes`
3. **Load**: descargar archivo, convertir a texto, parsear artículos, insertar chunks

## Archivos

| Script | Uso |
|---|---|
| `parser_v3.py` | Parser v3 — detecta `ARTÍCULO N.-`, `Articulo 1°`, `ARTICULO PRIMERO`, `Art. 1` (sin punct trailing). 95% tasa de parseo. |
| `load_yuc.py` | Loader genérico para PDFs accesibles. Env vars: `MATCHES`, `PROG`, `ENTIDAD`, `FUENTE`, `START`, `SIZE`. Fallback `.PDF` → `.pdf` para case-sensitive paths. |
| `load_federal.py` | Variante para federales: no setea `entidad` (queda NULL). |
| `load_bcs_fast.py` | Loader que asume `.doc` ya convertidos a `.txt` en disco (vía `libreoffice --convert-to txt`). 10× más rápido. |
| `load_oj_doc.py` | Loader para `.doc` desde Orden Jurídico Nacional. Multi-state via env `PREFIX`/`ENTIDAD`. |
| `retry_v3.py` | Re-procesa `.txt` con parser v3 sin re-descargar. Útil para corregir 0-chunk shells. |

## Estados cubiertos (sesión 2026-05-29)

Querétaro · Durango · Morelos · Chiapas · Nayarit · Campeche · Colima · Jalisco ·
Michoacán · Tlaxcala · Hidalgo · Tabasco · Guanajuato · BCS · Tamaulipas · Puebla ·
Coahuila · Veracruz · México · Guerrero · SLP · Sinaloa · Zacatecas · Quintana Roo

## Estrategias descubiertas

- **HTML directo**: 15 estados
- **API JSON**: Sonora (`/publico/ley?expand=archivos`)
- **Joomla `/etiquetas-x-materia/{id}/file`**: Campeche
- **ColdFusion latin-1**: Jalisco (BibliotecaVirtual)
- **AJAX `plantilla_datos.php` latin-1**: Colima
- **OJP `/legislaciondelestado?catid=X&start=Y`**: Puebla (bypass Cloudflare WAF)
- **OJ Nacional `/Documentos/Estatal/{state}/wo{idArchivo}.{pdf|doc}`**: Coa, Ver, Edomex, Gro, SLP, Sin, Zac
- **DOCX modernos + libreoffice**: Tamaulipas
- **DOC binarios + libreoffice batch**: BCS

## Dependencias

```bash
pip install rapidfuzz beautifulsoup4
apt install libreoffice pdftotext
```
