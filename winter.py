#!/usr/bin/env python3
"""
winter.py -- an animated ASCII winter night for your terminal.

A snowy village lane drawn in one-point perspective: pines and cabins with
lit windows, street lamps, a bonfire with sparks and smoke, a moon, drifting
snow with depth, and fireflies. Pure Python standard library, no dependencies.

    python winter.py

Press H while it runs for the key list.
"""

import argparse
import math
import os
import random
import shutil
import sys
import time

INF = 1e9
STAR_Z = 1000.0
MOON_Z = 900.0
HILL_Z = 60.0


# --------------------------------------------------------------------------
# colour helpers (colours are packed 0xRRGGBB ints)
# --------------------------------------------------------------------------

def rgb(r, g, b):
    r = 0 if r < 0 else 255 if r > 255 else int(r)
    g = 0 if g < 0 else 255 if g > 255 else int(g)
    b = 0 if b < 0 else 255 if b > 255 else int(b)
    return (r << 16) | (g << 8) | b


def mix(a, b, t):
    if t <= 0:
        return a
    if t >= 1:
        return b
    ar, ag, ab = a >> 16, (a >> 8) & 255, a & 255
    br, bg, bb = b >> 16, (b >> 8) & 255, b & 255
    return ((int(ar + (br - ar) * t) << 16)
            | (int(ag + (bg - ag) * t) << 8)
            | int(ab + (bb - ab) * t))


def dim(c, k):
    return rgb((c >> 16) * k, ((c >> 8) & 255) * k, (c & 255) * k)


HAZE = rgb(20, 26, 44)
LAMP_WARM = rgb(255, 188, 96)
SNOW_WARM = rgb(255, 196, 112)
FIRE_WARM = (255, 150, 50)

FIRE_N = 48
FIRE_CH = " .':^*+xX#%$@"
REFL_CH = ".-~=+*#0@"


def _fire_lut():
    stops = [(0.0, (56, 8, 4)), (0.25, (170, 28, 8)), (0.5, (240, 96, 14)),
             (0.72, (255, 170, 40)), (0.9, (255, 226, 110)),
             (1.0, (255, 250, 215))]
    lut = []
    for i in range(FIRE_N):
        v = i / (FIRE_N - 1)
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if v <= b:
                t = (v - a) / (b - a)
                lut.append(rgb(*[ca[k] + (cb[k] - ca[k]) * t
                                 for k in range(3)]))
                break
    return lut


FIRE_LUT = _fire_lut()

HELP = [
    "WINTER NIGHT",
    "",
    "up / down      animation speed",
    "left / right   wind",
    "s / S          less / more snow",
    "f / F          smaller / bigger fire",
    "e / E          fewer / more fireflies",
    "g              toggle glow",
    "m              toggle moon",
    "r              new random scene",
    "space          pause",
    "h              toggle this help",
    "q              quit",
]


class Settings:
    def __init__(self):
        self.speed = 1.0
        self.wind = 0.3
        self.snow = 1.0
        self.fire = 1.0
        self.flies = 1.0
        self.glow = True
        self.moon = True


# --------------------------------------------------------------------------
# the scene
# --------------------------------------------------------------------------

