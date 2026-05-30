# OCR runbook — PDFs escaneados sin capa de texto

Muchos PDFs municipales/estatales mexicanos vienen de scanners (Xerox D35, Visioneer OneTouch) **sin OCR aplicado**. `pdftotext` devuelve 0 bytes. Para extraer el texto necesitamos tesseract.

## Setup (sandbox / fresh VM)

```bash
# Tesseract base viene preinstalado en Ubuntu (`apt list --installed | grep tesseract`)
# Pero el modelo de español NO. Bajarlo a un dir local:
mkdir -p ~/.local/share/tessdata
curl -sL -o ~/.local/share/tessdata/spa.traineddata \
  https://github.com/tesseract-ocr/tessdata_fast/raw/main/spa.traineddata
curl -sL -o ~/.local/share/tessdata/eng.traineddata \
  https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata

export TESSDATA_PREFIX=~/.local/share/tessdata
```

## Pipeline manual (un PDF)

```bash
ID=7384
mkdir -p /tmp/img_$ID
pdftoppm -r 150 input.pdf /tmp/img_$ID/page -jpeg          # PDF → JPGs (150 dpi)
for img in /tmp/img_$ID/page-*.jpg; do
  tesseract -l spa "$img" "${img%.jpg}" 2>/dev/null         # cada página → .txt
done
cat /tmp/img_$ID/page-*.txt > /tmp/test_$ID.txt              # concat
python3 parser_v4.py /tmp/test_$ID.txt                       # parser_v4 con 4 modos
```

## Pipeline integrado (load_yuc.py + OCR fallback)

Si `pdftotext` devuelve <200 bytes:

```python
import subprocess, os
pages_dir = f'/tmp/img_{ley_id}'
os.makedirs(pages_dir, exist_ok=True)
subprocess.run(['pdftoppm','-r','150', pdf_path, f'{pages_dir}/page','-jpeg'], timeout=60)
for img in sorted(os.listdir(pages_dir)):
    if not img.endswith('.jpg'): continue
    out = os.path.join(pages_dir, img[:-4])
    subprocess.run(['tesseract','-l','spa', os.path.join(pages_dir, img), out],
                   capture_output=True, timeout=30,
                   env={**os.environ, 'TESSDATA_PREFIX': os.path.expanduser('~/.local/share/tessdata')})
text = '\n'.join(open(f'{pages_dir}/{f}').read() for f in sorted(os.listdir(pages_dir)) if f.endswith('.txt'))
```

## Topes (para no quemar tiempo en sandbox)

- **40 páginas máx** por PDF (`pdftoppm -f 1 -l 40`). La gran mayoría de reglamentos municipales son <40 pp.
- **Skip si OCR < 200 bytes** — significa que ni siquiera ese hack funcionó (PDF corrupto o protegido).
- **Tesseract tarda ~2–4 s por página a 150 dpi**, así que un protocolo de 13 pp toma ~40 s.

## Casos de uso vistos

| ID    | Doc                                  | Páginas | Bytes OCR | Modo parser_v4 |
|-------|--------------------------------------|---------|-----------|----------------|
| 7313  | Lineamientos Criterios Admin Ambient | 15      | 10,384    | strict         |
| 7384  | Protocolo Atención Migrantes         | 13      | 25,620    | flex           |
| 7386  | Protocolo Festejos Públicos          | 8       | 18,737    | strict         |

## Cuándo NO usar OCR

- Si `pdftotext` ya devuelve >5 KB con artículos detectables — `parser_v4` debería bastar.
- Si el PDF tiene capa de texto pero está **estructurado raro** (TOC dominante, columnas) — primero probar `parser_v4` modo `flex` o `paragraph`, no OCR.

## parser_v4.py — 4 modos en cascada

1. **strict**: solo `Artículo N` clásico. Si encuentra ≥3 chunks, devuelve.
2. **flex**: detecta `TÍTULO`, `CAPÍTULO`, `SECCIÓN`, `NUMERAL`, `1.1`, `1.2.3`. Filtra TOC residue (densidad de puntos > 20%).
3. **paragraph**: chunks por bloques `\n\n` de >250 chars con densidad de puntos <15%.
4. **pagesplit**: último recurso — divide por `\f` (formfeed) y luego en bloques de ~2000 chars.
