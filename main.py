#!/usr/bin/env python3
"""The World in One Day -- Saturday 21 November 2026.

A one-page newspaper summing up the world's news of 21 November 2026,
drawn entirely in code: real Liberation Serif outlines (OFL) embedded
as compact glyph data in glyphs.txt, flat original illustrations, a
dotted world map with numbered pins, a date-check box and a
by-the-clock strip. Standard library only, no network, no clock, no
randomness: two runs write byte-identical PNGs.

Usage: MAKEFUN_OUTPUT_DIR=out python3 main.py   (writes out/entry.png)
"""
import json
import math
import os
import struct
import sys
import zlib

W = 1024
H = 1024
SS = 2  # supersampling factor; the canvas is drawn at 2048 and averaged down

PAPER = (250, 243, 228)
INK = (26, 26, 30)
NAVY = (18, 38, 66)
RED = (178, 34, 34)
GOLD = (186, 138, 38)
RULE = (196, 182, 158)
FAINT = (118, 108, 92)
SKY = (214, 228, 238)
NIGHT = (16, 30, 54)
CREAM = (245, 236, 214)

REGION_COLORS = {
    "WORLD": (31, 58, 95),
    "MIDDLE EAST": (142, 43, 43),
    "EUROPE": (46, 94, 140),
    "AMERICAS": (46, 125, 91),
    "ASIA-PACIFIC": (196, 106, 27),
    "AFRICA": (107, 78, 142),
    "OCEANIA": (166, 124, 0),
}


# ---------------------------------------------------------------- fonts

class FontSet:
    """Liberation Serif outlines loaded from glyphs.txt (one JSON line per
    weight: R, B, I, BI). Each glyph: [advance, [flat contour, ...]] in
    font units (upm 2048). Composites were decomposed at extraction time."""

    def __init__(self, path):
        self.weights = {}
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                self.weights[data["w"]] = (data["upm"], data["g"])
        self._span_cache = {}

    def _glyph(self, weight, ch):
        upm, glyphs = self.weights[weight]
        entry = glyphs.get(str(ord(ch)))
        if entry is None:
            entry = glyphs.get("63")  # '?'
        return upm, entry

    def width(self, text, size, weight):
        total = 0.0
        for ch in text:
            upm, entry = self._glyph(weight, ch)
            total += entry[0] * size / upm
        return total

    def _spans(self, weight, size, ch):
        """Scanline spans of one glyph at 2x, relative to the origin on the
        baseline: list of (dy, x0, x1) in supersampled pixels, plus the
        advance in supersampled pixels. Cached per (weight, size, ch)."""
        key = (weight, size, ch)
        hit = self._span_cache.get(key)
        if hit is not None:
            return hit
        upm, entry = self._glyph(weight, ch)
        adv, contours = entry
        scale = size * SS / upm
        edges = []
        ymin = None
        ymax = None
        for flat in contours:
            pts = [(flat[i] * scale, -flat[i + 1] * scale)
                   for i in range(0, len(flat), 2)]
            n = len(pts)
            for i in range(n):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % n]
                if y0 == y1:
                    continue
                if y0 > y1:
                    x0, y0, x1, y1 = x1, y1, x0, y0
                edges.append((x0, y0, x1, y1))
                ymin = y0 if ymin is None else min(ymin, y0)
                ymax = y1 if ymax is None else max(ymax, y1)
        spans = []
        if edges and ymin is not None:
            y_start = int(math.floor(ymin))
            y_end = int(math.ceil(ymax))
            for yy in range(y_start, y_end):
                ym = yy + 0.5
                xs = []
                for (x0, y0, x1, y1) in edges:
                    if y0 <= ym < y1:
                        xs.append(x0 + (ym - y0) * (x1 - x0) / (y1 - y0))
                xs.sort()
                for i in range(0, len(xs) - 1, 2):
                    a = int(math.ceil(xs[i]))
                    b = int(math.ceil(xs[i + 1]))
                    if b > a:
                        spans.append((yy, a, b))
        result = (spans, int(round(adv * scale)))
        self._span_cache[key] = result
        return result

    def wrap(self, text, size, weight, maxw):
        """Greedy word wrap; returns a list of lines that fit maxw."""
        words = text.split(" ")
        lines = []
        cur = ""
        for word in words:
            trial = word if not cur else cur + " " + word
            if self.width(trial, size, weight) <= maxw or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines


# ---------------------------------------------------------------- canvas

