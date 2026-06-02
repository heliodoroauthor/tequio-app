#!/usr/bin/env python3
"""
ocr_pdfs_parallel.py
====================
Procesa leyes con chunks "basura" (texto < 50 chars) — probablemente PDFs escaneados.
Descarga PDF → pdftotext → tesseract si falla → UPDATE chunks.

Uso: python3 ocr_pdfs_parallel.py <batch_idx> <total_batches>
"""
import os, sys, time, json, tempfile, subprocess, requests
from pathlib import Path

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

if not all([SUPABASE_URL, SERVICE_KEY]):
    print('ERROR: faltan vars SUPABASE_URL/SERVICE_KEY')
    sys.exit(1)

BATCH_IDX = int(sys.argv[1]) if len(sys.argv) > 1 else 0
TOTAL_BATCHES = int(sys.argv[2]) if len(sys.argv) > 2 else 5

HEADERS = {'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}', 'Content-Type': 'application/json'}

print(f'🦎 OCR PDFs parallel · Batch {BATCH_IDX}/{TOTAL_BATCHES}', flush=True)


def find_leyes_que_necesitan_ocr():
    """Lista IDs de leyes con chunks basura y URL válida"""
    # Usar query RPC custom o select directo
    url = f'{SUPABASE_URL}/rest/v1/rpc/leyes_necesitan_ocr'
    try:
        r = requests.post(url, headers=HEADERS, json={'p_limit': 5000}, timeout=30)
        if r.ok:
            return r.json()
    except Exception:
        pass
    
    # Fallback: query directa
    print('  RPC no disponible, usando query directa', flush=True)
    url = f'{SUPABASE_URL}/rest/v1/leyes'
    # Solo munis/estatales con URL no nula  
    params = {
        'select': 'id,nombre,url,entidad',
        'ambito': 'in.(municipal,estatal)',
        'url': 'not.is.null',
        'limit': '5000',
    }
    try:
        r = requests.get(url, headers={**HEADERS, 'Range': '0-4999', 'Range-Unit': 'items'}, params=params, timeout=30)
        if not r.ok:
            return []
        return r.json() or []
    except Exception:
        return []


def download_pdf(url, dest_path):
    """Descarga PDF a archivo temp"""
    try:
        r = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'}, stream=True)
        if not r.ok:
            return False
        with open(dest_path, 'wb') as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return os.path.getsize(dest_path) > 1000
    except Exception:
        return False


def extract_text_pdftotext(pdf_path):
    """pdftotext → texto"""
    try:
        result = subprocess.run(
            ['pdftotext', '-layout', pdf_path, '-'],
            capture_output=True, timeout=30
        )
        text = result.stdout.decode('utf-8', errors='ignore')
        return text.strip()
    except Exception:
        return ''


def extract_text_ocr(pdf_path):
    """Tesseract OCR sobre PDF (convierte a imágenes primero)"""
    text_parts = []
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # PDF → PNGs (max 20 paginas para no comer todo el tiempo)
            subprocess.run([
                'pdftoppm', '-r', '200', '-l', '20',
                pdf_path, f'{tmpdir}/page', '-png'
            ], capture_output=True, timeout=120)
            
            for img in sorted(Path(tmpdir).glob('page*.png')):
                try:
                    result = subprocess.run(
                        ['tesseract', str(img), '-', '-l', 'spa', '--psm', '6'],
                        capture_output=True, timeout=60
                    )
                    page_text = result.stdout.decode('utf-8', errors='ignore').strip()
                    if page_text:
                        text_parts.append(page_text)
                except Exception:
                    continue
        except Exception:
            pass
    return '\n\n'.join(text_parts).strip()


def update_chunks_for_ley(ley_id, full_text):
    """Borra chunks viejos basura, inserta nuevos del texto OCR'd"""
    # Split en chunks ~500 chars
    chunks = []
    sentences = full_text.replace('\n', ' ').split('. ')
    current = ''
    for s in sentences:
        if len(current) + len(s) > 800:
            if current.strip():
                chunks.append(current.strip())
            current = s + '. '
        else:
            current += s + '. '
    if current.strip():
        chunks.append(current.strip())
    
    if not chunks:
        return False
    
    # Delete chunks basura existentes
    url = f'{SUPABASE_URL}/rest/v1/leyes_chunks'
    try:
        # Borrar chunks con texto basura (< 50 chars) para este ley_id
        del_params = {
            'ley_id': f'eq.{ley_id}',
            'texto': 'is.null',  # solo los basura/null
        }
        requests.delete(url, params=del_params, headers=HEADERS, timeout=15)
    except Exception:
        pass
    
    # Insertar nuevos
    rows = []
    for i, txt in enumerate(chunks[:100]):  # cap 100 chunks por ley
        rows.append({
            'ley_id': ley_id,
            'chunk_idx': 10000 + i,  # offset alto para no chocar con chunks existentes
            'texto': txt[:5000],
            'caracteres': len(txt),
        })
    
    try:
        r = requests.post(url, headers=HEADERS, json=rows, timeout=30)
        return r.ok
    except Exception:
        return False


def main():
    leyes = find_leyes_que_necesitan_ocr()
    print(f'  [batch {BATCH_IDX}] Encontradas {len(leyes)} leyes candidatas', flush=True)
    
    # Filtrar solo las de este batch
    my_leyes = [l for l in leyes if l['id'] % TOTAL_BATCHES == BATCH_IDX]
    print(f'  [batch {BATCH_IDX}] Procesando {len(my_leyes)} (mod {TOTAL_BATCHES} == {BATCH_IDX})', flush=True)
    
    processed = 0
    ocr_success = 0
    text_extract_success = 0
    no_text = 0
    
    for ley in my_leyes:
        ley_id = ley['id']
        url = ley.get('url', '')
        nombre = ley.get('nombre', '?')[:60]
        
        if not url or not url.lower().endswith('.pdf'):
            continue
        
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            if not download_pdf(url, tmp_path):
                continue
            
            # Strategy 1: pdftotext
            text = extract_text_pdftotext(tmp_path)
            
            # Strategy 2: OCR si pdftotext da poco
            if len(text) < 300:
                text_ocr = extract_text_ocr(tmp_path)
                if len(text_ocr) > len(text):
                    text = text_ocr
                    if update_chunks_for_ley(ley_id, text):
                        ocr_success += 1
                        print(f'  [{processed}] ✅ OCR: {nombre} ({len(text)} chars)', flush=True)
                else:
                    no_text += 1
            else:
                if update_chunks_for_ley(ley_id, text):
                    text_extract_success += 1
            
            processed += 1
            
            if processed % 10 == 0:
                print(f'  [batch {BATCH_IDX}] processed={processed} text={text_extract_success} ocr={ocr_success} sin_texto={no_text}', flush=True)
        
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    
    print(f'\n═══ SUMMARY OCR batch {BATCH_IDX} ═══', flush=True)
    print(f'  processed:           {processed}', flush=True)
    print(f'  text-extract OK:     {text_extract_success}', flush=True)
    print(f'  OCR OK:              {ocr_success}', flush=True)
    print(f'  no_text:             {no_text}', flush=True)


if __name__ == '__main__':
    main()
