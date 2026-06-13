#!/usr/bin/env python3
"""
generate_og_image.py - regenerate og-image.png with current Supabase counts.

Bug detectado 13-jun-2026: og-image.png era estatica con "9,101 leyes"
mientras la app servia 9,618. WhatsApp/Telegram renderizan el PNG, no
los meta tags, asi que el preview mentia.

Fix: este script reads count actual desde Supabase, genera PNG nuevo
con PIL, sobreescribe og-image.png. Ejecutar:
  python3 scripts/generate_og_image.py
o desde GH Actions (weekly cron).

Env vars:
  SUPABASE_URL, SUPABASE_ANON_KEY

Refs #2
"""
import os
import sys
import requests
from PIL import Image, ImageDraw, ImageFont

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
ANON = os.environ.get('SUPABASE_ANON_KEY', '')
OUT_PATH = os.environ.get('OG_OUT', 'og-image.png')

if not (SUPABASE_URL and ANON):
    print('FATAL: SUPABASE_URL/SUPABASE_ANON_KEY missing', flush=True)
    sys.exit(1)


def get_kpis():
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/kpis_globales_cache',
        headers={'apikey': ANON, 'Authorization': f'Bearer {ANON}'},
        params={'select': 'payload', 'id': 'eq.1', 'limit': '1'},
        timeout=15,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError('kpis_globales_cache vacio')
    return rows[0].get('payload') or {}


def find_font(size, bold=False):
    """Busca fuente del sistema. Fallback a default si no hay."""
    candidates = []
    if bold:
        candidates += [
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/System/Library/Fonts/Helvetica.ttc',
            'C:/Windows/Fonts/arialbd.ttf',
        ]
    else:
        candidates += [
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/System/Library/Fonts/Helvetica.ttc',
            'C:/Windows/Fonts/arial.ttf',
        ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def fmt_num(n):
    return f'{int(n):,}'


def generate(leyes, chunks_embedded):
    W, H = 1200, 630

    # Fondo: degradado morado oscuro
    img = Image.new('RGB', (W, H), (15, 8, 35))
    px = img.load()
    for y in range(H):
        # Degradado vertical sutil
        r = int(15 + (y / H) * 8)
        g = int(8 + (y / H) * 4)
        b = int(35 + (y / H) * 25)
        for x in range(W):
            # Toque diagonal con verde-azul
            dx = (x / W) * 12
            px[x, y] = (r + int(dx), g + int(dx * 0.5), b + int(dx * 0.8))

    draw = ImageDraw.Draw(img)

    # Badge "CERO INVENCION"
    badge_x, badge_y = 80, 80
    badge_w, badge_h = 240, 38
    draw.rounded_rectangle(
        (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
        radius=4,
        fill=(34, 197, 94),
    )
    # Cuadrito verde a la izquierda dentro
    draw.rectangle((badge_x + 8, badge_y + 10, badge_x + 22, badge_y + 28), fill=(255, 255, 255))
    f_badge = find_font(16, bold=True)
    draw.text((badge_x + 32, badge_y + 9), 'CERO INVENCION', fill=(255, 255, 255), font=f_badge)

    # Logo "TEQUIO"
    f_logo = find_font(60, bold=True)
    draw.text((80, 140), 'TEQUIO', fill=(255, 255, 255), font=f_logo)
    # Underline morado bajo logo
    draw.rectangle((80, 210, 260, 215), fill=(168, 85, 247))

    # Numero gigante
    n_text = fmt_num(leyes)
    f_num = find_font(140, bold=True)
    draw.text((80, 240), n_text, fill=(255, 255, 255), font=f_num)

    # Subtitulo
    f_sub = find_font(32, bold=True)
    draw.text((80, 395), 'leyes mexicanas verificables', fill=(216, 180, 254), font=f_sub)

    # Bullets
    f_bul = find_font(20)
    bullets = [
        '+ 300,000 tesis SCJN buscables con IA',
        '+ Congreso en vivo · contratos federales',
        '+ Sin registro · gratis · sin partidos',
    ]
    y_b = 460
    for b in bullets:
        draw.text((80, y_b), b, fill=(203, 213, 225), font=f_bul)
        y_b += 32

    # Separator line
    draw.rectangle((80, 565, W - 80, 567), fill=(75, 60, 110))

    # Footer
    f_foot = find_font(20, bold=True)
    f_foot_light = find_font(20)
    draw.text((80, 580), 'tequio.app', fill=(168, 85, 247), font=f_foot)
    draw.text((220, 580), 'Plataforma Civica Digital de Mexico  ·  Cero Invencion',
              fill=(148, 163, 184), font=f_foot_light)

    img.save(OUT_PATH, 'PNG', optimize=True)
    print(f'Generado {OUT_PATH} ({os.path.getsize(OUT_PATH)} bytes) con leyes={leyes:,}', flush=True)


def main():
    payload = get_kpis()
    leyes = payload.get('leyes') or 0
    chunks_embedded = payload.get('chunks_embedded') or 0
    if not leyes:
        print('FATAL: leyes count = 0 en kpis_globales_cache', flush=True)
        sys.exit(1)
    print(f'KPIs vivos: leyes={leyes:,} chunks_embedded={chunks_embedded:,}', flush=True)
    generate(leyes, chunks_embedded)


if __name__ == '__main__':
    main()
