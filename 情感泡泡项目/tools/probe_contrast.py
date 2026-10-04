# -*- coding: utf-8 -*-
"""对比度实测：量出泡泡里白字对背景的 WCAG 对比度。

做法（不依赖任何图像库）：
1. 用真实 CSS 与真实情绪色板生成一页 6 颗泡泡，位置固定、动画关闭；
2. 把 .soap-text 设为 visibility:hidden —— 只留"文字底下的背景"；
3. Chrome 截图 → 纯 Python 解码 PNG → 取每颗泡泡中央区域的像素；
4. 与白字（--color-white）算 WCAG 对比度。

判据：泡泡文字是加粗大字号，按 WCAG "大文本" 标准要求 ≥ 3.0:1。

用法：python tools/probe_contrast.py
"""
import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

BUBBLE = 200          # 泡泡边长
GAP = 40
MARGIN = 40
COLS = 3
SAMPLE = 55           # 中央采样半径（覆盖文字区域）

# ==================== 纯 Python PNG 解码 ====================
def decode_png(path: Path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG"
    pos, idat = 8, b""
    w = h = bitdepth = colortype = interlace = None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            w, h, bitdepth, colortype, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    assert bitdepth == 8 and interlace == 0, f"只支持 8bit 非隔行（实际 {bitdepth}/{interlace}）"
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colortype]
    raw = zlib.decompress(idat)
    stride = w * channels
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        if f == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, channels, out


# ==================== WCAG ====================
def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(r, g, b):
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(l1, l2):
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def region_stats(pixels, w, channels, x0, y0, x1, y1, text_lum):
    """返回 (平均底亮度, 最暗底, 最亮底, 区域内的最小对比度)。

    最坏情况必须逐像素取最小对比度：背景亮度越接近文字色，对比度越低。
    用"最亮背景"当最坏情况只在文字为白色时成立，深色字下是反的。
    """
    total, n = 0.0, 0
    lo, hi, worst = 1e9, -1e9, 99.0
    for y in range(y0, y1):
        base = y * w * channels
        for x in range(x0, x1):
            i = base + x * channels
            lum = luminance(pixels[i], pixels[i + 1], pixels[i + 2])
            total += lum
            n += 1
            lo = min(lo, lum)
            hi = max(hi, lum)
            worst = min(worst, contrast_ratio(text_lum, lum))
    return total / n, lo, hi, worst


# ==================== 生成探针页 ====================
def _load_palettes():
    """从 server_app 读情绪色板，兼容改造前后两套常量命名。

    返回 {mode: (dreamy, glow, core)}；改造前只有 light，core 与 glow 相同。
    """
    import src.server_app as app
    if hasattr(app, "_DREAMY_LIGHT"):
        out = {"light": (app._DREAMY_LIGHT, app._DREAMY_GLOW_LIGHT, app._DREAMY_GLOW_LIGHT)}
        if hasattr(app, "_DREAMY_DARK"):
            core = getattr(app, "_DREAMY_CORE_DARK", app._DREAMY_GLOW_DARK)
            out["dark"] = (app._DREAMY_DARK, app._DREAMY_GLOW_DARK, core)
        return out
    return {"light": (app._DREAMY, app._DREAMY_GLOW, app._DREAMY_GLOW)}