class Scene:
    """Builds the static picture once, then animates particles over it.

    render() returns (chars, colours): two flat lists of w*h cells.
    """

    def __init__(self, w, h, seed=None, settings=None):
        self.s = settings or Settings()
        self.seed = random.randrange(1 << 30) if seed is None else seed
        self.rng = random.Random(self.seed ^ 0x5F3759DF)
        self.t = 0.0
        self.show_help = False
        self.level = 0.0
        self.flick = 0.0
        self.wind_eff = self.s.wind
        self.facc = 0.0
        self.heat = None
        self.far = []
        self.near = []
        self.smoke = []
        self.sparks = []
        self.flies = []
        self.resize(w, h)

    # ----------------------------------------------------------------- setup

    def resize(self, w, h):
        self.w = max(24, w)
        self.h = max(10, h)
        self.build()
        self.far, self.near = [], []
        self.smoke, self.sparks, self.flies = [], [], []
        self._sync_particles(initial=True)

    def build(self):
        """(Re)draw the static layer. Deterministic for a given seed."""
        w, h = self.w, self.h
        n = w * h
        rng = random.Random(self.seed)
        self.bch = [' '] * n
        self.bco = [0] * n
        self.dep = [INF] * n
        self.lampL = [0.0] * n
        self.amb = 1.0 if self.s.moon else 0.72
        self.cx = w // 2
        self.hy = max(3, min(h - 5, int(h * 0.55)))
        self.gh = h - self.hy
        self.rw = w * 0.20
        self.windows = []
        self.twinkle = []
        self.emitters = []
        self.lights = []

        self._sky(rng)
        self._moon(rng)
        self._hills(rng)
        self._ground(rng)
        self._fire_geometry()

        objs = self._layout(rng)
        objs.sort(key=lambda o: -o[0])
        for z, kind, x, yb, size, side, sd in objs:
            orng = random.Random(sd)
            if kind == 'pine':
                self._pine(z, x, yb, size, orng)
            elif kind == 'cabin':
                self._cabin(z, x, yb, size, side, orng)
            else:
                self._lamp(z, x, yb, size)
        self._logs()
        self._bake_lights()
        self._finalize()

    def put(self, x, y, c, col, z):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = y * self.w + x
            self.bch[i] = c
            self.bco[i] = col
            self.dep[i] = z
            return i
        return -1

    def tone(self, col, z, lit=False):
        """Fade a colour into the night haze with distance."""
        f = 1.0 / (1.0 + max(0.0, z - 1.0) * 0.17)
        c = mix(HAZE, col, f)
        return c if lit else dim(c, self.amb)

    def base_row(self, z):
        return min(self.h - 1, int(self.hy + self.gh / z - 0.5))

    # ---------------------------------------------------------- static parts

    def _sky(self, rng):
        w = self.w
        count = int(w * self.hy * (0.014 if self.s.moon else 0.024))
        for _ in range(count):
            x = rng.randrange(w)
            y = rng.randrange(self.hy)
            r = rng.random()
            c = '*' if r > 0.965 else '+' if r > 0.91 else rng.choice(".'`,.")
            b = rng.uniform(55, 120) + (60 if c in '*+' else 0)
            if not self.s.moon:
                b *= 1.2
            col = rgb(b * 0.85, b * 0.92, b)
            i = self.put(x, y, c, col, STAR_Z)
            if rng.random() < 0.4:
                self.twinkle.append((i, col, rng.uniform(0, 6.28),
                                     rng.uniform(0.6, 2.6)))

    def _moon(self, rng):
        if not self.s.moon:
            return
        w, h = self.w, self.h
        ry = max(1.6, h * 0.075)
        rx = ry * 2.0
        mx = w * 0.70
        my = max(ry + 1.0, h * 0.15)
        face = rgb(250, 244, 214)
        for y in range(int(my - ry * 1.8) - 1, int(my + ry * 1.8) + 2):
            for x in range(int(mx - rx * 1.8) - 1, int(mx + rx * 1.8) + 2):
                d = math.hypot((x - mx) / rx, (y - my) / ry)
                if d <= 1.0:
                    nz = (math.sin(x * 0.9 + 1.3) * math.sin(y * 1.7 + 0.4)
                          + 0.5 * math.sin((x + y) * 0.6))
                    crater = nz > 0.55
                    shade = 1.0 - 0.2 * d * d - (0.2 if crater else 0.0)
                    if crater:
                        c = 'o'
                    else:
                        c = '@' if d < 0.55 else '0' if d < 0.82 else 'O'
                    self.put(x, y, c, dim(face, shade), MOON_Z)
                elif d <= 1.7 and rng.random() < 0.45 * (1.7 - d) / 0.7:
                    self.put(x, y, rng.choice(".:'."), rgb(104, 110, 128),
                             MOON_Z)

    def _hills(self, rng):
        w, h = self.w, self.h
        ph = [rng.uniform(0, 6.28) for _ in range(4)]
        maxh = h * 0.17

        def ridge(x):
            u = x / float(w)
            v = (math.sin(u * 5.1 + ph[0]) * 0.5
                 + math.sin(u * 11.3 + ph[1]) * 0.28
                 + math.sin(u * 23.7 + ph[2]) * 0.14
                 + math.sin(u * 47.0 + ph[3]) * 0.08)
            v = (v + 1.0) / 2.0
            side = abs(u - 0.5) * 2.0          # valley opens at the centre
            return maxh * (0.25 + 0.75 * v) * (0.4 + 0.6 * side)

        hs = [int(round(ridge(x))) for x in range(-1, w + 1)]
        line = rgb(96, 108, 146)
        cap = rgb(122, 134, 168)
        body = rgb(40, 48, 74)
        for x in range(w):
            left, top, right = hs[x], hs[x + 1], hs[x + 2]
            for k in range(top + 1):
                y = self.hy - 1 - k
                if k == top:
                    if right > top:
                        c = '/'
                    elif left > top:
                        c = '\\'
                    elif left < top and right < top:
                        c = '^'
                    else:
                        c = '_'
                    col = line
                elif k >= top - 2 and rng.random() < 0.45:
                    c, col = rng.choice("*.:'"), cap
                else:
                    c, col = rng.choice("        .:"), body
                self.put(x, y, c, dim(col, self.amb), HILL_Z)

    def _ground(self, rng):
        w, h = self.w, self.h
        cobble = rgb(74, 82, 108)
        bankc = rgb(186, 200, 228)
        fieldc = rgb(120, 134, 170)
        for y in range(self.hy, h):
            z = self.gh / (y - self.hy + 0.5)
            rhw = self.rw / z
            bank = max(1.0, 2.5 / z)
            seg = max(1, int(round(6.0 / z)))
            off = rng.randrange(seg + 2)
            stone = '.' if z > 7 else '-' if z > 3.2 else '_'
            drift = '.' if z > 6 else rng.choice("_-~_")
            run = 0
            for x in range(w):
                d = abs(x - self.cx)
                if d <= rhw:
                    if (x + off) % (seg + 2) >= seg or rng.random() < 0.18:
                        c, col = ' ', 0
                    else:
                        c = stone
                        col = dim(cobble, rng.uniform(0.7, 1.0))
                elif d <= rhw + bank:
                    c = rng.choice("~^*.,_~")
                    col = dim(bankc, rng.uniform(0.7, 1.0))
                else:
                    # wind-blown drifts: short horizontal runs
                    if run > 0:
                        run -= 1
                        c, col = drift, dim(fieldc, 0.8)
                    else:
                        r = rng.random()
                        if r < 0.05:
                            run = rng.randrange(1, 2 + int(9 / z))
                            c, col = drift, dim(fieldc, 0.8)
                        elif r < 0.13:
                            c, col = rng.choice(".,'`.:*"), fieldc
                        else:
                            c, col = ' ', 0
                self.put(x, y, c, self.tone(col, z) if col else 0, z)
        # the far tree line, right on the horizon
        for x in range(w):
            if rng.random() < 0.6 and abs(x - self.cx) > 2:
                self.put(x, self.hy, rng.choice("^^A.:,"),
                         dim(rgb(44, 70, 74), self.amb), 45.0)

    def _fire_geometry(self):
        w, h = self.w, self.h
        self.fz = 2.8
        self.fyb = self.base_row(self.fz)
        self.fh = max(4, int(h * 0.18))
        fw = (self.fh * 2 + 3) | 1
        if fw > w - 6:
            fw = max(5, (w - 6) | 1)
        self.fw = fw
        self.fhm = self.fh * 2
        self.fx0 = self.cx - fw // 2
        if (self.heat is None or len(self.heat) != self.fhm
                or len(self.heat[0]) != fw):
            self.heat = [[0.0] * fw for _ in range(self.fhm)]

    def _layout(self, rng):
        w, h, cx = self.w, self.h, self.cx
        objs = []
        pine_h1 = 0.92 * h
        cabin_h1 = 0.60 * h
        cabin_half1 = cabin_h1 * 0.85

        def add(kind, z, off, size, side):
            x = cx + side * off
            if -size < x < w + size:
                objs.append((z, kind, x, self.base_row(z), size, side,
                             rng.randrange(1 << 30)))

        slots = (1.12, 1.6, 2.3, 3.3, 4.7, 6.7, 9.5, 13.5)
        phase = rng.randrange(2)
        for side in (-1, 1):
            phase ^= 1                      # stagger cabins across the lane
            for k, z0 in enumerate(slots):
                z = z0 * rng.uniform(0.96, 1.05)
                if 1 <= k <= 6 and (k + phase) % 2 == 1:
                    add('cabin', z, (self.rw * 1.35 + cabin_half1) / z,
                        cabin_h1 / z * rng.uniform(0.9, 1.08), side)
                else:
                    size = pine_h1 / z * rng.uniform(0.75, 1.05)
                    near = 2.2 if k == 0 else 1.25
                    add('pine', z, (self.rw * near + size * z * 0.5
                                    * rng.uniform(0.9, 1.3)) / z, size, side)
                # the woods behind the front row
                zb = z * rng.uniform(1.15, 1.3)
                add('pine', zb, self.rw * rng.uniform(3.0, 3.6) / zb,
                    pine_h1 / zb * rng.uniform(0.75, 1.05), side)
                if k >= 2:
                    zc = z * rng.uniform(1.05, 1.4)
                    add('pine', zc, self.rw * rng.uniform(4.6, 6.0) / zc,
                        pine_h1 / zc * rng.uniform(0.7, 1.0), side)
            for zl in (1.55, 2.25, 5.2, 7.6, 11.0):
                add('lamp', zl, self.rw * 1.14 / zl, 0.34 * h / zl, side)
        return objs

    def _pine(self, z, xc, yb, hr, rng):
        w, h = self.w, self.h
        H = int(round(hr))
        xc = int(round(xc))
        if H <= 0:
            return
        needle = rgb(36, 108, 70)
        needle2 = rgb(20, 72, 50)
        snowc = rgb(214, 226, 246)
        bark = rgb(98, 68, 46)
        tn, put = self.tone, self.put
        if H == 1:
            put(xc, yb, rng.choice('^A'), tn(mix(needle, snowc, 0.35), z), z)
            return
        if H == 2:
            put(xc, yb - 1, '^', tn(snowc, z), z)
            put(xc - 1, yb, '/', tn(needle, z), z)
            put(xc, yb, '|', tn(bark, z), z)
            put(xc + 1, yb, '\\', tn(needle, z), z)
            return
        tr = max(1, H // 8)
        cr = H - tr
        hwmax = cr * 0.52
        tiers = max(1, min(7, int(round(cr / 4.5))))
        moon = self.s.moon
        for r in range(cr):
            y = yb - tr - (cr - 1 - r)
            f = (r + 1.0) / cr
            tp = (r * tiers / float(cr)) % 1.0
            hw = int(round(hwmax * f * (0.58 + 0.42 * tp)))
            if hw <= 0:
                put(xc, y, '^' if r == 0 else 'A', tn(snowc, z), z)
                continue
            edge = mix(needle, snowc, 0.5 if tp < 0.4 else 0.18)
            snowp = 0.5 if tp < 0.3 else 0.10
            for x in range(xc - hw, xc + hw + 1):
                q = rng.random()
                if x == xc - hw:
                    c, col = '/', edge
                elif x == xc + hw:
                    c, col = '\\', edge
                elif q < snowp:
                    c = rng.choice("*~*'.=")
                    col = dim(snowc, rng.uniform(0.8, 1.0))
                elif q < snowp + 0.06:
                    c, col = ' ', 0
                else:
                    c = rng.choice("^^AA##%%mw")
                    col = mix(needle2, needle, rng.random())
                if col and moon:
                    col = dim(col, 0.85 + 0.25 * (x - xc) / float(hw))
                if 0 <= x < w and 0 <= y < h:
                    put(x, y, c, tn(col, z) if col else 0, z)
        tw = int(hwmax / 7)
        for r in range(tr):
            for x in range(xc - tw, xc + tw + 1):
                put(x, yb - r, '#' if tw else '|',
                    tn(dim(bark, rng.uniform(0.75, 1.0)), z), z)

    def _cabin(self, z, xc, yb, hr, side, rng):
        H = int(round(hr))
        xc = int(round(xc))
        tn, put = self.tone, self.put
        wood = rgb(128, 86, 54)
        wood2 = rgb(84, 56, 36)
        snowc = rgb(216, 227, 246)
        eave = tn(rgb(150, 166, 198), z)
        lit = mix(HAZE, rgb(255, 208, 98),
                  1.0 / (1.0 + max(0.0, z - 1.0) * 0.04))

        def window(x, y, c='#'):
            i = put(x, y, c, lit, z)
            if i >= 0:
                self.windows.append((i, lit, rng.uniform(0, 6.28),
                                     rng.uniform(1.5, 5.0), z))

        if H <= 1:
            put(xc - 1, yb, '/', eave, z)
            window(xc, yb, 'o')
            put(xc + 1, yb, '\\', eave, z)
            return
        if H == 2:
            put(xc - 1, yb - 1, '/', eave, z)
            put(xc, yb - 1, '^', tn(snowc, z), z)
            put(xc + 1, yb - 1, '\\', eave, z)
            put(xc - 1, yb, '[', tn(wood, z), z)
            window(xc, yb)
            put(xc + 1, yb, ']', tn(wood, z), z)
            return

        bh = max(1, int(round(H * 0.45)))
        rh = H - bh
        half = max(2, int(round(H * 0.8)))
        over = 1 if H < 8 else 2
        roof_hw = [int(round((r + 0.5) / rh * (half + over)))
                   for r in range(rh)]
        for r in range(rh):
            y = yb - bh - (rh - 1 - r)
            hw = roof_hw[r]
            if hw == 0:
                put(xc, y, '^', tn(snowc, z), z)
                continue
            shade = 1.0 - 0.22 * r / float(rh)
            for x in range(xc - hw, xc + hw + 1):
                if x == xc - hw:
                    c, col = '/', eave
                elif x == xc + hw:
                    c, col = '\\', eave
                else:
                    c = rng.choice("~~~==_*~")
                    col = tn(dim(snowc, shade * rng.uniform(0.82, 1.0)), z)
                put(x, y, c, col, z)
        for r in range(bh):
            y = yb - bh + 1 + r
            for x in range(xc - half, xc + half + 1):
                if x in (xc - half, xc + half):
                    c, col = '|', wood2
                else:
                    c = '='
                    col = dim(wood if r % 2 == 0 else wood2,
                              rng.uniform(0.8, 1.0))
                put(x, y, c, tn(col, z), z)
        # door
        dw = max(1, int(round(half * 0.22)))
        dh = max(1, int(round(bh * 0.6)))
        for r in range(dh):
            for k in range(dw):
                put(xc - dw // 2 + k, yb - r, '|' if dw == 1 else '#',
                    tn(rgb(52, 34, 24), z), z)
        # windows
        ww = max(1, int(round(half * 0.36)))
        wh = max(1, int(round(bh * 0.42)))
        wy0 = yb - bh + 1 + max(0, (bh - wh) // 2 - (1 if bh >= 6 else 0))
        for sgn in (-1, 1):
            wx0 = xc + sgn * int(round(half * 0.54)) - ww // 2
            for yy in range(wy0, wy0 + wh):
                for k in range(ww):
                    mullion = ww >= 5 and k == ww // 2
                    window(wx0 + k, yy, '|' if mullion else
                           'H' if ww >= 3 else '#')
            self.lights.append((wx0 + ww // 2, wy0 + wh,
                                max(1.2, 0.2 * self.h / z), 0.5, z))
        # chimney, on the roof slope away from the road
        chx = xc + side * max(1, int(half * 0.55))
        for r in range(rh):
            if roof_hw[r] > abs(chx - xc):
                top = yb - bh - (rh - 1 - r) - 1
                tall = 1 if H < 9 else 2
                for k in range(tall):
                    put(chx, top - k, '#', tn(rgb(120, 108, 108), z), z)
                    if H >= 9:
                        put(chx + 1, top - k, '#',
                            tn(rgb(104, 94, 94), z), z)
                if H >= 5:
                    self.emitters.append((chx, top - tall, z))
                break

    def _lamp(self, z, x, yb, hr):
        H = max(1, int(round(hr)))
        x = int(round(x))
        put = self.put
        pole = self.tone(rgb(92, 98, 118), z)
        glowc = mix(HAZE, rgb(255, 228, 152),
                    1.0 / (1.0 + max(0.0, z - 1.0) * 0.03))
        head_y = yb - (H - 1)
        if H == 1:
            put(x, yb, 'i', glowc, z)
        else:
            for k in range(H - 1):
                put(x, yb - k, '|', pole, z)
            if H >= 7:
                put(x - 1, yb, '_', pole, z)
                put(x + 1, yb, '_', pole, z)
                put(x - 1, head_y, '(', dim(glowc, 0.7), z)
                put(x, head_y, '@', glowc, z)
                put(x + 1, head_y, ')', dim(glowc, 0.7), z)
                put(x, head_y - 1, '_', pole, z)
            else:
                put(x, head_y, 'O' if H >= 3 else 'o', glowc, z)
        self.lights.append((x, head_y, max(1.5, 0.30 * self.h / z), 0.75, z))

    def _logs(self):
        z = self.fz
        span = max(2, int(self.fw * 0.36))
        logc = rgb(112, 70, 38)
        for k in range(-span, span + 1):
            c = '#' if k % 2 == 0 else '='
            self.put(self.cx + k, self.fyb, c,
                     dim(logc, 0.8 + 0.2 * (k % 3 == 0)), z)
        self.put(self.cx - span - 1, self.fyb, 'o', rgb(120, 124, 136), z)
        self.put(self.cx + span + 1, self.fyb, 'o', rgb(120, 124, 136), z)

    def _bake_lights(self):
        if not self.s.glow:
            return
        w, h = self.w, self.h
        L, dep = self.lampL, self.dep
        for lx, ly, r, strength, z in self.lights:
            rx = r * 2.0
            zmin, zmax = z * 0.6, z * 2.2 + 0.5
            for y in range(max(0, int(ly - r)), min(h, int(ly + r) + 1)):
                for x in range(max(0, int(lx - rx)), min(w, int(lx + rx) + 1)):
                    d = math.hypot((x - lx) / rx, (y - ly) / r)
                    if d < 1.0:
                        i = y * w + x
                        if zmin <= dep[i] <= zmax or dep[i] == INF:
                            L[i] += strength * (1.0 - d) ** 2
        bch, bco = self.bch, self.bco
        for i in range(w * h):
            v = L[i]
            if v > 0.02 and bch[i] != ' ':
                bco[i] = mix(bco[i], LAMP_WARM, min(0.7, v))

    def _finalize(self):
        w, h = self.w, self.h
        dep, bch, bco = self.dep, self.bch, self.bco
        self.windows = [(i, col, ph, rate) for i, col, ph, rate, z
                        in self.windows if dep[i] == z]
        self.twinkle = [tw for tw in self.twinkle if dep[tw[0]] == STAR_Z]
        self.fireL = [0.0] * (w * h)
        self.fire_lit = []
        R = h * 0.48
        fx, fy = self.cx, self.fyb - self.fh * 0.4
        for y in range(max(0, int(fy - R)), min(h, int(fy + R) + 1)):
            for x in range(max(0, int(fx - 2 * R)),
                           min(w, int(fx + 2 * R) + 1)):
                d = math.hypot((x - fx) / (2.0 * R), (y - fy) / R)
                if d < 1.0:
                    i = y * w + x
                    wgt = (1.0 - d) ** 2.2 * 0.6
                    self.fireL[i] = wgt
                    if bch[i] != ' ' and dep[i] < 30 and wgt > 0.01:
                        c = bco[i]
                        self.fire_lit.append((i, c >> 16, (c >> 8) & 255,
                                              c & 255, wgt))

    # ------------------------------------------------------------- particles

    def _new_flake(self, top=False):
        rng, w, h = self.rng, self.w, self.h
        z = 0.6 + 11.4 * rng.random() ** 1.7
        x = rng.uniform(0, w)
        y = rng.uniform(-2.0, 0.0) if top else rng.uniform(0, h)
        vy = rng.uniform(5.0, 8.0) * 1.5 / z ** 0.6 * max(0.6, h / 45.0)
        if z < 1.1:
            c = rng.choice("*oO0*")
        elif z < 2.4:
            c = rng.choice("**+o.")
        elif z < 5.0:
            c = rng.choice("*..'+")
        else:
            c = rng.choice("..',`")
        b = min(255.0, 80.0 + 175.0 / z ** 0.9)
        return [x, y, z, vy, rng.uniform(0, 6.28), c,
                rgb(b * 0.88, b * 0.93, b), 16.0 / z ** 0.7, 2.2 / z ** 0.5]

    def _sync_particles(self, initial=False):
        s, rng, w, h = self.s, self.rng, self.w, self.h
        want = int(w * h * 0.05 * s.snow)
        have = len(self.far) + len(self.near)
        while have < want:
            f = self._new_flake(top=not initial)
            (self.far if f[2] > self.fz else self.near).append(f)
            have += 1
        while have > want and (self.far or self.near):
            lst = self.far if len(self.far) >= len(self.near) else self.near
            lst.pop(rng.randrange(len(lst)))
            have -= 1
        want = int(w * h / 420.0 * s.flies)
        while len(self.flies) < want:
            self.flies.append([rng.uniform(0, w), rng.uniform(h * 0.3, h - 1),
                               rng.uniform(-2, 2), rng.uniform(-0.6, 0.6),
                               rng.uniform(0, 6.28), rng.uniform(1.2, 3.0)])
        while len(self.flies) > want:
            self.flies.pop()

    def _fire_step(self):
        fw, fhm = self.fw, self.fhm
        heat, rnd = self.heat, self.rng.random
        lvl = min(1.0, self.level)
        amt = max(0.05, self.s.fire)
        decay = 1.5 / self.fh / (0.35 + 0.65 * amt)
        bottom = heat[fhm - 1]
        mid = (fw - 1) / 2.0
        for x in range(fw):
            u = (x - mid) / mid
            if lvl > 0.02:
                bottom[x] = lvl * (1.0 - u * u) ** 0.4 * (0.7 + 0.3 * rnd())
            else:
                bottom[x] = 0.0
        # cool faster towards the sides so the flames taper
        edge = [0.55 + 1.9 * ((x - mid) / mid) ** 2 for x in range(fw)]
        wb = self.wind_eff
        lean = min(0.5, abs(wb) * 0.2)
        step = -1 if wb > 0 else 1
        for y in range(fhm - 1):
            row, below = heat[y], heat[y + 1]
            for x in range(fw):
                r = rnd()
                sx = x + (1 if r < 0.3 else -1 if r > 0.7 else 0)
                if lean and rnd() < lean:
                    sx += step
                v = ((below[sx] if 0 <= sx < fw else 0.0)
                     - rnd() * decay * edge[x])
                row[x] = v if v > 0.0 else 0.0

    def step(self, dt):
        """Advance the animation by dt seconds (already scaled by speed)."""
        s, rng, w, h = self.s, self.rng, self.w, self.h
        self.t += dt
        t = self.t
        gust = 0.45 * math.sin(t * 0.31) * math.sin(t * 0.113 + 1.7)
        wind = s.wind + gust * (0.4 + abs(s.wind) * 0.5)
        self.wind_eff = wind

        target = 0.0 if s.fire <= 0 else min(1.25, 0.55 + 0.45 * s.fire)
        self.level += (target - self.level) * min(1.0, dt * 2.0)
        self.flick = self.level * (0.84 + 0.10 * math.sin(t * 9.1)
                                   + 0.06 * math.sin(t * 23.7 + 1.0))
        self.facc += dt
        steps = 0
        while self.facc >= 1.0 / 15 and steps < 4:
            self._fire_step()
            self.facc -= 1.0 / 15
            steps += 1
        if steps == 4:
            self.facc = 0.0

        self._sync_particles()

        sin = math.sin
        for lst in (self.far, self.near):
            for f in lst:
                f[1] += f[3] * dt
                f[0] += (wind * f[7] + sin(t * 1.6 + f[4]) * f[8]) * dt
                if f[1] >= h:
                    f[1] -= h + rng.uniform(0.0, 2.0)
                    f[0] = rng.uniform(0, w)
                if f[0] < 0:
                    f[0] += w
                elif f[0] >= w:
                    f[0] -= w

        # smoke: [x, y, vx, vy, age, life, z]
        lvl = self.level
        if lvl > 0.1 and rng.random() < 5.0 * lvl * dt:
            self.smoke.append([self.cx + rng.uniform(-1, 1) * self.fw * 0.15,
                               self.fyb - self.fh * 0.7 * lvl,
                               rng.uniform(-0.6, 0.6),
                               -rng.uniform(2.0, 3.4) * h / 50.0,
                               0.0, rng.uniform(3.0, 6.0), self.fz])
        for ex, ey, ez in self.emitters:
            if rng.random() < 1.6 * dt:
                self.smoke.append([ex + rng.uniform(-0.3, 0.3), ey,
                                   rng.uniform(-0.3, 0.3),
                                   -rng.uniform(0.8, 1.4) * h * 0.06 / ez
                                   - 0.4,
                                   0.0, rng.uniform(2.5, 4.5), ez])
        keep = []
        for p in self.smoke:
            p[4] += dt
            p[0] += (p[2] + wind * 5.0 / p[6] ** 0.5 * min(1.0, p[4])) * dt
            p[1] += p[3] * dt
            p[3] *= 1.0 - 0.25 * dt
            if p[4] < p[5] and p[1] > -1:
                keep.append(p)
        self.smoke = keep

        # sparks: [x, y, vx, vy, age, life]
        if lvl > 0.1 and rng.random() < 8.0 * lvl * dt:
            self.sparks.append([self.cx + rng.uniform(-1, 1) * self.fw * 0.3,
                                self.fyb - 1 - rng.uniform(0, self.fh * 0.5),
                                rng.uniform(-3, 3) + wind * 4.0,
                                -rng.uniform(5, 11) * h / 50.0,
                                0.0, rng.uniform(0.5, 1.5)])
        keep = []
        for p in self.sparks:
            p[4] += dt
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            if p[4] < p[5]:
                keep.append(p)
        self.sparks = keep

        # fireflies: [x, y, vx, vy, phase, rate]
        for p in self.flies:
            p[2] += rng.uniform(-1, 1) * 5.0 * dt
            p[3] += rng.uniform(-1, 1) * 2.0 * dt
            p[2] = max(-3.0, min(3.0, p[2]))
            p[3] = max(-1.0, min(1.0, p[3]))
            p[0] += (p[2] + wind * 0.5) * dt
            p[1] += p[3] * dt
            if p[0] < 0:
                p[0] += w
            elif p[0] >= w:
                p[0] -= w
            if p[1] < h * 0.25:
                p[3] += 2.0 * dt
            elif p[1] > h - 2:
                p[3] -= 2.0 * dt

    # ---------------------------------------------------------------- render

    def render(self):
        w, h, s, t = self.w, self.h, self.s, self.t
        ch = self.bch[:]
        co = self.bco[:]
        dep = self.dep
        flick = self.flick
        glow = s.glow
        sin = math.sin

        if glow and flick > 0.01:
            WR, WG, WB = FIRE_WARM
            for i, r, g, b, wg in self.fire_lit:
                k = wg * flick
                co[i] = ((int(r + (WR - r) * k) << 16)
                         | (int(g + (WG - g) * k) << 8)
                         | int(b + (WB - b) * k))

        for i, col, ph, rate in self.twinkle:
            co[i] = dim(col, 0.55 + 0.45 * sin(t * rate + ph))
        for i, col, ph, rate in self.windows:
            co[i] = dim(col, 0.86 + 0.14 * sin(t * rate + ph)
                        * sin(t * rate * 0.37 + ph * 2.0))

        # firelight shimmering on the lane
        cx = self.cx
        if glow and flick > 0.05:
            yb = self.fyb
            span = max(1, h - 1 - yb)
            nr = len(REFL_CH)
            for y in range(yb + 1, h):
                tt = (y - yb) / float(span)
                hwr = self.fw * 0.36 * (1.0 + 0.9 * tt)
                fade = (1.0 - 0.6 * tt) * flick
                row = y * w
                for x in range(max(0, int(cx - hwr)),
                               min(w, int(cx + hwr) + 1)):
                    u = (x - cx) / hwr
                    nz = (sin(x * 1.9 + y * 0.7 + t * 3.3)
                          * sin(y * 2.1 - t * 2.3 + x * 0.45))
                    v = (fade * max(0.0, 1.0 - u * u) ** 0.7
                         * (0.74 + 0.26 * nz))
                    if v > 0.2:
                        v = v ** 0.6
                        ch[row + x] = REFL_CH[min(nr - 1, int(v * nr))]
                        co[row + x] = FIRE_LUT[min(FIRE_N - 1,
                                                   int(v * 0.92 * FIRE_N))]

        lampL, fireL = self.lampL, self.fireL

        def flakes(lst):
            for f in lst:
                x = int(f[0])
                y = int(f[1])
                if 0 <= x < w and 0 <= y < h:
                    i = y * w + x
                    if dep[i] > f[2]:
                        c = f[6]
                        if glow:
                            lv = lampL[i] + fireL[i] * flick
                            if lv > 0.03:
                                c = mix(c, SNOW_WARM, min(0.85, lv * 1.3))
                        ch[i] = f[5]
                        co[i] = c

        flakes(self.far)

        # flames
        heat, fx0, fw, fhm = self.heat, self.fx0, self.fw, self.fhm
        nch = len(FIRE_CH)
        top = self.fyb - fhm
        for gy in range(fhm):
            y = top + gy
            if y < 0:
                continue
            rowh = heat[gy]
            row = y * w
            for gx in range(fw):
                v = rowh[gx]
                if v > 0.1:
                    x = fx0 + gx
                    if 0 <= x < w:
                        ch[row + x] = FIRE_CH[min(nch - 1, int(v * nch))]
                        co[row + x] = FIRE_LUT[min(FIRE_N - 1,
                                                   int(v * FIRE_N))]

        for p in self.smoke:
            x = int(p[0])
            y = int(p[1])
            if 0 <= x < w and 0 <= y < h:
                i = y * w + x
                if dep[i] > p[6]:
                    a = p[4] / p[5]
                    ch[i] = ('.' if a < 0.1 else 'o' if a < 0.28 else
                             'O' if a < 0.5 else '0' if a < 0.66 else
                             'o' if a < 0.84 else '.')
                    b = 40.0 + 130.0 * (1.0 - a) ** 1.2
                    c = rgb(b * 0.9, b * 0.94, b)
                    if glow:
                        c = mix(c, rgb(255, 160, 84),
                                min(0.8, fireL[i] * flick * 1.4))
                    co[i] = c

        for p in self.sparks:
            x = int(p[0])
            y = int(p[1])
            if 0 <= x < w and 0 <= y < h:
                a = p[4] / p[5]
                i = y * w + x
                ch[i] = ('*' if a < 0.3 else '+' if a < 0.5 else
                         "'" if a < 0.8 else '.')
                co[i] = FIRE_LUT[int((1.0 - a) * 0.85 * (FIRE_N - 1))]

        fly = rgb(255, 236, 130)
        for p in self.flies:
            b = sin(t * p[5] + p[4])
            if b > 0.2:
                x = int(p[0])
                y = int(p[1])
                if 0 <= x < w and 0 <= y < h:
                    i = y * w + x
                    if dep[i] > 1.2:
                        ch[i] = '*' if b > 0.85 else 'o' if b > 0.6 else '.'
                        co[i] = dim(fly, 0.35 + 0.65 * b)

        flakes(self.near)

        if self.show_help:
            self._help(ch, co)
        return ch, co

    def _help(self, ch, co):
        w, h = self.w, self.h
        bw = max(len(line) for line in HELP) + 6
        bh = len(HELP) + 2
        x0 = (w - bw) // 2
        y0 = (h - bh) // 2
        frame = rgb(150, 164, 196)
        text = rgb(232, 236, 244)
        title = rgb(255, 214, 120)
        for r in range(bh):
            y = y0 + r
            if not 0 <= y < h:
                continue
            if r in (0, bh - 1):
                line = '+' + '-' * (bw - 2) + '+'
            else:
                body = HELP[r - 1]
                if r == 1:
                    body = body.center(bw - 6)
                line = '|  ' + body.ljust(bw - 6) + '  |'
            for k, c in enumerate(line):
                x = x0 + k
                if 0 <= x < w:
                    i = y * w + x
                    ch[i] = c
                    edge = r in (0, bh - 1) or k in (0, bw - 1)
                    co[i] = frame if edge else title if r == 1 else text

    def status(self, fps):
        s = self.s
        return (" WINTER NIGHT | speed %.1f | wind %+.1f | snow %.1f | "
                "fire %.1f | flies %.1f | glow %s | moon %s | %d fps | "
                "[H]elp" % (s.speed, s.wind, s.snow, s.fire, s.flies,
                            'on' if s.glow else 'off',
                            'on' if s.moon else 'off', round(fps)))


# --------------------------------------------------------------------------
# terminal front end
# --------------------------------------------------------------------------

def to256(r, g, b):
    if abs(r - g) < 10 and abs(g - b) < 10 and abs(r - b) < 10:
        if r < 8:
            return 16
        if r > 248:
            return 231
        return 232 + int((r - 8) / 247.0 * 24)
    return (16 + 36 * int(r / 255.0 * 5 + 0.5) + 6 * int(g / 255.0 * 5 + 0.5)
            + int(b / 255.0 * 5 + 0.5))


def detect_color():
    env = os.environ
    if env.get('NO_COLOR'):
        return 'mono'
    if env.get('COLORTERM', '').lower() in ('truecolor', '24bit'):
        return 'truecolor'
    if env.get('TERM_PROGRAM') == 'Apple_Terminal':
        return '256'
    if env.get('TERM') in ('linux', 'dumb'):
        return '256'
    return 'truecolor'


class Encoder:
    """Turns the cell buffers into one ANSI string per frame."""

    def __init__(self, mode):
        self.mode = mode
        self.cache = {}
        self.bg = {'truecolor': '\x1b[48;2;0;0;0m', '256': '\x1b[48;5;16m',
                   'mono': ''}[mode]
        self.status_fg = {'truecolor': '\x1b[38;2;128;136;156m',
                          '256': '\x1b[38;5;245m', 'mono': ''}[mode]

    def esc(self, c):
        e = self.cache.get(c)
        if e is None:
            r, g, b = c >> 16, (c >> 8) & 255, c & 255
            if self.mode == 'truecolor':
                e = '\x1b[38;2;%d;%d;%dm' % (r, g, b)
            elif self.mode == '256':
                e = '\x1b[38;5;%dm' % to256(r, g, b)
            else:
                e = ''
            self.cache[c] = e
        return e

    def frame(self, ch, co, w, cols, rows, status):
        out = ['\x1b[?2026h', self.bg]
        add = out.append
        esc = self.esc
        last = -1
        for y in range(rows):
            add('\x1b[%d;1H' % (y + 1))
            base = y * w
            for i in range(base, base + cols):
                c = ch[i]
                if c != ' ':
                    k = co[i] & 0xF8F8F8      # quantise: bounded escape cache
                    if k != last:
                        add(esc(k))
                        last = k
                add(c)
        add('\x1b[%d;1H' % (rows + 1))
        add(self.status_fg)
        add(status[:cols].ljust(cols))
        add('\x1b[?2026l')
        return ''.join(out)


class Terminal:
    def __init__(self):
        self.win = os.name == 'nt'
        self.fd = None
        self.old = None
        self.old_mode = None

    def __enter__(self):
        if self.win:
            import ctypes
            import msvcrt
            self.msvcrt = msvcrt
            try:
                k32 = ctypes.windll.kernel32
                k32.GetStdHandle.restype = ctypes.c_void_p
                self.hout = ctypes.c_void_p(k32.GetStdHandle(-11))
                mode = ctypes.c_uint32()
                if k32.GetConsoleMode(self.hout, ctypes.byref(mode)):
                    self.old_mode = mode.value
                    k32.SetConsoleMode(self.hout, mode.value | 0x0004)
                self.k32 = k32
            except Exception:
                pass
        elif sys.stdin.isatty():
            import termios
            import tty
            self.termios = termios
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        self.write('\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[2J')
        return self

    def __exit__(self, *exc):
        self.write('\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l')
        if self.old is not None:
            self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self.old)
        if self.old_mode is not None:
            self.k32.SetConsoleMode(self.hout, self.old_mode)
        return False

    @staticmethod
    def write(s):
        sys.stdout.write(s)
        sys.stdout.flush()

    def keys(self):
        out = []
        if self.win:
            m = self.msvcrt
            arrows = {'H': 'UP', 'P': 'DOWN', 'K': 'LEFT', 'M': 'RIGHT'}
            while m.kbhit():
                c = m.getwch()
                if c in ('\x00', '\xe0'):
                    out.append(arrows.get(m.getwch(), ''))
                else:
                    out.append(c)
        elif self.fd is not None:
            import select
            if select.select([self.fd], [], [], 0)[0]:
                data = os.read(self.fd, 256).decode('utf-8', 'ignore')
                arrows = {'A': 'UP', 'B': 'DOWN', 'C': 'RIGHT', 'D': 'LEFT'}
                i = 0
                while i < len(data):
                    c = data[i]
                    if c == '\x1b' and data[i + 1:i + 2] in ('[', 'O') \
                            and i + 2 < len(data):
                        out.append(arrows.get(data[i + 2], ''))
                        i += 3
                    else:
                        out.append(c)
                        i += 1
        return [k for k in out if k]


def handle_key(k, scene, state):
    """Apply one key press. Returns False when the program should quit."""
    s = scene.s

    def bump(value, delta, lo, hi):
        return max(lo, min(hi, round(value + delta, 1)))

    if k in ('q', 'Q', '\x1b', '\x03'):
        return False
    if k == ' ':
        state['paused'] = not state['paused']
    elif k in ('UP', '+', '='):
        s.speed = bump(s.speed, 0.1, 0.1, 4.0)
    elif k in ('DOWN', '-', '_'):
        s.speed = bump(s.speed, -0.1, 0.1, 4.0)
    elif k == 'RIGHT':
        s.wind = bump(s.wind, 0.1, -3.0, 3.0)
    elif k == 'LEFT':
        s.wind = bump(s.wind, -0.1, -3.0, 3.0)
    elif k == 's':
        s.snow = bump(s.snow, -0.1, 0.0, 4.0)
    elif k == 'S':
        s.snow = bump(s.snow, 0.1, 0.0, 4.0)
    elif k == 'f':
        s.fire = bump(s.fire, -0.1, 0.0, 3.0)
    elif k == 'F':
        s.fire = bump(s.fire, 0.1, 0.0, 3.0)
    elif k == 'e':
        s.flies = bump(s.flies, -0.1, 0.0, 4.0)
    elif k == 'E':
        s.flies = bump(s.flies, 0.1, 0.0, 4.0)
    elif k in ('g', 'G'):
        s.glow = not s.glow
        scene.build()
    elif k in ('m', 'M'):
        s.moon = not s.moon
        scene.build()
    elif k in ('r', 'R'):
        scene.seed = random.randrange(1 << 30)
        scene.build()
    elif k in ('h', 'H', '?'):
        scene.show_help = not scene.show_help
    return True


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="An animated ASCII winter night for your terminal.")
    p.add_argument('--fps', type=float, default=25.0,
                   help="target frames per second (default 25)")
    p.add_argument('--seed', type=int, help="scene seed (default: random)")
    p.add_argument('--speed', type=float, default=1.0)
    p.add_argument('--wind', type=float, default=0.3)
    p.add_argument('--snow', type=float, default=1.0)
    p.add_argument('--fire', type=float, default=1.0)
    p.add_argument('--flies', type=float, default=1.0)
    p.add_argument('--no-glow', action='store_true')
    p.add_argument('--no-moon', action='store_true')
    p.add_argument('--color', default='auto',
                   choices=('auto', 'truecolor', '256', 'mono'))
    p.add_argument('--size', help="force a size, e.g. 120x40")
    p.add_argument('--frames', type=int, default=0,
                   help="stop after this many frames (for benchmarking)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    st = Settings()
    st.speed = max(0.1, min(4.0, args.speed))
    st.wind = max(-3.0, min(3.0, args.wind))
    st.snow = max(0.0, min(4.0, args.snow))
    st.fire = max(0.0, min(3.0, args.fire))
    st.flies = max(0.0, min(4.0, args.flies))
    st.glow = not args.no_glow
    st.moon = not args.no_moon
    enc = Encoder(detect_color() if args.color == 'auto' else args.color)
    fps_target = max(1.0, min(120.0, args.fps))
    forced = None
    if args.size:
        fw, fh = args.size.lower().split('x')
        forced = (int(fw), int(fh))

    def term_size():
        if forced:
            return forced
        sz = shutil.get_terminal_size((100, 32))
        return sz.columns, sz.lines

    state = {'paused': False}
    with Terminal() as term:
        size = term_size()
        scene = Scene(size[0], size[1] - 1, args.seed, st)
        last = time.perf_counter()
        fps = fps_target
        frames = 0
        try:
            running = True
            while running:
                start = time.perf_counter()
                dt = min(0.1, start - last)
                last = start
                for k in term.keys():
                    if not handle_key(k, scene, state):
                        running = False
                now_size = term_size()
                if now_size != size:
                    size = now_size
                    scene.resize(size[0], size[1] - 1)
                    term.write('\x1b[2J')
                if not state['paused']:
                    scene.step(dt * st.speed)
                ch, co = scene.render()
                cols = min(scene.w, size[0])
                rows = min(scene.h, max(1, size[1] - 1))
                term.write(enc.frame(ch, co, scene.w, cols, rows,
                                     scene.status(fps)))
                frames += 1
                if args.frames and frames >= args.frames:
                    break
                spare = 1.0 / fps_target - (time.perf_counter() - start)
                if spare > 0:
                    time.sleep(spare)
                elapsed = time.perf_counter() - start
                if elapsed > 0:
                    fps += (1.0 / elapsed - fps) * 0.1
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
