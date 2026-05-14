#!/usr/bin/env python3
"""
scrape_cerrar_quemones.py — Fase 4.1.D
========================================
Cron diario. Cierra votaciones ciudadanas y genera quemones:
  1) Para votaciones_pendientes 'abiertas' SIN votacion_id, intenta ligar
     con votaciones_diputados por similitud de texto.
  2) Para las que YA tienen votacion_id ligado, llama RPC para generar
     quemones y refrescar el ranking.
"""
import os, sys, requests
from datetime import datetime, timedelta

import urllib3
urllib3.disable_warnings()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

if not (SUPABASE_URL and SERVICE_KEY):
    print("ERR: vars de Supabase faltan"); sys.exit(1)

H = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

VENTANA_DIAS = int(os.environ.get('VENTANA_DIAS', '60'))
MIN_SCORE = float(os.environ.get('MIN_SCORE', '0.5'))

print("Tequio · Cron Quemones (cierre + generación)")


def sb_get(path):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=H, timeout=30, verify=False)
    return r.json() if r.ok else []


def sb_rpc(name, body):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/{name}", headers=H, json=body, timeout=120, verify=False)
    try:
        return r.json() if r.ok else {'error': r.text[:300], 'status': r.status_code}
    except Exception:
        return {'error': r.text[:300], 'status': r.status_code}


def sb_patch(path, body):
    h2 = {**H, 'Prefer': 'return=minimal'}
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{path}", headers=h2, json=body, timeout=30, verify=False)
    return r.ok


def normaliza(s):
    s = (s or '').lower()
    rep = {'á':'a','é':'e','í':'i','ó':'o','ú':'u','ñ':'n'}
    for k,v in rep.items():
        s = s.replace(k, v)
    out = []
    for c in s:
        if c.isalnum() or c == ' ':
            out.append(c)
    return ' '.join(''.join(out).split())[:400]


STOPWORDS = {
    'que','reforman','adicionan','derogan','diversas','disposiciones','decreto',
    'por','el','la','los','las','en','de','del','y','o','para','con','una','un',
    'lo','general','particular','articulo','articulos','ley','leyes','materia',
    'mexicanos','federal','union','dictamen','iniciativa','reforma','adiciona',
    'fraccion','parrafo','inciso','sobre','codigo','este','esta','estado',
}


def palabras_significativas(s):
    norm = normaliza(s)
    return set(w for w in norm.split() if len(w) > 4 and w not in STOPWORDS)


def main():
    t0 = datetime.now()

    # ── 1) Ligar pendientes sin votacion_id ───────────────────────────
    sin_ligar = sb_get('votaciones_pendientes?estado=eq.abierta&votacion_id=is.null&select=id,titulo,fecha_propuesta&limit=200')
    print(f"\n[1] Pendientes sin ligar: {len(sin_ligar)}")

    ventana = (datetime.now() - timedelta(days=VENTANA_DIAS)).date().isoformat()
    reales = sb_get(f"votaciones_diputados?fecha=gte.{ventana}&select=votacion_id,asunto,fecha&limit=500")
    print(f"    Votaciones reales en ventana ({VENTANA_DIAS}d): {len(reales)}")

    vinculadas = 0
    for p in sin_ligar:
        pal_p = palabras_significativas(p['titulo'])
        if len(pal_p) < 3:
            continue
        mejor, mejor_score = None, 0.0
        for r in reales:
            pal_r = palabras_significativas(r['asunto'])
            if not pal_r:
                continue
            inter = pal_p & pal_r
            score = len(inter) / max(len(pal_p | pal_r), 1)  # Jaccard
            if score > mejor_score:
                mejor_score = score
                mejor = r
        if mejor and mejor_score >= MIN_SCORE:
            if sb_patch(f"votaciones_pendientes?id=eq.{p['id']}", {'votacion_id': mejor['votacion_id']}):
                vinculadas += 1
                print(f"    ✓ p{p['id']} → real {mejor['votacion_id']} (jaccard={mejor_score:.2f})")

    print(f"    Vinculadas: {vinculadas}")

    # ── 2) Cerrar y generar quemones ─────────────────────────────────
    ligadas = sb_get('votaciones_pendientes?estado=eq.abierta&votacion_id=not.is.null&select=id,titulo,votacion_id,fecha_votacion&limit=200')
    print(f"\n[2] Pendientes ligadas a cerrar: {len(ligadas)}")

    n_cerradas = 0
    n_quemones = 0
    for p in ligadas:
        res = sb_rpc('cerrar_votacion_y_generar_quemones', {'p_id': p['id']})
        if isinstance(res, dict) and res.get('ok'):
            n_cerradas += 1
            n_quemones += res.get('quemones', 0)
            print(f"    ✓ p{p['id']} cerrada · {res.get('quemones', 0)} quemones (de {res.get('evaluados', 0)} evaluados)")
        else:
            print(f"    ✗ p{p['id']}: {res}")

    # ── 3) Refresh materialized view ────────────────────────────────
    print(f"\n[3] Refresh quemones_ranking...")
    refresh = sb_rpc('refresh_quemones_ranking', {})
    print(f"    {refresh}")

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n══ Resumen ({elapsed:.0f}s) ══")
    print(f"  Vinculadas:    {vinculadas}")
    print(f"  Cerradas:      {n_cerradas}")
    print(f"  Quemones nuevos: {n_quemones}")


if __name__ == '__main__':
    main()