def build_page(mode: str = "light", hide_glyphs: bool = False) -> str:
    from src.server_app import ALL_EMOTIONS

    dreamy, glow_map, core_map = _load_palettes()[mode]
    # 深色模式：根元素挂 data-theme，让 light-dark() 取暗支
    theme_attr = ' data-theme="dark"' if mode == "dark" else ""
    glyph_rule = ('.soap-content,.soap-emoji{visibility:hidden}' if hide_glyphs else '')
    cells = []
    for idx, emo in enumerate(ALL_EMOTIONS):
        pal = dreamy[emo]
        left = MARGIN + (idx % COLS) * (BUBBLE + GAP)
        top = MARGIN + (idx // COLS) * (BUBBLE + GAP)
        cells.append(
            f'<div class="soap" style="position:absolute;left:{left}px;top:{top}px;'
            f'--size:{BUBBLE}px;--x:0px;--y:0px;'
            f'--c1:{pal[0]};--c2:{pal[1]};--c3:{pal[2]};--c4:{pal[3]};'
            f'--glow:{glow_map[emo]};--core:{core_map[emo]};">'
            '<span class="soap-film"></span><span class="soap-highlight"></span>'
            '<span class="soap-ring"></span><span class="soap-shadow"></span>'
            '<span class="soap-text"><span class="soap-emoji">🙂</span>'
            '<span class="soap-content">测试文字</span></span></div>'
        )
    width = MARGIN * 2 + COLS * BUBBLE + (COLS - 1) * GAP
    height = MARGIN * 2 + 2 * BUBBLE + GAP
    return (
        f'<!DOCTYPE html><html{theme_attr}><head><meta charset="utf-8">'
        f'<link rel="stylesheet" href="{(shoot.CSS_DIR / "style.css").as_uri()}">'
        '<style>'
        'body{margin:0}'
        '.stage{position:relative;width:' + str(width) + 'px;height:' + str(height) + 'px;}'
        '.soap{animation:none !important}'          # 关动画，保证几何位置确定
        '.soap-film{animation:none !important}'
        + glyph_rule +
        '</style></head><body><div class="stage">' + "".join(cells) + '</div></body></html>'
    )


def _hex_luminance(h: str):
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return luminance(r, g, b)


def text_color_luminance(mode: str = "light"):
    """从 CSS token 里读出泡泡文字色。兼容裸 hex 与 light-dark(亮, 暗) 两种写法。"""
    css = (shoot.CSS_DIR / "style.css").read_text(encoding="utf-8")
    m = re.search(r'--soap-text-color:\s*([^;]+);', css)
    if not m:
        raise SystemExit("CSS 里找不到 --soap-text-color")
    val = m.group(1).strip()
    ld = re.fullmatch(
        r'light-dark\(\s*#([0-9a-fA-F]{6})\s*,\s*#([0-9a-fA-F]{6})\s*\)', val)
    if ld:
        h = ld.group(1) if mode == "light" else ld.group(2)
        return _hex_luminance(h), "#" + h
    plain = re.fullmatch(r'#([0-9a-fA-F]{6})', val)
    if plain:
        h = plain.group(1)
        return _hex_luminance(h), "#" + h
    raise SystemExit(f"--soap-text-color 写法无法解析：{val}")


def self_test():
    """先用已知纯色页验证解码器；解码器不可信则后面的对比度数字全是假的。"""
    page = shoot.HTML_DIR / "selftest.html"
    page.write_text(
        '<!DOCTYPE html><html><body style="margin:0;background:#3366CC"></body></html>',
        encoding="utf-8")
    png = shoot.PNG_DIR / "selftest.png"
    if not shoot.chrome_shot(page, png, 500, 200, scale=1):
        raise SystemExit("自检截图失败")
    w, h, ch, px = decode_png(png)
    r, g, b = px[0], px[1], px[2]
    ok = abs(r - 0x33) <= 2 and abs(g - 0x66) <= 2 and abs(b - 0xCC) <= 2
    print(f"解码器自检：{w}x{h} 首像素 rgb({r},{g},{b}) 期望 rgb(51,102,204) -> "
          f"{'通过' if ok else '失败'}")
    if not ok:
        raise SystemExit("PNG 解码器不可信，终止")


def glyph_stats(px_text, px_bg, w, channels, x0, y0, x1, y1, text_lum):
    """只在"字形真实覆盖的像素"上算对比度。

    两帧差分定位墨迹：有字帧 vs 无字帧，差值超阈值的像素 = 字形（含光晕）。
    对墨迹像素取"无字帧"的同位置像素当背景——这才是文字底下的"底"。
    此前用固定方块采样，会把文字永远到不了的左上角高光区算进来，冤枉设计。
    """
    total, n = 0.0, 0
    worst = 99.0
    for y in range(y0, y1):
        base = y * w * channels
        for x in range(x0, x1):
            i = base + x * channels
            dt = abs(px_text[i] - px_bg[i]) + abs(px_text[i + 1] - px_bg[i + 1]) \
                + abs(px_text[i + 2] - px_bg[i + 2])
            if dt < 16:      # 非墨迹像素：字形没盖到这，不参与判定
                continue
            lum = luminance(px_bg[i], px_bg[i + 1], px_bg[i + 2])
            total += lum
            n += 1
            worst = min(worst, contrast_ratio(text_lum, lum))
    if n == 0:
        return 0.0, 99.0, 0
    return total / n, worst, n


def run_mode(mode: str):
    """对一种模式（light/dark）：两帧差分定位墨迹，逐情绪报对比度。"""
    page_text = shoot.HTML_DIR / f"contrast_{mode}_text.html"
    page_bg = shoot.HTML_DIR / f"contrast_{mode}_bg.html"
    page_text.write_text(build_page(mode, hide_glyphs=False), encoding="utf-8")
    page_bg.write_text(build_page(mode, hide_glyphs=True), encoding="utf-8")

    width = MARGIN * 2 + COLS * BUBBLE + (COLS - 1) * GAP
    height = MARGIN * 2 + 2 * BUBBLE + GAP
    png_text = shoot.PNG_DIR / f"contrast_{mode}_text.png"
    png_bg = shoot.PNG_DIR / f"contrast_{mode}_bg.png"
    if not shoot.chrome_shot(page_text, png_text, width, height, scale=1):
        print(f"[{mode}] 有字帧截图失败")
        return []
    if not shoot.chrome_shot(page_bg, png_bg, width, height, scale=1):
        print(f"[{mode}] 无字帧截图失败")
        return []

    w, h, ch, px_text = decode_png(png_text)
    w2, h2, ch2, px_bg = decode_png(png_bg)
    assert (w, h, ch) == (w2, h2, ch2), "两帧几何不一致，差分无意义"
    assert (w, h) == (width, height), f"尺寸不符：{w}x{h} != {width}x{height}"
    print(f"[{mode}] 截图 {w}x{h}")

    from src.server_app import ALL_EMOTIONS
    text_lum, text_hex = text_color_luminance(mode)
    print(f"[{mode}] 文字色 {text_hex}（亮度 {text_lum:.3f}）")
    print(f"{'情绪':<8}{'墨迹像素':>10}{'平均对比度':>12}{'最小对比度':>12}  判定")
    failures = []
    for idx, emo in enumerate(ALL_EMOTIONS):
        left = MARGIN + (idx % COLS) * (BUBBLE + GAP)
        top = MARGIN + (idx // COLS) * (BUBBLE + GAP)
        cx, cy = left + BUBBLE // 2, top + BUBBLE // 2
        mean_lum, c_min, n = glyph_stats(
            px_text, px_bg, w, ch, cx - SAMPLE, cy - SAMPLE, cx + SAMPLE, cy + SAMPLE, text_lum)
        c_mean = contrast_ratio(text_lum, mean_lum) if mean_lum else 0
        ok = "通过" if c_min >= 3.0 else "不足"
        if c_min < 3.0:
            failures.append((mode, emo, c_min))
        print(f"{emo:<8}{n:>10}{c_mean:>12.2f}{c_min:>12.2f}  {ok}")
    print(f"探针截图：{png_bg}\n")
    return failures


def main():
    shoot.prepare()
    self_test()
    modes = list(_load_palettes().keys())     # 改造前只有 light，改造后 light+dark
    failures = []
    for mode in modes:
        failures += run_mode(mode)

    print("判据：加粗大字按 WCAG 大文本标准需 ≥ 3.0:1（最小对比度 = 采样区内逐像素最小值）")
    if failures:
        print("未达标：" + "、".join(f"[{m}]{e}({c:.2f}:1)" for m, e, c in failures))
        sys.exit(1)
    else:
        print("全部达标。")


if __name__ == "__main__":
    main()
