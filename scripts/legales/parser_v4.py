"""Parser flexible — detecta encabezados estructurales y filtra basura TOC."""
import re

ORDINALES = ('PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|S[ÉE]PTIMO|OCTAVO|NOVENO|'
    'D[ÉE]CIMO|UND[ÉE]CIMO|DUOD[ÉE]CIMO|VIG[ÉE]SIMO|TRIG[ÉE]SIMO')
RE_A = re.compile(r'^\s*(?:ARTÍCULO|ARTICULO|Art[íi]culo|Art\.)\s+(\d+\s*[°ºoª]?(?:\s+(?:BIS|TER|QU[ÁA]TER|QUINQUIES))?|(?:'+ORDINALES+r')(?:\s+(?:'+ORDINALES+r'))?)\s*[\.:]\s*[-—]?', re.I)
RE_T = re.compile(r'^\s*(TÍTULO|TITULO)\s+([A-ZÁÉÍÓÚÑ]+)\b', re.I)
RE_C = re.compile(r'^\s*(CAPÍTULO|CAPITULO|CAP[ÍI]TULO)\s+([A-ZÁÉÍÓÚÑ0-9]+)\b', re.I)
RE_S = re.compile(r'^\s*(SECCIÓN|SECCION)\s+([A-ZÁÉÍÓÚÑ0-9]+)\b', re.I)
RE_NUM = re.compile(r'^\s*(NUMERAL|LINEAMIENTO|PUNTO)\s+(\d+(?:\.\d+)*)\s*[\.:]', re.I)
RE_DEC = re.compile(r'^\s*(\d+(?:\.\d+){0,3})\s*[\.\-)]\s+([A-Za-záéíóúñÁÉÍÓÚÑ])')

# TOC heuristics: dots filler, page numbers at end
RE_TOC = re.compile(r'\.{4,}|^\s*\d+\s*$|p[áa]g\.\s*\d+\s*$', re.I)
RE_TOC_LINE = re.compile(r'\.{4,}\s*\d+\s*$')

def is_toc_line(ln):
    return bool(RE_TOC_LINE.search(ln))

def strip_toc(text):
    """Remove obvious TOC lines (with dots filler + page nums)."""
    out = []
    in_toc = False
    for ln in text.split('\n'):
        if is_toc_line(ln):
            in_toc = True
            continue
        out.append(ln)
    return '\n'.join(out)

def parse_strict(text):
    chunks, ct, cc, ca, cb = [], None, None, None, []
    def flush():
        if ca and cb:
            t = re.sub(r'\s+',' ',' '.join(l.strip() for l in cb if l.strip())).strip()
            if len(t) > 20: chunks.append({'articulo_num':ca,'titulo':ct,'capitulo':cc,'texto':t[:8000]})
    for ln in text.split('\n'):
        if is_toc_line(ln): continue
        mt,mc,ma = RE_T.match(ln), RE_C.match(ln), RE_A.match(ln)
        if mt: flush(); ca=None; cb=[]; ct=ln.strip(); cc=None
        elif mc: flush(); ca=None; cb=[]; cc=ln.strip()
        elif ma: flush(); cb=[ln]; ca=re.sub(r'\s+',' ',ma.group(1).strip().upper())
        else:
            if ca: cb.append(ln)
    flush(); return chunks

def parse_flex(text):
    """Encabezados estructurales sin Artículo."""
    text = strip_toc(text)
    chunks, ct, cc, ca, cb = [], None, None, None, []
    def flush():
        if ca and cb:
            t = re.sub(r'\s+',' ',' '.join(l.strip() for l in cb if l.strip())).strip()
            # Filter chunks that are mostly TOC residue
            if len(t) > 60 and t.count('.')/len(t) < 0.2:
                chunks.append({'articulo_num':ca[:50],'titulo':ct,'capitulo':cc,'texto':t[:8000]})
    for ln in text.split('\n'):
        mt = RE_T.match(ln); mc = RE_C.match(ln); ms = RE_S.match(ln)
        ma = RE_A.match(ln); mn = RE_NUM.match(ln); md = RE_DEC.match(ln)
        if mt: flush(); ca=f"T{mt.group(2).upper()[:20]}"; cb=[ln]; ct=ln.strip(); cc=None
        elif mc: flush(); ca=f"C{mc.group(2).upper()[:20]}"; cb=[ln]; cc=ln.strip()
        elif ms: flush(); ca=f"S{ms.group(2).upper()[:20]}"; cb=[ln]; cc=ln.strip() if cc is None else cc
        elif mn: flush(); ca=mn.group(2); cb=[ln]
        elif ma: flush(); cb=[ln]; ca=re.sub(r'\s+',' ',ma.group(1).strip().upper())
        elif md: flush(); ca=md.group(1); cb=[ln]
        else:
            if ca: cb.append(ln)
    flush(); return chunks

def parse_paragraph(text):
    """Fallback: párrafos de prosa."""
    text = strip_toc(text)
    # Join wrapped lines: if line ends without period and next starts lowercase, join
    paras = re.split(r'\n\s*\n+', text)
    chunks = []
    idx = 0
    for p in paras:
        t = re.sub(r'\s+',' ', p).strip()
        if len(t) >= 250 and t.count('.')/len(t) < 0.15:
            idx += 1
            chunks.append({'articulo_num':f'P{idx}','titulo':None,'capitulo':None,'texto':t[:8000]})
    return chunks

def parse_pagesplit(text):
    """Último recurso: dividir por páginas (formfeed) y por ~2000 chars."""
    pages = text.split('\f')
    chunks = []
    for i, page in enumerate(pages, 1):
        page = re.sub(r'\s+',' ', page).strip()
        if len(page) < 150: continue
        # Split en chunks de ~2000 chars
        for j in range(0, len(page), 2000):
            t = page[j:j+2000].strip()
            if len(t) > 150 and t.count('.')/len(t) < 0.2:
                chunks.append({'articulo_num':f'pag{i}.{j//2000+1}','titulo':None,'capitulo':None,'texto':t})
    return chunks

def parse_any(text):
    c = parse_strict(text)
    if len(c) >= 3: return c, 'strict'
    c = parse_flex(text)
    if len(c) >= 3: return c, 'flex'
    c = parse_paragraph(text)
    if len(c) >= 2: return c, 'paragraph'
    c = parse_pagesplit(text)
    return c, 'pagesplit' if c else 'none'

if __name__ == '__main__':
    import sys
    text = open(sys.argv[1], errors='replace').read()
    chunks, mode = parse_any(text)
    print(f"Mode: {mode}, chunks: {len(chunks)}")
    if chunks:
        print(f"First [{chunks[0]['articulo_num']}]: {chunks[0]['texto'][:200]}")
        print(f"Last  [{chunks[-1]['articulo_num']}]: {chunks[-1]['texto'][:200]}")
