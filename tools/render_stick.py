#!/usr/bin/env python3
"""Render StickS3 screens for the article.

Reconstructed render, NOT a device framebuffer capture. It reuses the exact
layout constants, palette and detector from pulse_cyd.py (v1.1-resync) and
drives them with a synthesized PPG waveform. All values are SIMULATED.

Outputs PNG (native 240x135 + 4x) and an animated SVG showing the real
progression: acquiring -> locking -> qualified.
"""
import math, os, random
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "hackster", "screen-renders")
os.makedirs(OUT, exist_ok=True)

W, H = 240, 135
# --- palette, from pulse_cyd.py CONFIG ---
BG, PANEL, GRID, GRID_SOFT = "#060A06", "#0C140C", "#1C4A32", "#143323"
TEXT, LABEL, ANNOT = "#FFFFFF", "#5BE7FF", "#FFE34D"
BLUE, YELLOW, GREEN = "#5BE7FF", "#FFE34D", "#6EF58A"

# --- layout, from pulse_cyd.py LAYOUT ---
SAFE = 5
L, R = SAFE, W - SAFE
HDR_Y, HDR_BOT = 4, 20
BATT_W, BATT_H = 22, 11
BATT_X, BATT_Y = R - (BATT_W + 3), 6
WIFI_W = 12
WIFI_X, WIFI_Y = BATT_X - 6 - WIFI_W, 6
HEART_R, HEART_Y = 7, 10
GX, GY, GW, GH = 7, 24, W - 14, 64
GRID_STEP, GRID_COL_STEP = 16, 34
PY, PH, TILE_W = 93, 36, 112
BPM_X, COACH_X = L, 123
THR_W = 62
THR_X, THR_Y = GX + GW - THR_W - 2, GY + 2
CONF_SEGS, CONF_SEG_W = 10, 4

# --- detector constants, from pulse_cyd.py ---
PULSE_THRESHOLD, MIN_AMP = 550, 20
MIN_BPM, MAX_BPM, MIN_IBI, MAX_IBI = 40, 180, 333, 1500
Q_STEPS, Q_LOCK, Q_UP, Q_DOWN = 12, 10, 3, 1
RANGE_SNAP, FLAT_RANGE, FLAT_AMP = 80, 90, 12
REFRACTORY, SAMPLE_MS, BPM_AVERAGE_N = 250, 20, 10