class Canvas:
    def __init__(self):
        self.bw = W * SS
        self.bh = H * SS
        self.buf = bytearray(bytes(PAPER) * (self.bw * self.bh))

    # -- low level helpers (coordinates in 1x pixels) ----------------
    def _row(self, yss, x0ss, x1ss, color):
        if x1ss <= x0ss:
            return
        off = (yss * self.bw + x0ss) * 3
        self.buf[off:off + (x1ss - x0ss) * 3] = bytes(color) * (x1ss - x0ss)

    def rect(self, x, y, w, h, color):
        x0 = max(0, int(round(x * SS)))
        y0 = max(0, int(round(y * SS)))
        x1 = min(self.bw, int(round((x + w) * SS)))
        y1 = min(self.bh, int(round((y + h) * SS)))
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for yy in range(y0, y1):
            off = (yy * self.bw + x0) * 3
            self.buf[off:off + (x1 - x0) * 3] = row

    def hline(self, x, y, w, color, thick=1):
        self.rect(x, y, w, thick, color)

    def vline(self, x, y, h, color, thick=1):
        self.rect(x, y, thick, h, color)

    def poly(self, pts, color):
        """Even-odd scanline fill of one polygon (1x float coords)."""
        self.polys([pts], color)

    def polys(self, contours, color):
        """Even-odd fill of several contours at once (holes work)."""
        edges = []
        ymin = None
        ymax = None
        for pts in contours:
            sp = [(px * SS, py * SS) for (px, py) in pts]
            n = len(sp)
            for i in range(n):
                x0, y0 = sp[i]
                x1, y1 = sp[(i + 1) % n]
                if y0 == y1:
                    continue
                if y0 > y1:
                    x0, y0, x1, y1 = x1, y1, x0, y0
                edges.append((x0, y0, x1, y1))
                ymin = y0 if ymin is None else min(ymin, y0)
                ymax = y1 if ymax is None else max(ymax, y1)
        if not edges:
            return
        y_start = max(0, int(math.floor(ymin)))
        y_end = min(self.bh, int(math.ceil(ymax)))
        for yy in range(y_start, y_end):
            ym = yy + 0.5
            xs = []
            for (x0, y0, x1, y1) in edges:
                if y0 <= ym < y1:
                    xs.append(x0 + (ym - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                a = int(math.ceil(xs[i]))
                b = int(math.ceil(xs[i + 1]))
                a = max(0, a)
                b = min(self.bw, b)
                if b > a:
                    self._row(yy, a, b, color)

    def circle(self, cx, cy, r, color, segments=28):
        pts = []
        for i in range(segments):
            a = 2.0 * math.pi * i / segments
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        self.poly(pts, color)

    def ring(self, cx, cy, r, thick, color):
        outer = []
        inner = []
        for i in range(32):
            a = 2.0 * math.pi * i / 32
            outer.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            inner.append((cx + (r - thick) * math.cos(a),
                          cy + (r - thick) * math.sin(a)))
        self.polys([outer, inner], color)

    def ellipse(self, cx, cy, rx, ry, color, segments=28):
        pts = []
        for i in range(segments):
            a = 2.0 * math.pi * i / segments
            pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
        self.poly(pts, color)

    def line(self, x0, y0, x1, y1, color, thick=1):
        dx = x1 - x0
        dy = y1 - y0
        length = math.hypot(dx, dy)
        if length == 0:
            return
        nx = -dy / length * thick / 2.0
        ny = dx / length * thick / 2.0
        self.poly([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny),
                   (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)], color)

    # -- text --------------------------------------------------------
    def text(self, fonts, x, y, s, size, weight, color, align="left",
             tracking=0.0):
        """Draw text with baseline at (x, y); align left/center/right."""
        if not s:
            return 0.0
        total = fonts.width(s, size, weight) + tracking * max(0, len(s) - 1)
        if align == "center":
            x -= total / 2.0
        elif align == "right":
            x -= total
        xss = int(round(x * SS))
        yss = int(round(y * SS))
        for ch in s:
            spans, adv = fonts._spans(weight, size, ch)
            for (dy, a, b) in spans:
                yy = yss + dy
                if 0 <= yy < self.bh:
                    aa = xss + a
                    bb = xss + b
                    aa = max(0, aa)
                    bb = min(self.bw, bb)
                    if bb > aa:
                        self._row(yy, aa, bb, color)
            xss += adv + int(round(tracking * SS))
        return total

    def para(self, fonts, x, y, lines, size, weight, color, leading=None,
             align="left"):
        """Draw pre-wrapped lines; y is the first baseline. Returns the
        baseline after the last line."""
        if leading is None:
            leading = size * 1.22
        for line in lines:
            self.text(fonts, x, y, line, size, weight, color, align=align)
            y += leading
        return y

    # -- output ------------------------------------------------------
    def downsample(self):
        """Average the 2x buffer down to 1024x1024 RGB bytes."""
        out = bytearray(W * H * 3)
        src = self.buf
        bw = self.bw
        for oy in range(H):
            r0 = (oy * 2) * bw * 3
            r1 = (oy * 2 + 1) * bw * 3
            ooff = oy * W * 3
            for ox in range(W):
                s = ox * 6
                for k in range(3):
                    v = (src[r0 + s + k] + src[r0 + s + 3 + k]
                         + src[r1 + s + k] + src[r1 + s + 3 + k] + 2) // 4
                    out[ooff + ox * 3 + k] = v
        return bytes(out)

    def png_bytes(self):
        raw = self.downsample()
        rows = b"".join(b"\x00" + raw[y * W * 3:(y + 1) * W * 3]
                         for y in range(H))
        comp = zlib.compress(rows, 6)

        def chunk(kind, data):
            out = struct.pack(">I", len(data)) + kind + data
            out += struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            return out

        ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- helpers

def fit_text(fonts, s, size, weight, maxw):
    """Shrink size until the text fits maxw; returns (size, width)."""
    while size > 6 and fonts.width(s, size, weight) > maxw:
        size -= 0.5
    return size, fonts.width(s, size, weight)

# ---------------------------------------------------------------- data

SUBHEAD = ("COP31 talks run into overtime with no mention of fossil fuels "
           "\u00b7 Iran strikes US bases in Bahrain "
           "\u00b7 Russia fires 148 drones at Ukraine overnight")

STATS = [
    ("148", "Russian drones fired in one night (+3 ballistic missiles; "
            "117 shot down or jammed)"),
    ("24", "killed as a boat of mourners capsized in Bayelsa, "
           "Nigeria (24 rescued)"),
    ("-5.3%", "Nasdaq on Friday, wiping out all of 2026's gains"),
    ("98.5%", "of EU goods tariffs scrapped under the EU-Indonesia "
              "trade deal"),
    ("69%", "cut in dengue and chikungunya infections in the Wolbachia "
           "trial (5,757 children)"),
    ("700,000", "displaced in southern Ethiopia"),
]

REGIONS = [
    ("WORLD", 2), ("MIDDLE EAST", 7), ("EUROPE", 9), ("AMERICAS", 9),
    ("ASIA-PACIFIC", 6), ("AFRICA", 5), ("OCEANIA", 2),
]

HEROES = [
    {"num": 1, "art": "cop31",
     "kicker": "1 \u00b7 ANTALYA, TURKEY \u00b7 CLIMATE",
     "headline": "COP31 drifts into overtime with no \u2018fossil fuel\u2019 "
                 "in the draft",
     "summary": "Antalya talks enter a second day of overtime with no "
                "mention of fossil fuels in the draft. 100 civil-society "
                "groups call it \u2018one of the most disappointing "
                "COPs\u2019.",
     "source": "(AP; CommonSpace.eu)"},
    {"num": 2, "art": "bahrain",
     "kicker": "2 \u00b7 BAHRAIN \u00b7 MIDDLE EAST",
     "headline": "Iran strikes US bases in Bahrain; no casualties reported",
     "summary": "The Revolutionary Guard says it hit two US positions in "
                "al-Riffa and Awali. Bahrain confirms the strikes and a "
                "fire at a civilian industrial complex, with no "
                "casualties.",
     "source": "(Reuters; Euronews; Al Jazeera)"},
    {"num": 3, "art": "ukraine",
     "kicker": "3 \u00b7 KYIV, UKRAINE \u00b7 EUROPE",
     "headline": "Russia fires 148 drones in a night; child, 7, killed "
                 "in Kryvyi Rih",
     "summary": "87 of the drones were jet-powered; defences shot down "
                "or jammed 117, with strikes in 19 places. A missile in "
                "Kryvyi Rih killed a seven-year-old and a paramedic "
                "of 20.",
     "source": "(Kyiv Independent; TRT World; Ukrainian Air Force)"},
]

# Grid stories 4-15: num, region, kicker, chip label, icon, location line,
# headline, summary (sources in parentheses at the end).
GRID = [
    {"num": 4, "region": "AMERICAS", "chip": "MARKETS SLUMP",
     "icon": "chart",
     "loc": "4 \u00b7 NEW YORK \u00b7 MARKETS",
     "headline": "Wall Street slumps as Nasdaq loses 5.3%",
     "summary": "Nasdaq falls to 22,617.73, erasing all of 2026's gains "
                "and down 10% in a week. S&P -3.7%, Dow -950; Nvidia "
                "-13.8% on an SEC probe report. (AP; NPR)"},
    {"num": 5, "region": "ASIA-PACIFIC", "chip": "CANADA WARNED",
     "icon": "flags",
     "loc": "5 \u00b7 TAIPEI \u00b7 ASIA-PACIFIC",
     "headline": "Taiwan warns Canada on deeper China ties",
     "summary": "Taipei cautions Prime Minister Mark Carney that closer "
                "relations with Beijing carry risks. (AP; Reuters)"},
    {"num": 6, "region": "WORLD", "chip": "ORBIT AT LAST",
     "icon": "planet",
     "loc": "6 \u00b7 MERCURY \u00b7 SPACE",
     "headline": "BepiColombo enters orbit around Mercury",
     "summary": "After an eight-year journey the probe is captured by "
                "Mercury's gravity at 18:24 UTC, only the second mission "
                "to orbit the innermost planet. (ESA; AP)"},
    {"num": 7, "region": "AFRICA", "chip": "24 DEAD",
     "icon": "boat",
     "loc": "7 \u00b7 BAYELSA, NIGERIA \u00b7 AFRICA",
     "headline": "Boat of mourners capsizes in Bayelsa; 24 dead",
     "summary": "24 killed and 24 rescued in Nigeria's Bayelsa state as "
                "the search goes on; President Tinubu sends condolences. "
                "(AP; DW)"},
    {"num": 8, "region": "AMERICAS", "chip": "BOLSONARO: ICU",
     "icon": "cross",
     "loc": "8 \u00b7 BRAS\u00cdLIA \u00b7 AMERICAS",
     "headline": "Bolsonaro in intensive care in Bras\u00edlia",
     "summary": "The former president, serving 27 years over a coup plot, "
                "is stable and breathing on his own after acute bowel "
                "obstruction. (Reuters; AFP)"},
    {"num": 9, "region": "EUROPE", "chip": "BULGARI, 89",
     "icon": "gem",
     "loc": "9 \u00b7 ROME \u00b7 EUROPE",
     "headline": "Paolo Bulgari dies at 89 in Rome",
     "summary": "Grandson of the founder and chairman since 1984, he led "
                "the jeweller's global expansion until its sale to LVMH "
                "in 2011. (AP; People)"},
    {"num": 10, "region": "MIDDLE EAST", "chip": "3 KILLED",
     "icon": "truck",
     "loc": "10 \u00b7 GAZA CITY \u00b7 MIDDLE EAST",
     "headline": "Israeli strike kills 3 in Gaza City food vehicle",
     "summary": "Three die in a food vehicle in Tel al-Hawa; witnesses "
                "say it held World Central Kitchen papers. Israel says "
                "the dead were Islamic Jihad members. (Al Jazeera; AFP)"},
    {"num": 11, "region": "MIDDLE EAST", "chip": "3 MISSILES STOPPED",
     "icon": "shield",
     "loc": "11 \u00b7 RIYADH \u00b7 MIDDLE EAST",
     "headline": "Saudi defences stop 3 Houthi missiles aimed at Riyadh",
     "summary": "A third straight night of warnings; the coalition hits "
                "back at Hodeidah as the Houthis claim 71 coalition "
                "strikes in 24 hours. (AFP; Middle East Eye)"},
    {"num": 12, "region": "MIDDLE EAST", "chip": "TANKERS HIT",
     "icon": "tanker",
     "loc": "12 \u00b7 STRAIT OF HORMUZ \u00b7 MIDDLE EAST",
     "headline": "Two tankers hit by projectiles in the Strait of Hormuz",
     "summary": "One struck at the stern, one in the engine room near "
                "Khasab, Oman; no casualties. UKMTO counts six tankers "
                "attacked near Hormuz in a week. (AFP; UKMTO)"},
    {"num": 13, "region": "AMERICAS", "chip": "10% CAP VOW",
     "icon": "card",
     "loc": "13 \u00b7 WASHINGTON \u00b7 AMERICAS",
     "headline": "Trump vows to work with Democrats to cap card rates "
                 "at 10%",
     "summary": "Average card rates run at 22.36% (Fed, Q3 2026); no bill "
                "or vote yet. (AP; Axios)"},
    {"num": 14, "region": "ASIA-PACIFIC", "chip": "TRADE DEAL",
     "icon": "deal",
     "loc": "14 \u00b7 BRUSSELS \u00b7 ASIA-PACIFIC",
     "headline": "EU and Indonesia sign trade deal after talks since 2016",
     "summary": "CEPA scraps 98.5% of EU goods tariffs and frees 90.4% "
                "of Indonesian lines from day one; EU exporters to save "
                "~650m euros a year. (European Commission; ANTARA)"},
    {"num": 15, "region": "AMERICAS", "chip": "AI TAX THREAT",
     "icon": "server",
     "loc": "15 \u00b7 WASHINGTON \u00b7 AMERICAS",
     "headline": "Trump threatens big taxes on tech firms building AI "
                 "centres abroad",
     "summary": "Few details in his first press conference after the "
                "Asia tour. (BBC; Guardian)"},
]

# Also-on stories 16-40, one line each (number + headline + source).
ALSO = [
    "16. Cost-of-living protests go on in Europe; 144 held in Paris "
    "(Al Jazeera)",
    "17. Three French police convicted over racist beating of Zecler (BBC)",
    "18. Jury convicts man who started the Palisades fire; 12 died (BBC)",
    "19. Child dies of measles in Virginia, a US first in 2026 (Guardian)",
    "20. Wolbachia mosquitoes cut dengue infections 69% in Brazil "
    "(Straits Times)",
    "21. UN Geneva talks agree to keep working on AI weapons rules (CBC)",
    "22. Estonia shuts three Russia border crossings from 1 Dec "
    "(Straits Times)",
    "23. South Korea ruling party moves to impeach chief justice (Yonhap)",
    "24. Japan feels sidelined at China summit; Takaichi coolly met "
    "(Japan Times)",
    "25. Saudi Arabia and Japan to triple oil stocks to 24m barrels "
    "(Japan Today)",
    "26. Argentina bonds turn distressed; $50bn LNG deal approved "
    "(Buenos Aires Times)",
    "27. Fire at Basra power plant kills 4, injures 35 (Reuters)",
    "28. Iraq, Kata'ib Hezbollah agree joint presence at Jurf al-Sakhar "
    "(Al Jazeera)",
    "29. 700,000 displaced in southern Ethiopia amid Moyale clashes "
    "(Islamic Relief)",
    "30. Kenya starts oil drilling at Amosing 1 in Turkana (The Star)",
    "31. Uganda limits gatherings over Ebola; Congo outbreak feared worst "
    "(WHO)",
    "32. Fighting spreads in eastern Congo, threatening December calm "
    "(The Voice)",
    "33. England 37-12 New Zealand: biggest win over the All Blacks "
    "(BBC Sport)",
    "34. Scotland 67-5 Japan with 11 tries at Murrayfield (BBC Sport)",
    "35. Wallabies 29-26 Wales in Cardiff, ending four straight defeats "
    "(BBC Sport)",
    "36. Harden scores 51, passing Kobe on the NBA 50-point list "
    "(Guardian)",
    "37. BTS win five prizes at the MAMA Awards in Osaka (Yonhap)",
    "38. France investigates Depardieu, Houellebecq over alleged rape "
    "(BBC)",
    "39. North Melbourne reach fourth straight AFLW grand final "
    "(ABC News)",
    "40. Lewis Pugh completes 15km swim across the Strait of Hormuz "
    "(Euronews)",
]

DATE_CHECK = (
    "Wires read up to 12:04 UTC, Sunday 22 November 2026. Every story "
    "here happened, or was first reported, on Saturday 21 November 2026 "
    "in at least one time zone. Left out as other days\u2019 news: the "
    "148-drone barrage of the night of 21\u201322 Nov (reported 22 Nov) "
    "\u00b7 COP31 final adoption, 22 Nov \u00b7 \u2018El Chapo Isidro\u2019 "
    "arrest, 22 Nov \u00b7 Iran\u2019s Patriot-radar claim, 22 Nov, "
    "unverified \u00b7 Sparta II ship fire, 22 Nov \u00b7 Hong Kong vote, "
    "22 Nov \u00b7 Las Vegas GP, 22 Nov \u00b7 ATP final Zverev\u2013"
    "Alcaraz, 22 Nov \u00b7 Liverpool\u2013Man Utd, 22 Nov \u00b7 400+ "
    "drone barrage (first reported 20 Nov) \u00b7 Peru quake, 20 Nov "
    "\u00b7 Russian missiles in Poland, 19\u201320 Nov \u00b7 Shenzhen "
    "summit, 19 Nov \u00b7 Anthropic IPO, 19 Nov \u00b7 GTA VI launch, "
    "19 Nov \u00b7 Kerch Bridge strikes, 17+19 Nov \u00b7 ICJ Myanmar "
    "ruling, 19 Nov."
)

CLOCK_ITEMS = [
    ("00:21", "Saudi coalition says 3 Houthi missiles stopped over Riyadh"),
    ("~06:00", "Ukraine air force morning report: 148 drones + 3 missiles, "
               "117 downed, 19 sites"),
    ("~09:00", "EVITA Wolbachia trial symposium at ASTMH TropMed26"),
    ("14:10", "Nations Championship kick-offs: England v New Zealand, "
             "Scotland v Japan"),
    ("15:00", "Premier League kick-off: Bournemouth v Nottingham Forest"),
    ("18:24", "BepiColombo captured by Mercury\u2019s gravity"),
    ("~19:50", "NEMA: Bayelsa toll rises to 24"),
    ("23:46", "AP reports Paolo Bulgari dead, 89"),
]

FOOTER_SOURCES = (
    "Sources: AP, Reuters, AFP, BBC, Al Jazeera, DW, The Guardian, "
    "European Commission, ANTARA, ESA, Nature, Kyiv Independent, TRT "
    "World, UKMTO, Yonhap, CBC, France 24, ABC News, WHO. Pictures and "
    "map: original, drawn in code."
)

# World-map dots: continents as ellipses in normalised lon/lat space
# (x: -180..180, y: 85..-60). Pins: (lon, lat, label, dx, dy offset).
CONTINENTS = [
    (0.16, 0.22, 0.13, 0.09), (0.28, 0.10, 0.03, 0.03),
    (0.29, 0.55, 0.055, 0.11), (0.49, 0.20, 0.05, 0.06),
    (0.52, 0.45, 0.075, 0.11), (0.68, 0.22, 0.15, 0.10),
    (0.74, 0.48, 0.05, 0.04), (0.80, 0.55, 0.06, 0.025),
    (0.86, 0.62, 0.055, 0.04), (0.465, 0.16, 0.012, 0.025),
    (0.94, 0.72, 0.012, 0.03),
]
PINS = [
    (30.7, 36.9, "1", 8, -4), (50.6, 26.2, "2", 0, -9),
    (30.5, 50.4, "3", 0, -8), (-74.0, 40.7, "4", 8, -8),
    (121.6, 25.0, "5", 0, 0), (8.6, 49.9, "6", -14, -6),
    (6.3, 4.9, "7", 0, 0), (-47.9, -15.8, "8", 0, 0),
    (12.5, 41.9, "9", -10, 8), (34.5, 31.5, "10", 10, 8),
    (46.7, 24.7, "11", -12, 6), (56.2, 26.2, "12", 12, 4),
    (-77.0, 38.9, "13", -10, 8), (4.35, 50.85, "14", -6, 10),
    (-77.0, 38.9, "15", 12, 2),
]


# ---------------------------------------------------------------- rich text

def rich_wrap(fonts, segments, size, maxw):
    """Wrap styled segments [(text, weight, color), ...] into lines.
    Returns a list of lines; each line is a list of (word, weight, color)."""
    words = []
    for (text, weight, color) in segments:
        for word in text.split(" "):
            if word:
                words.append((word, weight, color))
    lines = []
    cur = []
    cur_w = 0.0
    space_w = fonts.width(" ", size, "R")
    for (word, weight, color) in words:
        w = fonts.width(word, size, weight)
        add = w if not cur else space_w + w
        if cur and cur_w + add > maxw:
            lines.append(cur)
            cur = [(word, weight, color)]
            cur_w = w
        else:
            cur.append((word, weight, color))
            cur_w += add
    if cur:
        lines.append(cur)
    return lines


def rich_draw(cv, fonts, x, y, lines, size, leading):
    """Draw wrapped rich lines; y is the first baseline."""
    for line in lines:
        cx = x
        for (word, weight, color) in line:
            w = fonts.width(word, size, weight)
            cv.text(fonts, cx, y, word, size, weight, color)
            cx += w + fonts.width(" ", size, "R")
        y += leading
    return y


# ---------------------------------------------------------------- art

def _burst(cv, cx, cy, r, color):
    pts = []
    for i in range(16):
        rr = r if i % 2 == 0 else r * 0.45
        a = math.pi * i / 8.0
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    cv.poly(pts, color)


def hero_cop31(cv, x, y, w, h):
    cv.rect(x, y, w, h, (168, 198, 214))
    cv.rect(x, y, w, h * 0.42, (196, 216, 230))
    # globe
    gx, gy, gr = x + w * 0.30, y + h * 0.34, h * 0.30
    cv.circle(gx, gy, gr, (46, 110, 130))
    cv.circle(gx, gy, gr * 0.72, (88, 158, 120))
    cv.ellipse(gx, gy, gr, gr * 0.34, (46, 110, 130))
    cv.rect(gx - gr, gy - 1, gr * 2, 2, (200, 225, 225))
    # thermometer
    tx = x + w * 0.80
    cv.rect(tx, y + h * 0.16, 10, h * 0.52, (245, 243, 235))
    cv.rect(tx + 2, y + h * 0.34, 6, h * 0.34, RED)
    cv.circle(tx + 5, y + h * 0.72, 11, RED)
    cv.circle(tx + 5, y + h * 0.72, 7, (200, 60, 60))
    # conference table + delegates
    cv.rect(x, y + h * 0.66, w, h * 0.34, (122, 84, 52))
    cv.rect(x, y + h * 0.66, w, 4, (150, 108, 70))
    for i, col in enumerate([(178, 34, 34), (18, 38, 66), (46, 125, 91),
                             (196, 106, 27), (107, 78, 142), (46, 94, 140)]):
        dx = x + w * (0.10 + i * 0.16)
        cv.circle(dx, y + h * 0.56, 9, col)
        cv.rect(dx - 10, y + h * 0.62, 20, h * 0.08, col)
        cv.rect(dx + 12, y + h * 0.44, 2, 12, (90, 80, 60))
        cv.rect(dx + 14, y + h * 0.44, 12, 8, col)
    cv.rect(x + w * 0.06, y + h * 0.72, 26, 12, (245, 243, 235))
    cv.rect(x + w * 0.06, y + h * 0.76, 18, 2, (150, 140, 120))


def hero_bahrain(cv, x, y, w, h):
    cv.rect(x, y, w, h, NIGHT)
    for (sx, sy) in [(0.08, 0.12), (0.22, 0.06), (0.38, 0.16), (0.55, 0.08),
                     (0.70, 0.14), (0.86, 0.07), (0.94, 0.20), (0.30, 0.24)]:
        cv.circle(x + w * sx, y + h * sy, 1.4, (235, 235, 220))
    cv.circle(x + w * 0.82, y + h * 0.20, 14, (232, 226, 200))
    cv.circle(x + w * 0.82, y + h * 0.20, 11, (210, 202, 170))
    # base buildings with lit windows
    cv.rect(x + w * 0.06, y + h * 0.52, w * 0.24, h * 0.48, (52, 62, 84))
    cv.rect(x + w * 0.34, y + h * 0.40, w * 0.22, h * 0.60, (62, 72, 96))
    cv.rect(x + w * 0.60, y + h * 0.56, w * 0.30, h * 0.44, (46, 56, 78))
    for bx in (0.10, 0.16, 0.22, 0.38, 0.44, 0.50, 0.64, 0.72, 0.80):
        for by in (0.58, 0.68, 0.78):
            cv.rect(x + w * bx, y + h * by, 5, 6, (240, 200, 90))
    # explosions + smoke
    _burst(cv, x + w * 0.42, y + h * 0.42, 16, (240, 140, 40))
    _burst(cv, x + w * 0.42, y + h * 0.42, 9, (250, 210, 80))
    _burst(cv, x + w * 0.72, y + h * 0.50, 11, (230, 110, 40))
    cv.circle(x + w * 0.42, y + h * 0.30, 10, (120, 120, 125))
    cv.circle(x + w * 0.48, y + h * 0.24, 8, (140, 140, 145))
    # drone silhouette
    dx, dy = x + w * 0.60, y + h * 0.16
    cv.rect(dx - 12, dy - 1, 24, 2, (220, 220, 210))
    cv.rect(dx - 2, dy - 6, 4, 12, (220, 220, 210))
    cv.circle(dx - 12, dy, 3, (200, 200, 190))
    cv.circle(dx + 12, dy, 3, (200, 200, 190))


def hero_ukraine(cv, x, y, w, h):
    cv.rect(x, y, w, h, (20, 34, 62))
    cv.rect(x, y, w, h * 0.30, (30, 48, 84))
    cv.circle(x + w * 0.86, y + h * 0.16, 13, (232, 226, 200))
    for (sx, sy) in [(0.10, 0.10), (0.26, 0.06), (0.44, 0.14), (0.60, 0.08),
                     (0.72, 0.18), (0.92, 0.30)]:
        cv.circle(x + w * sx, y + h * sy, 1.3, (235, 235, 220))
    # skyline
    base = y + h * 0.98
    for (bx, bw, bh) in [(0.02, 0.10, 0.34), (0.13, 0.08, 0.46),
                         (0.22, 0.12, 0.30), (0.35, 0.09, 0.52),
                         (0.45, 0.11, 0.38), (0.57, 0.08, 0.48),
                         (0.66, 0.12, 0.32), (0.79, 0.09, 0.44),
                         (0.89, 0.10, 0.30)]:
        cv.rect(x + w * bx, base - h * bh, w * bw, h * bh, (10, 18, 36))
        for wy in (0.12, 0.22):
            cv.rect(x + w * (bx + 0.02), base - h * (bh - wy), 4, 5,
                    (240, 200, 90))
    # drones with red trails
    for (dx, dy) in [(0.24, 0.22), (0.52, 0.14), (0.74, 0.26)]:
        px, py = x + w * dx, y + h * dy
        cv.line(px - 26, py + 8, px - 4, py + 1, (200, 60, 50), 1.5)
        cv.rect(px - 8, py - 1, 16, 2, (225, 225, 215))
        cv.rect(px - 1, py - 8, 2, 16, (225, 225, 215))
    # fire glow at the bottom
    cv.ellipse(x + w * 0.30, base - 2, w * 0.22, 12, (230, 120, 40))
    cv.ellipse(x + w * 0.30, base - 2, w * 0.13, 8, (250, 200, 80))
    _burst(cv, x + w * 0.30, base - 10, 10, (240, 140, 40))


def draw_icon(cv, kind, x, y, w, h, bg):
    cv.rect(x, y, w, h, bg)
    cx, cy = x + w / 2.0, y + h / 2.0
    white = (250, 247, 238)
    dark = (26, 26, 30)
    if kind == "chart":
        cv.rect(x + 8, y + h - 8, 5, 6, dark)
        cv.rect(x + 18, y + h - 13, 5, 11, dark)
        cv.rect(x + 28, y + h - 10, 5, 8, dark)
        cv.line(x + 6, y + 5, x + w - 8, y + h - 6, RED, 2.5)
    elif kind == "flags":
        for (fx, col) in [(0.30, (178, 34, 34)), (0.62, (18, 38, 66))]:
            cv.rect(x + w * fx, y + 4, 2, h - 8, dark)
            cv.rect(x + w * fx + 2, y + 4, 16, 10, col)
    elif kind == "planet":
        cv.circle(cx - 6, cy, 8, (150, 110, 80))
        cv.circle(cx - 8, cy - 2, 3, (120, 85, 60))
        cv.ring(cx - 6, cy, 13, 1.5, dark)
        cv.rect(cx + 8, cy - 10, 5, 5, dark)
    elif kind == "boat":
        cv.poly([(cx - 12, cy + 2), (cx + 12, cy + 2), (cx + 7, cy + 9),
                 (cx - 7, cy + 9)], dark)
        cv.poly([(cx - 1, cy - 9), (cx - 1, cy + 1), (cx + 9, cy + 1)],
                white)
        cv.line(x + 6, cy + 11, x + w - 6, cy + 11, (46, 94, 140), 1.5)
    elif kind == "cross":
        cv.circle(cx, cy, 10, white)
        cv.rect(cx - 2, cy - 7, 4, 14, RED)
        cv.rect(cx - 7, cy - 2, 14, 4, RED)
    elif kind == "gem":
        cv.poly([(cx - 10, cy - 6), (cx + 10, cy - 6), (cx + 6, cy + 8),
                 (cx - 6, cy + 8)], (230, 190, 90))
        cv.poly([(cx - 10, cy - 6), (cx, cy - 6), (cx, cy + 8),
                 (cx - 6, cy + 8)], (200, 160, 60))
        cv.line(cx - 10, cy - 6, cx + 10, cy - 6, white, 1)
    elif kind == "truck":
        cv.rect(cx - 16, cy - 6, 20, 10, dark)
        cv.rect(cx + 5, cy - 3, 9, 7, dark)
        cv.circle(cx - 9, cy + 6, 3, (250, 247, 238))
        cv.circle(cx + 8, cy + 6, 3, (250, 247, 238))
    elif kind == "shield":
        cv.poly([(cx - 9, cy - 8), (cx + 9, cy - 8), (cx + 9, cy + 1),
                 (cx, cy + 10), (cx - 9, cy + 1)], (46, 94, 140))
        cv.rect(cx + 6, cy - 12, 8, 3, RED)
        cv.poly([(cx + 14, cy - 12), (cx + 20, cy - 9), (cx + 14, cy - 6)],
                RED)
    elif kind == "tanker":
        cv.rect(cx - 18, cy + 1, 36, 6, dark)
        cv.rect(cx + 8, cy - 8, 7, 9, dark)
        cv.line(x + 4, cy + 10, x + w - 4, cy + 10, (46, 94, 140), 1.5)
    elif kind == "card":
        cv.rect(cx - 15, cy - 8, 30, 16, (18, 38, 66))
        cv.rect(cx - 15, cy - 5, 30, 4, white)
        cv.rect(cx - 11, cy + 2, 7, 5, (230, 190, 90))
    elif kind == "deal":
        cv.rect(x + w * 0.22, y + 5, 2, h - 10, dark)
        cv.rect(x + w * 0.22 + 2, y + 5, 15, 9, (46, 94, 140))
        cv.rect(x + w * 0.22 + 2, y + 14, 15, 9, (250, 247, 238))
        cv.rect(x + w * 0.60, y + 5, 2, h - 10, dark)
        cv.rect(x + w * 0.60 + 2, y + 5, 15, 9, (178, 34, 34))
        cv.rect(x + w * 0.60 + 2, y + 14, 15, 9, white)
    elif kind == "server":
        for i in range(3):
            yy = y + 3 + i * 8
            cv.rect(cx - 12, yy, 24, 6, dark)
            cv.circle(cx - 8, yy + 3, 1.2, (240, 200, 90))
            cv.circle(cx + 8, yy + 3, 1.2, (240, 200, 90))


def tint(color, frac):
    """Mix a region colour toward paper for strip backgrounds."""
    return tuple(int(color[i] + (PAPER[i] - color[i]) * frac)
                 for i in range(3))

# ---------------------------------------------------------------- layout

MX = 26            # left/right margin
CW = W - 2 * MX    # 972 content width


def draw_map(cv, fonts, x, y, w, h):
    # dotted continents
    step = 7
    dot = (74, 98, 128)
    nx = int(w // step)
    ny = int(h // step)
    for iy in range(ny + 1):
        for ix in range(nx + 1):
            px = ix / max(1, nx)
            py = iy / max(1, ny)
            inside = False
            for (ccx, ccy, rx, ry) in CONTINENTS:
                if ((px - ccx) / rx) ** 2 + ((py - ccy) / ry) ** 2 <= 1.0:
                    inside = True
                    break
            if inside:
                cv.circle(x + ix * step, y + iy * step, 1.7, dot)
    # pins with leader lines for offset ones
    for (lon, lat, label, dx, dy) in PINS:
        px = x + (lon + 180.0) / 360.0 * w
        py = y + (85.0 - lat) / 145.0 * h
        qx, qy = px + dx, py + dy
        if dx or dy:
            cv.line(px, py, qx, qy, (120, 110, 95), 1)
            cv.circle(px, py, 2, RED)
        cv.circle(qx, qy, 7.5, RED)
        cv.circle(qx, qy, 7.5, (250, 247, 238))  # ring trick: redraw below
        cv.circle(qx, qy, 6.5, RED)
        size, _ = fit_text(fonts, label, 8, "B", 11)
        cv.text(fonts, qx, qy + size * 0.36, label, size, "B",
                (255, 255, 255), align="center")


def build(fonts):
    cv = Canvas()
    # -- identity strip ------------------------------------------------
    cv.text(fonts, MX, 24, "THE WORLD IN ONE DAY", 13.5, "B", NAVY,
            tracking=0.6)
    cv.text(fonts, W - MX, 23,
            "40 STORIES \u00b7 7 REGIONS \u00b7 EVERY ONE DATED "
            "SATURDAY 21 NOVEMBER 2026", 9.5, "B", NAVY, align="right")
    cv.hline(MX, 30, CW, INK, 2)
    # -- masthead ------------------------------------------------------
    cv.text(fonts, W / 2, 78, "Saturday 21 November 2026", 50, "B", INK,
            align="center")
    cv.hline(MX, 86, CW, INK, 2)
    cv.hline(MX, 89.5, CW, INK, 1)
    # -- sub-headline --------------------------------------------------
    lines = fonts.wrap(SUBHEAD, 12.5, "I", CW - 40)
    cv.para(fonts, W / 2, 105, lines, 12.5, "I", RED, leading=15.5,
            align="center")
    # -- stat strip ----------------------------------------------------
    y0, y1 = 126, 190
    cv.hline(MX, y0, CW, INK, 1.5)
    cell = CW / 6.0
    for i, (num, cap) in enumerate(STATS):
        cx = MX + cell * i
        if i:
            cv.vline(cx, y0 + 4, y1 - y0 - 8, RULE, 1)
        nsize, _ = fit_text(fonts, num, 20, "B", cell - 12)
        cv.text(fonts, cx + cell / 2, 152, num, nsize, "B", RED,
                align="center")
        cap_lines = fonts.wrap(cap, 8.5, "R", cell - 14)
        cv.para(fonts, cx + cell / 2, 165, cap_lines[:3], 8.5, "R", INK,
                leading=10.5, align="center")
    cv.hline(MX, y1, CW, INK, 1.5)
    # -- region bar ----------------------------------------------------
    y0 = 196
    total = sum(c for _, c in REGIONS)
    rx = MX
    for (label, count) in REGIONS:
        seg = CW * count / total
        col = REGION_COLORS[label]
        cv.rect(rx, y0, seg, 20, col)
        cv.text(fonts, rx + seg / 2, y0 + 13.5,
                "%s %d" % (label, count), 8.5, "B", (255, 255, 255),
                align="center")
        rx += seg
    # -- heroes --------------------------------------------------------
    hy = 222
    hw = 312
    gap = (CW - 3 * hw) / 2.0
    for i, hero in enumerate(HEROES):
        hx = MX + i * (hw + gap)
        art = {"cop31": hero_cop31, "bahrain": hero_bahrain,
               "ukraine": hero_ukraine}[hero["art"]]
        art(cv, hx, hy, hw, 124)
        # chip: number circle + stat chip on the picture
        cv.circle(hx + 13, hy + 13, 10, (250, 247, 238))
        cv.text(fonts, hx + 13, hy + 16.5, str(hero["num"]), 11, "B", RED,
                align="center")
        cv.text(fonts, hx, hy + 140, hero["kicker"], 9.5, "B", RED)
        hl = fonts.wrap(hero["headline"], 15, "B", hw)
        cv.para(fonts, hx, hy + 157, hl[:2], 15, "B", INK, leading=17.5)
        sl = fonts.wrap(hero["summary"], 10, "R", hw)
        cv.para(fonts, hx, hy + 196, sl[:3], 10, "R", INK, leading=12)
        cv.text(fonts, hx, hy + 236, hero["source"], 8.5, "I", FAINT)
    cv.hline(MX, 468, CW, INK, 1)
    # -- grid 4-15 -----------------------------------------------------
    gy = 476
    gw = 234
    ggap = (CW - 4 * gw) / 3.0
    gh = 94
    for idx, story in enumerate(GRID):
        row, col = divmod(idx, 4)
        gx = MX + col * (gw + ggap)
        gy2 = gy + row * (gh + 2)
        colr = REGION_COLORS[story["region"]]
        draw_icon(cv, story["icon"], gx, gy2, gw, 26, tint(colr, 0.82))
        # chip label on the strip
        chip = story["chip"]
        cw = fonts.width(chip, 8, "B") + 10
        cv.rect(gx, gy2, cw, 14, colr)
        cv.text(fonts, gx + 5, gy2 + 10.5, chip, 8, "B", (255, 255, 255))
        cv.circle(gx + gw - 11, gy2 + 13, 9.5, (250, 247, 238))
        cv.text(fonts, gx + gw - 11, gy2 + 16.5, str(story["num"]), 10, "B",
                NAVY, align="center")
        cv.text(fonts, gx, gy2 + 38, story["loc"], 8.5, "B", RED)
        hl = fonts.wrap(story["headline"], 10.5, "B", gw)
        cv.para(fonts, gx, gy2 + 51, hl[:2], 10.5, "B", INK, leading=12)
        sl = fonts.wrap(story["summary"], 8.5, "R", gw)
        cv.para(fonts, gx, gy2 + 72, sl[:3], 8.5, "R", INK, leading=10)
    # -- map + also-on -------------------------------------------------
    my = 764
    cv.text(fonts, MX, my + 11, "WHERE IT HAPPENED \u2014 pins match story "
            "numbers", 10, "B", NAVY)
    cv.text(fonts, 372, my + 11, "ALSO ON SATURDAY 21 NOVEMBER \u2014 25 "
            "MORE STORIES", 10, "B", NAVY)
    cv.hline(MX, my + 16, 330, INK, 1)
    cv.hline(372, my + 16, W - MX - 372, INK, 1)
    draw_map(cv, fonts, MX + 2, my + 22, 326, 118)
    cv.text(fonts, MX, my + 152,
            "Pin 6: mission control, Darmstadt. Pins 13 & 15: Washington.",
            7.5, "I", FAINT)
    col_w = 312
    for j, item in enumerate(ALSO):
        col = 0 if j < 13 else 1
        row = j if j < 13 else j - 13
        ix = 372 + col * (col_w + 14)
        iy = my + 26 + row * 10.6
        dot = item.index(".")
        cv.text(fonts, ix, iy, item[:dot + 1], 8.3, "B", RED)
        num_w = fonts.width(item[:dot + 1], 8.3, "B")
        cv.text(fonts, ix + num_w + 3, iy, item[dot + 1:].strip(), 8.3, "R",
                INK)
    # -- date check ----------------------------------------------------
    dy = 922
    cv.rect(MX, dy, CW, 52, (250, 243, 228))
    cv.rect(MX, dy, CW, 2, RED)
    cv.rect(MX, dy + 50, CW, 2, RED)
    cv.rect(MX, dy, 2, 52, RED)
    cv.rect(MX + CW - 2, dy, 2, 52, RED)
    segs = [("DATE CHECK \u2014 ", "B", RED),
            (DATE_CHECK, "R", INK)]
    lines = rich_wrap(fonts, segs, 9, CW - 24)
    rich_draw(cv, fonts, MX + 12, dy + 14, lines, 9, 11.5)
    # -- by the clock --------------------------------------------------
    cy = 982
    segs = [("BY THE CLOCK \u00b7 UTC  ", "B", RED)]
    for (t, txt) in CLOCK_ITEMS:
        segs.append((t + " ", "B", RED))
        segs.append((txt + " \u00b7 ", "R", INK))
    lines = rich_wrap(fonts, segs, 8.8, CW)
    rich_draw(cv, fonts, MX, cy + 11, lines, 8.8, 11)
    # -- footer --------------------------------------------------------
    cv.hline(MX, 1012, CW, INK, 1)
    cv.text(fonts, MX, 1021, FOOTER_SOURCES, 7.8, "R", FAINT)
    return cv


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    fonts = FontSet(os.path.join(here, "glyphs.txt"))
    cv = build(fonts)
    outdir = os.environ.get("MAKEFUN_OUTPUT_DIR", "out")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "entry.png")
    with open(path, "wb") as fh:
        fh.write(cv.png_bytes())
    print("wrote", path)


if __name__ == "__main__":
    main()
