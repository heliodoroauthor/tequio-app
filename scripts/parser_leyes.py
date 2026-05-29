#!/usr/bin/env python3
"""
Parser de leyes mexicanas — extrae chunks artículo-por-artículo desde texto plano.

Soporta los 3 patrones de numeración de artículos más comunes:
  - ARTÍCULO 1.- / ARTÍCULO 1 BIS.-                    (estándar federal y la mayoría estatal)
  - ARTICULO 1o.- / ARTICULO 2o.-                       (ordinal abreviado — leyes NL antiguas)
  - ARTICULO PRIMERO.- / ARTICULO DECIMO PRIMERO.-     (ordinales escritos — leyes orgánicas viejas)

Uso:
  from scripts.parser_leyes import parse_leyes_texto
  chunks = parse_leyes_texto(open('ley.txt').read())
  # cada chunk: {'articulo_num', 'titulo', 'capitulo', 'texto'}

Desarrollado en sesión de auditoría 2026-05-28 (CERO INVENCIÓN).
Probado contra 38 leyes estatales NL (1,422 chunks, 1.30 MB texto).
"""
import re

ORDINALES = (
    'PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|'
    'S[ÉE]PTIMO|OCTAVO|NOVENO|D[ÉE]CIMO|UND[ÉE]CIMO|'
    'DUOD[ÉE]CIMO|VIG[ÉE]SIMO|TRIG[ÉE]SIMO'
)

RE_ARTICULO = re.compile(
    r'^\s*(?:ARTÍCULO|ARTICULO|Art[íi]culo|Art\.)\s+'
    r'('
    r'\d+\s*[°ºoª]?'                              # 1, 1o, 1°, 1ª
    r'(?:\s+(?:BIS|TER|QU[ÁA]TER|QUINQUIES))?'    # 1 BIS, 1 TER
    r'|'
    r'(?:' + ORDINALES + r')'                    # PRIMERO
    r'(?:\s+(?:' + ORDINALES + r'))?'            # DECIMO PRIMERO
    r')'
    r'\s*[\.:]\s*[-—]?',                         # ".-" o ":-"
    re.I
)
RE_TITULO   = re.compile(r'^\s*(TÍTULO|TITULO)\s+([A-ZÁÉÍÓÚÑ]+)\b', re.I)
RE_CAPITULO = re.compile(r'^\s*(CAPÍTULO|CAPITULO)\s+([A-ZÁÉÍÓÚÑ0-9]+)\b', re.I)


def parse_leyes_texto(text: str) -> list[dict]:
    """Parsea texto de una ley en chunks artículo-por-artículo.
    
    Returns:
        Lista de dicts con keys: articulo_num, titulo, capitulo, texto
    """
    chunks = []
    ct, cc, ca, cb = None, None, None, []

    def flush():
        if ca and cb:
            t = re.sub(r'\s+', ' ', ' '.join(l.strip() for l in cb if l.strip())).strip()
            if len(t) > 20:
                chunks.append({
                    'articulo_num': ca,
                    'titulo': ct,
                    'capitulo': cc,
                    'texto': t
                })

    for ln in text.split('\n'):
        mt = RE_TITULO.match(ln)
        mc = RE_CAPITULO.match(ln)
        ma = RE_ARTICULO.match(ln)
        if mt:
            flush(); ca = None; cb = []
            ct = ln.strip(); cc = None
        elif mc:
            flush(); ca = None; cb = []
            cc = ln.strip()
        elif ma:
            flush(); cb = []
            ca = re.sub(r'\s+', ' ', ma.group(1).strip().upper())
            cb.append(ln)
        else:
            if ca:
                cb.append(ln)
    flush()
    return chunks


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Uso: parser_leyes.py <archivo.txt>")
        sys.exit(1)
    text = open(sys.argv[1]).read()
    chunks = parse_leyes_texto(text)
    print(f"Chunks: {len(chunks)}")
    for c in chunks[:3]:
        print(f"  art {c['articulo_num']!r} ({c['titulo']!r}) :: {c['texto'][:80]}...")
