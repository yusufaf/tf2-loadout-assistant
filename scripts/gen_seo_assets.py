#!/usr/bin/env python3
"""Generate static SEO/social assets for tf2-loadout-assistant (issue #33).

Brand tokens (from DESIGN.md + favicon.svg):
  cream  #e8dcc0  background
  ink    #2b2620  strokes/text
  gold   #cf9b45  top bar accent
  red    #b8383b  badge accent
  gray   #7c7c74  secondary text
"""
import os
from PIL import Image, ImageDraw, ImageFont

ROOT = "/mnt/HC_Volume_106549717/adventure-products/tf2-loadout-work/frontend/public"
CLASSES = os.path.join(ROOT, "classes")
FONTS = "/tmp/oswald"

CREAM = (232, 220, 192, 255)
INK = (43, 38, 32, 255)
GOLD = (207, 155, 69, 255)
RED = (184, 56, 59, 255)
GRAY = (124, 124, 116, 255)

def font(path, size):
    return ImageFont.truetype(os.path.join(FONTS, path), size)

OSWALD_600 = lambda s: font("Oswald.ttf", s)  # variable font defaults ~400; use size-based

def draw_mark(size, corner=False):
    """Redraw the favicon mark (cream square, ink border, gold top bar, red circle) at any size."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    inset = max(1, size // 32)
    border = max(1, size // 16)
    d.rectangle([0, 0, size - 1, size - 1], fill=CREAM, outline=INK, width=border)
    bar_h = max(2, size // 6)
    d.rectangle([border, border, size - 1 - border, border + bar_h], fill=GOLD)
    r = size // 4
    cx, cy = size // 2, size // 2 + bar_h // 2 + size // 10
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=RED, outline=INK, width=border)
    return im

def og_image():
    W, H = 1200, 630
    im = Image.new("RGBA", (W, H), CREAM)
    d = ImageDraw.Draw(im)

    # Gold top banner with URL, like the favicon's top bar
    d.rectangle([0, 0, W, 64], fill=GOLD)
    url_f = font("Oswald.ttf", 30)
    d.text((W - 40, 32), "tf2.yusufaf.dev", font=url_f, fill=INK, anchor="rm")
    d.text((40, 32), "MANN CO.", font=font("Oswald.ttf", 30), fill=INK, anchor="lm")

    # Ink frame like the favicon border
    d.rectangle([20, 88, W - 20, H - 20], outline=INK, width=8)

    # Heading
    h1 = font("Oswald.ttf", 96)
    h2 = font("Oswald.ttf", 138)
    d.text((W / 2, 250), "MANN CO.", font=h1, fill=INK, anchor="mm")
    d.text((W / 2, 348), "LOADOUT BENCH", font=h2, fill=INK, anchor="mm")

    # Red rule under heading (badge accent)
    d.rectangle([W / 2 - 100, 382, W / 2 + 100, 392], fill=RED)

    # Tagline
    tag = font("Oswald.ttf", 44)
    d.text((W / 2, 436), "TRY IT ON BEFORE YOU TRADE FOR IT", font=tag, fill=INK, anchor="mm")

    # Secondary line
    sub = font("Oswald.ttf", 30)
    d.text((W / 2, 486), "BROWSE  \u00b7  EQUIP  \u00b7  PRICE-CHECK", font=sub, fill=GRAY, anchor="mm")

    # Nine class icons in a centered row
    icons = ["scout", "soldier", "pyro", "demoman", "heavy", "engineer", "medic", "sniper", "spy"]
    iw = 64
    gap = (W - 2 * 40 - len(icons) * iw) / (len(icons) - 1)
    y = 510
    for i, name in enumerate(icons):
        icon = Image.open(os.path.join(CLASSES, name + ".png")).convert("RGBA").resize((iw, iw), Image.LANCZOS)
        x = 40 + i * (iw + gap)
        im.alpha_composite(icon, (int(x), y))

    im = im.convert("RGB")
    out = os.path.join(ROOT, "og-image.png")
    im.save(out, "PNG", optimize=True)
    print("og-image.png", im.size, os.path.getsize(out) // 1024, "KB")

def favicons():
    for size in (16, 32):
        mark = draw_mark(size)
        mark.save(os.path.join(ROOT, f"favicon-{size}x{size}.png"), "PNG", optimize=True)
    # ICO with both sizes
    small = draw_mark(16)
    large = draw_mark(32)
    large.save(os.path.join(ROOT, "favicon.ico"), sizes=[(16, 16), (32, 32)])
    print("favicon png/ico ok")

    apple = draw_mark(180)
    apple.save(os.path.join(ROOT, "apple-touch-icon.png"), "PNG", optimize=True)
    for size in (192, 512):
        mark = draw_mark(size)
        mark.save(os.path.join(ROOT, f"icon-{size}.png"), "PNG", optimize=True)
        print(f"icon-{size}.png ok")

if __name__ == "__main__":
    og_image()
    favicons()
    print("done")
