#!/usr/bin/env python3
"""The boot screen's animation, as xnu draws it (patch 0309, sweet_splash2_frame): same integer arithmetic,
so the preview is what the phone shows.

  splash_anim.py splash2.bin out.gif

Per frame at T milliseconds since the boot screen came up:
  - the gem pulses: in the gem box, base + glow * P / 256 per channel (saturating), P = 150 + SIN(T / 2400)
  - a comet runs round the ring every 2.8 s: TAIL points behind the head, brightness falling linearly
  - every 3 s a light sweeps the wordmark diagonally (1.4 s), only where its mask is set
  - the status panel (patch 0328, sweet_splash2_status): how long the kernel has been up, the frame count,
    and a bar sweeping its rail every 2.2 s -- so a slow boot visibly moves
"""
import math, os, re, struct, sys
import numpy as np
from PIL import Image

SIN = [int(round(127 * math.sin(2 * math.pi * i / 256))) for i in range(256)]
TAIL = 110
PULSE_MS, COMET_MS, SWEEP_MS, SWEEP_RUN, GLINT_MS = 2400, 2800, 3000, 1400, 2000

# the status panel: the same numbers as make_splash.py and xnu's patch 0328
PANEL_Y = 1810
PANEL_TX, BAR_Y, BAR_W, SEG, LAP_MS = 144, 1998, 792, 180, 2200


