#!/usr/bin/env python3
"""Plot a charge log (CSV from the station /status logger) as an SVG:
voltage and current vs. time, two stacked panels sharing the time axis.
Usage: python3 plot_charge.py <log.csv> <out.svg>
No third-party packages needed."""
import csv, sys, math
from datetime import datetime

src, out = sys.argv[1], sys.argv[2]
rows = list(csv.DictReader(open(src)))
t = lambda r: datetime.strptime(r['time'], '%H:%M:%S')
# 只取真的有電流的區段 (排除急停壓著、charging=True 但 0 A 的那幾秒)
live = [r for r in rows if r['charging'] == 'True' and float(r['current']) > 0.5]
t0 = t(live[0])
pts = [((t(r) - t0).total_seconds() / 60, float(r['voltage']), float(r['current'])) for r in live]
tmax = pts[-1][0]

W, PH, ML, MR, MT, GAP, MB = 760, 200, 64, 24, 64, 56, 48
H = MT + PH * 2 + GAP + MB
INK, INK2, GRID, BLUE, ORANGE, BG = '#0b0b0b', '#52514e', '#e4e3df', '#2a78d6', '#eb6834', '#fcfcfb'
x = lambda m: ML + (W - ML - MR) * m / tmax

def nice(lo, hi, n=4):
    step = 10 ** math.floor(math.log10((hi - lo) / n))
    for k in (1, 2, 2.5, 5, 10):
        if (hi - lo) / (step * k) <= n: step *= k; break
    a = math.floor(lo / step) * step; b = math.ceil(hi / step) * step
    ticks = []; v = a
    while v <= b + 1e-9: ticks.append(round(v, 6)); v += step
    return a, b, ticks

def panel(top, idx, color, title, unit, lo, hi):
    a, b, ticks = nice(lo, hi)
    y = lambda v: top + PH - PH * (v - a) / (b - a)
    s = [f'<text x="{ML}" y="{top-12}" font-size="14" font-weight="600" fill="{INK}">{title}</text>']
    for v in ticks:
        s.append(f'<line x1="{ML}" x2="{W-MR}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{GRID}" stroke-width="1"/>')
        s.append(f'<text x="{ML-8}" y="{y(v)+4:.1f}" font-size="11" fill="{INK2}" text-anchor="end">{v:g}</text>')
    s.append(f'<text x="{ML-8}" y="{top-12}" font-size="11" fill="{INK2}" text-anchor="end">{unit}</text>')
    d = ' '.join(f'{"M" if i==0 else "L"}{x(p[0]):.1f},{y(p[idx]):.1f}' for i, p in enumerate(pts))
    s.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>')
    for p, anchor, dx in ((pts[0], 'start', 6), (pts[-1], 'end', -6)):
        s.append(f'<circle cx="{x(p[0]):.1f}" cy="{y(p[idx]):.1f}" r="4" fill="{color}" stroke="{BG}" stroke-width="2"/>')
        s.append(f'<text x="{x(p[0])+dx:.1f}" y="{y(p[idx])-14:.1f}" font-size="12" fill="{INK}" text-anchor="{anchor}">{p[idx]:.2f} {unit}</text>')
    return s

vs = [p[1] for p in pts]; cs = [p[2] for p in pts]
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">',
       f'<rect width="100%" height="100%" fill="{BG}"/>',
       f'<text x="{ML}" y="24" font-size="16" font-weight="700" fill="{INK}">First charge via copper contacts, {live[0]["time"]}–{live[-1]["time"]} ({tmax:.0f} min)</text>']
svg += panel(MT, 1, BLUE, 'Charger output voltage', 'V', min(vs) - 0.3, max(vs) + 0.3)
svg += panel(MT + PH + GAP, 2, ORANGE, 'Charging current (constant-current stage)', 'A', 0, 26)
yb = MT + PH * 2 + GAP
for m in range(0, int(tmax) + 1, 5):
    svg.append(f'<text x="{x(m):.1f}" y="{yb+18}" font-size="11" fill="{INK2}" text-anchor="middle">{m}</text>')
svg.append(f'<text x="{(ML+W-MR)/2}" y="{yb+38}" font-size="12" fill="{INK2}" text-anchor="middle">Time since current started (min)</text>')
svg.append('</svg>')
open(out, 'w').write('\n'.join(svg))
print(f'{len(pts)} points, {tmax:.1f} min, V {vs[0]}->{vs[-1]}, I {min(cs)}-{max(cs)}')