def font(px):
    for p in ("/System/Library/Fonts/Supplemental/DejaVuSansMono.ttf",
              "/System/Library/Fonts/Menlo.ttc",
              "/System/Library/Fonts/Supplemental/Andale Mono.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, px)
            except Exception:
                pass
    return ImageFont.load_default()

F1, F2 = font(11), font(22)   # ~ device size-1 (15px box) and size-2

def tw(d, s, f):
    return d.textbbox((0, 0), s, font=f)[2]

class Sim:
    """The pulse_cyd.py detector, faithfully ported."""
    def __init__(self):
        self.smin = self.smax = 512
        self.thresh = PULSE_THRESHOLD
        self.peak = self.trough = 512
        self.pulsing = False
        self.amp = 0
        self.ibi_ms = 600
        self.bpm = 0
        self.rates = []
        self.quality = 0
        self.locked = False
        self.first, self.second = True, False
        self.now = 0
        self.last_beat = self.last_qual = 0
        self.flash_until = 0
        self.sig = 512
        self.phase = 0.0

    def beating(self):
        return self.flash_until - self.now > 0

    def sample(self, finger, hr=72, noise=0.10):
        if not finger:
            return int(500 + random.uniform(-4, 4))
        self.phase = (self.phase + SAMPLE_MS / (60000.0 / hr)) % 1.0
        p = self.phase
        v = (math.exp(-((p - 0.16) / 0.075) ** 2)
             + 0.36 * math.exp(-((p - 0.42) / 0.11) ** 2)
             - 0.10 * math.exp(-((p - 0.30) / 0.05) ** 2))
        return int(max(0, min(1023, 470 + v * 170 + random.uniform(-1, 1) * 40 * noise)))

    def step(self, finger, hr=72, noise=0.10):
        self.now += SAMPLE_MS
        s = self.sample(finger, hr, noise)
        self.sig = s
        self.smin = min(self.smin + 1, s)
        self.smax = max(self.smax - 1, s)
        if self.smax - self.smin < RANGE_SNAP:
            self.smin, self.smax = s - RANGE_SNAP // 2, s + RANGE_SNAP // 2
        n = self.now - self.last_beat
        gate = self.ibi_ms * 3 // 5
        if s < self.thresh and n > gate and s < self.trough:
            self.trough = s
        if s > self.thresh and s > self.peak:
            self.peak = s
        if n > REFRACTORY and n > gate and s > self.thresh and not self.pulsing:
            self.pulsing = True
            ibi = n
            self.last_beat = self.now
            if self.second:
                self.second = False
                self.ibi_ms = ibi
            elif self.first:
                self.first, self.second = False, True
            else:
                self.ibi_ms = ibi
                rate = 60000 // ibi if ibi else 0
                good = (MIN_BPM <= rate <= MAX_BPM and MIN_IBI <= ibi <= MAX_IBI
                        and self.amp >= MIN_AMP)
                if good:
                    self.rates.append(ibi)
                    if len(self.rates) > BPM_AVERAGE_N:
                        self.rates.pop(0)
                    self.bpm = 60000 * len(self.rates) // sum(self.rates)
                    self.last_qual = self.now
                    self.quality = min(Q_STEPS, self.quality + Q_UP)
                else:
                    self.quality = max(0, self.quality - Q_DOWN)
                self.locked = self.quality >= Q_LOCK
                if self.locked and good:
                    self.flash_until = self.now + 200
        if s < self.thresh and self.pulsing:
            self.pulsing = False
            self.amp = self.peak - self.trough
            self.thresh = self.trough + self.amp // 2
            self.peak = self.trough = self.thresh
        if self.now - self.last_qual > 3000:
            self.locked, self.quality, self.bpm = False, 0, 0
        return s

    def coach(self):
        rng = self.smax - self.smin
        fresh = (self.now - self.last_qual) <= 1500
        if fresh and self.locked and self.quality >= Q_STEPS:
            return "QUALIFIED", GREEN
        if fresh and (self.locked or self.quality > 0):
            return "LOCKING", YELLOW
        if self.locked:
            return "SIGNAL LOST", YELLOW
        if rng < FLAT_RANGE or self.amp < FLAT_AMP:
            return "NO SIGNAL", BLUE
        if self.amp < MIN_AMP:
            return "HOLD STEADY", BLUE
        if rng >= 120:
            return "GOOD WAVE", BLUE
        return "SEARCHING", BLUE

def heart(d, cx, cy, r, col):
    d.ellipse([cx - r // 2 - r // 2 - 1, cy - r // 3 - r // 2 - 1,
               cx - r // 2 + r // 2 + 1, cy - r // 3 + r // 2 + 1], fill=col)
    d.ellipse([cx + r // 2 - r // 2 - 1, cy - r // 3 - r // 2 - 1,
               cx + r // 2 + r // 2 + 1, cy - r // 3 + r // 2 + 1], fill=col)
    d.polygon([(cx - r, cy - r // 4), (cx + r, cy - r // 4), (cx, cy + r)], fill=col)

def render(sim, trace, batt=76):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    label, col = sim.coach()

    # header
    d.rectangle([0, HDR_BOT - 1, W, HDR_BOT - 1], fill=GRID)
    d.text((L, HDR_Y), "PulseSensor", font=F1, fill=TEXT)
    hx = L + tw(d, "PulseSensor", F1) + 10 + HEART_R
    heart(d, hx, HEART_Y, HEART_R if sim.beating() else HEART_R - 2, col)
    for i in range(3):
        h = 4 + i * 3
        d.rectangle([WIFI_X + i * 4, WIFI_Y + 11 - h, WIFI_X + i * 4 + 2, WIFI_Y + 11], fill=GRID)
    d.rectangle([BATT_X, BATT_Y, BATT_X + BATT_W, BATT_Y + BATT_H], outline=LABEL)
    d.rectangle([BATT_X + BATT_W, BATT_Y + 3, BATT_X + BATT_W + 3, BATT_Y + 8], fill=LABEL)
    fill = batt * (BATT_W - 4) // 100
    d.rectangle([BATT_X + 2, BATT_Y + 2, BATT_X + 2 + fill, BATT_Y + BATT_H - 2], fill=GREEN)

    # graph
    d.rectangle([GX - 2, GY - 2, GX + GW + 2, GY + GH + 2], outline=GRID)
    for x in range(0, GW, GRID_COL_STEP):
        d.line([(GX + x, GY), (GX + x, GY + GH)], fill=GRID_SOFT)
    for y in range(0, GH + 1, GRID_STEP):
        d.line([(GX, GY + y), (GX + GW, GY + y)], fill=GRID_SOFT)
    lo, hi = sim.smin, sim.smax
    if hi <= lo:
        hi = lo + 1
    ty = GY + GH - 4 - (PULSE_THRESHOLD - lo) * (GH - 8) // (hi - lo)
    ty = max(GY + 4, min(GY + GH - 4, ty))
    for x in range(0, GW, 6):
        d.point((GX + x, ty), fill=ANNOT)
    pts = trace[-GW:]
    for i in range(1, len(pts)):
        y0 = GY + GH - 4 - (pts[i - 1] - lo) * (GH - 8) // (hi - lo)
        y1 = GY + GH - 4 - (pts[i] - lo) * (GH - 8) // (hi - lo)
        y0 = max(GY + 4, min(GY + GH - 4, y0))
        y1 = max(GY + 4, min(GY + GH - 4, y1))
        d.line([(GX + i - 1, y0), (GX + i, y1)], fill=col, width=2)
    thr = "THR %4d" % int(sim.thresh)
    d.rectangle([THR_X - 2, THR_Y - 1, THR_X + THR_W, THR_Y + 13], fill=BG)
    d.text((THR_X, THR_Y), thr, font=F1, fill=ANNOT)

    # BPM tile (inverts on beat)
    inv = sim.beating()
    bg = col if inv else PANEL
    d.rectangle([BPM_X, PY, BPM_X + TILE_W, PY + PH], fill=bg,
                outline=(col if sim.locked else GRID))
    d.text((BPM_X + 6, PY + 11), "BPM", font=F1, fill=(BG if inv else LABEL))
    val = str(sim.bpm) if sim.locked else "--"
    d.text((BPM_X + TILE_W - 6 - tw(d, val, F2), PY + 6), val, font=F2,
           fill=(BG if inv else (col if sim.locked else LABEL)))

    # coach tile
    conf = sim.quality * 100 // Q_STEPS
    d.rectangle([COACH_X, PY, COACH_X + TILE_W, PY + PH], fill=PANEL, outline=col)
    d.text((COACH_X + 6, PY + 3), label, font=F1, fill=col)
    filled = conf * CONF_SEGS // 100
    for i in range(CONF_SEGS):
        x = COACH_X + 6 + i * CONF_SEG_W
        d.rectangle([x, PY + 21, x + CONF_SEG_W - 2, PY + 29],
                    fill=(col if i < filled else GRID))
    pct = "%d%%" % conf
    d.text((COACH_X + TILE_W - 6 - tw(d, pct, F1), PY + 18), pct, font=F1, fill=col)
    return img

def save(img, name):
    p = os.path.join(OUT, name + ".png")
    img.save(p)
    img.resize((W * 4, H * 4), Image.NEAREST).save(os.path.join(OUT, name + "-highres.png"))
    return p

def main():
    random.seed(7)
    sim = Sim()
    trace = []
    frames = []          # (trace snapshot, sim snapshot) for the SVG

    def run(n, finger, hr=72, noise=0.10, collect=False):
        for _ in range(n):
            trace.append(sim.step(finger, hr, noise))
            if collect:
                frames.append((list(trace[-GW:]), sim.coach()[1], sim.bpm,
                               sim.quality, sim.beating(), sim.smin, sim.smax))

    run(60, False)                       # settle, no finger
    searching = render(sim, trace)
    save(searching, "sticks3-searching")

    run(250, True, collect=True)         # acquire -> lock
    while not sim.beating():
        run(1, True, collect=True)
    beat = render(sim, trace)
    save(beat, "sticks3-heartbeat")
    while sim.beating():
        run(1, True, collect=True)
    qualified = render(sim, trace)
    save(qualified, "sticks3-qualified")
    run(200, True, collect=True)

    # ---------- animated SVG ----------
    # SMIL cannot animate text content, so each distinct readout is its own
    # <text> toggled by a discrete opacity track.
    step = 4
    sel = frames[::step]
    n = len(sel)
    dur = n * step * SAMPLE_MS / 1000.0
    keys = ";".join("%.4f" % (i / float(n - 1)) for i in range(n))

    polys, cols, labels, bpms = [], [], [], []
    for tr, c, bpm, q, bt, lo, hi in sel:
        if hi <= lo:
            hi = lo + 1
        pts = []
        for i, v in enumerate(tr):
            yy = GY + GH - 4 - (v - lo) * (GH - 8) // (hi - lo)
            pts.append("%d,%d" % (GX + i, max(GY + 4, min(GY + GH - 4, yy))))
        polys.append(" ".join(pts))
        cols.append(c)
        labels.append("QUALIFIED" if q >= Q_STEPS else
                      ("LOCKING" if q > 0 else "NO SIGNAL"))
        bpms.append(str(bpm) if q >= Q_LOCK else "--")

    def track(values, want):
        return ";".join("1" if v == want else "0" for v in values)

    P = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 135" '
         'width="960" height="540" shape-rendering="crispEdges" '
         'font-family="DejaVu Sans Mono, Menlo, monospace">',
         '<rect width="240" height="135" fill="%s"/>' % BG,
         '<text x="%d" y="15" fill="%s" font-size="11">PulseSensor</text>' % (L, TEXT),
         '<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="%s"/>'
         % (GX - 2, GY - 2, GW + 4, GH + 4, GRID)]
    for x in range(0, GW, GRID_COL_STEP):
        P.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>'
                 % (GX + x, GY, GX + x, GY + GH, GRID_SOFT))
    for y in range(0, GH + 1, GRID_STEP):
        P.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>'
                 % (GX, GY + y, GX + GW, GY + y, GRID_SOFT))

    P.append('<polyline fill="none" stroke-width="2" points="%s">' % polys[0])
    P.append('<animate attributeName="points" dur="%.1fs" repeatCount="indefinite" '
             'calcMode="discrete" keyTimes="%s" values="%s"/>' % (dur, keys, ";".join(polys)))
    P.append('<animate attributeName="stroke" dur="%.1fs" repeatCount="indefinite" '
             'calcMode="discrete" keyTimes="%s" values="%s"/>' % (dur, keys, ";".join(cols)))
    P.append('</polyline>')

    # tiles
    P.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="%s"/>'
             % (BPM_X, PY, TILE_W, PH, GRID))
    P.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="%s"/>'
             % (COACH_X, PY, TILE_W, PH, GRID))
    P.append('<text x="%d" y="%d" fill="%s" font-size="11">BPM</text>'
             % (BPM_X + 6, PY + 22, LABEL))

    for v in sorted(set(bpms)):
        P.append('<text x="%d" y="%d" font-size="22" text-anchor="end" opacity="0">%s'
                 '<animate attributeName="opacity" dur="%.1fs" repeatCount="indefinite" '
                 'calcMode="discrete" keyTimes="%s" values="%s"/>'
                 '<animate attributeName="fill" dur="%.1fs" repeatCount="indefinite" '
                 'calcMode="discrete" keyTimes="%s" values="%s"/></text>'
                 % (BPM_X + TILE_W - 8, PY + 28, v, dur, keys, track(bpms, v),
                    dur, keys, ";".join(cols)))
    for v in sorted(set(labels)):
        P.append('<text x="%d" y="%d" font-size="11" opacity="0">%s'
                 '<animate attributeName="opacity" dur="%.1fs" repeatCount="indefinite" '
                 'calcMode="discrete" keyTimes="%s" values="%s"/>'
                 '<animate attributeName="fill" dur="%.1fs" repeatCount="indefinite" '
                 'calcMode="discrete" keyTimes="%s" values="%s"/></text>'
                 % (COACH_X + 6, PY + 14, v, dur, keys, track(labels, v),
                    dur, keys, ";".join(cols)))
    P.append('<text x="236" y="132" font-size="5" fill="#5a6a5e" text-anchor="end">'
             'simulated data - software render</text>')
    P.append('</svg>')
    with open(os.path.join(OUT, "sticks3-acquire-to-lock.svg"), "w") as f:
        f.write("\n".join(P))

    print("frames:", len(frames), "svg keyframes:", len(sel), "duration %.1fs" % dur)
    print("locked:", sim.locked, "bpm:", sim.bpm, "quality:", sim.quality)
    for n in os.listdir(OUT):
        print("  ", n)

main()