def _font():
    """shim/font.h, the console's 8x16 glyphs (bit 7 is the left column). The phone draws these with xnu's
    iso_font, which is the same VGA face for ASCII."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "font.h")).read()
    rows = re.findall(r"\{([^{}]*)\}", src)
    return np.array([[int(v, 16) for v in r.split(",")] for r in rows], np.uint8)


FONT = _font()


def load(path):
    b = open(path, "rb").read()
    magic, w, h, ver = struct.unpack_from("<4s3I", b, 0)
    assert magic == b"SPL2"
    gx, gy, gw, gh = struct.unpack_from("<4I", b, 16)
    wx, wy, ww, wh = struct.unpack_from("<4I", b, 32)
    cx, cy, r, n = struct.unpack_from("<4I", b, 48)
    o = 80
    base = np.frombuffer(b, "<u4", w * h, o).reshape(h, w); o += w * h * 4
    glow = np.frombuffer(b, "<u4", gw * gh, o).reshape(gh, gw); o += gw * gh * 4
    mask = np.frombuffer(b, np.uint8, ww * wh, o).reshape(wh, ww); o += ww * wh
    ring = np.frombuffer(b, "<i2", 2 * n, o).reshape(n, 2); o += 4 * n
    spr_n, glint_n = struct.unpack_from("<2I", b, 64)
    spr = np.frombuffer(b, "<u4", spr_n * spr_n, o).reshape(spr_n, spr_n); o += spr_n * spr_n * 4
    glints = np.frombuffer(b, "<i2", 2 * glint_n, o).reshape(glint_n, 2)
    return dict(w=w, h=h, gem=(gx, gy, gw, gh), word=(wx, wy, ww, wh), ring=(cx, cy, r, n),
                base=base, glow=glow, mask=mask, pts=ring, spr=spr, glints=glints)


def rgb(a):
    return np.stack([(a >> 16) & 255, (a >> 8) & 255, a & 255], -1).astype(np.int32)


def text(f, x, y, s, scale, color):
    """one line at x, y (top-left) with squash 8x16 glyphs, what sweet_splash2_text() does"""
    color = np.array(color, np.int32)
    for ch in s:
        if 0x20 < ord(ch) < 0x7f:
            g = FONT[ord(ch) - 0x20]
            for gy in range(16):
                for gx in range(8):
                    if (g[gy] >> (7 - gx)) & 1:
                        f[y + gy * scale:y + (gy + 1) * scale, x + gx * scale:x + (gx + 1) * scale] = color
        x += 8 * scale
    return x


def status(f, t, frames):
    """the boot's live numbers on the plate (sweet_splash2_status)"""
    x = text(f, PANEL_TX, PANEL_Y + 26, "BOOT", 3, (0x5f, 0x86, 0xb8))
    text(f, x + 24, PANEL_Y + 26, "%d.%ds" % (t // 1000, (t // 100) % 10), 3, (255, 255, 255))
    line = "FRAME %d" % frames
    text(f, PANEL_TX + BAR_W - len(line) * 8, PANEL_Y + 50, line, 1, (0x7f, 0xa2, 0xcc))
    text(f, PANEL_TX, PANEL_Y + 92, "IF THIS COUNTS UP THE KERNEL IS NOT HUNG", 1, (0x89, 0xa9, 0xcf))
    text(f, PANEL_TX, PANEL_Y + 118, "iOS takes this screen when it draws its first frame", 1, (0x5d, 0x77, 0x96))
    i = np.arange(SEG)
    span = BAR_W - SEG
    bx = PANEL_TX + (t % LAP_MS) * span // LAP_MS
    for r in range(8):
        a = np.minimum(i, SEG - 1 - i) * 255 // (SEG // 2)
        a = a * (255 - (3 - r if r < 4 else r - 4) * 56) >> 8
        sl = f[BAR_Y + r, bx:bx + SEG]
        sl[:] = np.minimum(255, sl + np.stack([30 * a >> 8, 140 * a >> 8, 255 * a >> 8], -1))


def frame(s, t, frames=0):
    f = rgb(s["base"]).copy()
    gx, gy, gw, gh = s["gem"]
    p = 150 + SIN[(t * 256 // PULSE_MS) & 255] * 105 // 127
    box = f[gy:gy + gh, gx:gx + gw]
    box[:] = np.minimum(255, box + ((rgb(s["glow"]) * p) >> 8))
    # comet
    cx, cy, r, n = s["ring"]
    head = (t * n // COMET_MS) % n
    for i in range(TAIL - 1, -1, -1):
        dx, dy = s["pts"][(head - i) % n]
        b = 255 - i * 255 // TAIL
        rad = 3 if i < 6 else 2
        x, y = cx + dx, cy + dy
        add = np.array([b * 90 >> 8, b * 200 >> 8, b]) if i >= 4 else np.array([b, b, b])
        sl = f[y - rad:y + rad + 1, x - rad:x + rad + 1]
        sl[:] = np.minimum(255, sl + add)
    # sparkles: one at a time, each for GLINT_MS / count
    k = len(s["glints"])
    ph = t % GLINT_MS
    j = ph * k // GLINT_MS
    slot = GLINT_MS // k
    q = ph - j * slot
    if q < slot:
        a = SIN[(q * 128 // slot) & 255] * 2      # 0..254..0, half a sine over its slot
        sp = rgb(s["spr"])
        hs = sp.shape[0] // 2
        x, y = s["glints"][j]
        sl = f[y - hs:y + hs + 1, x - hs:x + hs + 1]
        sl[:] = np.minimum(255, sl + ((sp * a) >> 8))
    status(f, t, frames)
    # wordmark sweep
    wx, wy, ww, wh = s["word"]
    ts = t % SWEEP_MS
    if ts < SWEEP_RUN:
        span = ww + wh // 2 + 240
        pos = -120 + ts * span // SWEEP_RUN
        yy, xx = np.mgrid[0:wh, 0:ww]
        k = xx + (wh - yy) // 2
        dd = np.abs(k - pos)
        a = np.where(dd < 70, (70 - dd) * s["mask"].astype(np.int32) // 70, 0)
        wb = f[wy:wy + wh, wx:wx + ww]
        wb[:] = np.minimum(255, wb + a[..., None])
    return f.astype(np.uint8)


def preview(binpath, gifpath, seconds=6, fps=20, scale=0.5):
    s = load(binpath)
    frames = []
    for i in range(seconds * fps):
        im = Image.fromarray(frame(s, i * 1000 // fps, i))
        frames.append(im.resize((int(s["w"] * scale), int(s["h"] * scale)), Image.LANCZOS))
    frames[0].save(gifpath, save_all=True, append_images=frames[1:], duration=1000 // fps, loop=0, optimize=False)
    print(gifpath, len(frames), "frames")


if __name__ == "__main__":
    preview(sys.argv[1], sys.argv[2])
