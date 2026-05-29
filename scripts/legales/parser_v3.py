import json, subprocess, re, os
ORDINALES = ('PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|'
    'S[ÉE]PTIMO|OCTAVO|NOVENO|D[ÉE]CIMO|UND[ÉE]CIMO|'
    'DUOD[ÉE]CIMO|VIG[ÉE]SIMO|TRIG[ÉE]SIMO')

# v3: trailing [\.:] is now OPTIONAL (was required)
RE_A = re.compile(r'^\s*(?:ARTÍCULO|ARTICULO|Art[íi]culo|Art\.)\s+('
    r'\d+\s*[°ºoª]?(?:\s+(?:BIS|TER|QU[ÁA]TER|QUINQUIES))?'
    r'|(?:'+ORDINALES+r')(?:\s+(?:'+ORDINALES+r'))?'
    r')(?:\s*[\.:]\s*[-—]?|\s*$|\s+)', re.I)
RE_T = re.compile(r'^\s*(TÍTULO|TITULO)\s+([A-ZÁÉÍÓÚÑ]+)\b', re.I)
RE_C = re.compile(r'^\s*(CAPÍTULO|CAPITULO)\s+([A-ZÁÉÍÓÚÑ0-9]+)\b', re.I)

def parse(text):
    chunks, ct, cc, ca, cb = [], None, None, None, []
    def flush():
        if ca and cb:
            t = re.sub(r'\s+',' ',' '.join(l.strip() for l in cb if l.strip())).strip()
            if len(t) > 20: chunks.append({'articulo_num':ca,'titulo':ct,'capitulo':cc,'texto':t})
    for ln in text.split('\n'):
        mt,mc,ma = RE_T.match(ln), RE_C.match(ln), RE_A.match(ln)
        if mt: flush(); ca=None; cb=[]; ct=ln.strip(); cc=None
        elif mc: flush(); ca=None; cb=[]; cc=ln.strip()
        elif ma: flush(); cb=[]; ca=re.sub(r'\s+',' ',ma.group(1).strip().upper()); cb.append(ln)
        else:
            if ca: cb.append(ln)
    flush(); return chunks

if __name__ == '__main__':
    import sys
    path = sys.argv[1]
    text = open(path, errors='replace').read()
    chunks = parse(text)
    print(f'{path}: {len(chunks)} chunks')
    if chunks:
        print('First:', chunks[0]['articulo_num'], '-', chunks[0]['texto'][:80])
