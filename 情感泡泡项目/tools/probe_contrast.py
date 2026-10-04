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
def build_page() -> str:
    from src.server_app import ALL_EMOTIONS, _DREAMY, _DREAMY_GLOW

    cells = []
    for idx, emo in enumerate(ALL_EMOTIONS):
        pal = _DREAMY[emo]
        left = MARGIN + (idx % COLS) * (BUBBLE + GAP)
        top = MARGIN + (idx // COLS) * (BUBBLE + GAP)
        cells.append(
            f'<div class="soap" style="position:absolute;left:{left}px;top:{top}px;'
            f'--size:{BUBBLE}px;--x:0px;--y:0px;'
            f'--c1:{pal[0]};--c2:{pal[1]};--c3:{pal[2]};--c4:{pal[3]};'
            f'--glow:{_DREAMY_GLOW[emo]};">'
            '<span class="soap-film"></span><span class="soap-highlight"></span>'
            '<span class="soap-ring"></span><span class="soap-shadow"></span>'
            '<span class="soap-text"><span class="soap-emoji">🙂</span>'
            '<span class="soap-content">测试文字</span></span></div>'
        )
    width = MARGIN * 2 + COLS * BUBBLE + (COLS - 1) * GAP
    height = MARGIN * 2 + 2 * BUBBLE + GAP
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<link rel="stylesheet" href="{(shoot.CSS_DIR / "style.css").as_uri()}">'
        '<style>'
        'body{margin:0}'
        '.stage{position:relative;width:' + str(width) + 'px;height:' + str(height) + 'px;}'
        '.soap{animation:none !important}'          # 关动画，保证几何位置确定
        '.soap-film{animation:none !important}'
        '.soap-text{visibility:hidden}'             # 只留文字底下的背景
        '</style></head><body><div class="stage">' + "".join(cells) + '</div></body></html>'
    )


def text_color_luminance():
    """从 CSS token 里读出泡泡文字色，跟着设计系统走。"""
    css = (shoot.CSS_DIR / "style.css").read_text(encoding="utf-8")
    m = re.search(r'--soap-text-color:\s*#([0-9a-fA-F]{6})', css)
    if not m:
        raise SystemExit("CSS 里找不到 --soap-text-color")
    h = m.group(1)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return luminance(r, g, b), "#" + h


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


def main():
    shoot.prepare()
    self_test()
    page = shoot.HTML_DIR / "contrast.html"
    page.write_text(build_page(), encoding="utf-8")

    width = MARGIN * 2 + COLS * BUBBLE + (COLS - 1) * GAP
    height = MARGIN * 2 + 2 * BUBBLE + GAP
    png = shoot.PNG_DIR / "contrast.png"
    if not shoot.chrome_shot(page, png, width, height, scale=1):
        print("截图失败")
        return

    w, h, channels, pixels = decode_png(png)
    print(f"截图 {w}x{h}，{channels} 通道")

    # 自检：几何必须对得上，否则坐标就是错的
    assert (w, h) == (width, height), f"尺寸不符：{w}x{h} != {width}x{height}"

    from src.server_app import ALL_EMOTIONS
    text_lum, text_hex = text_color_luminance()
    print(f"文字色 {text_hex}（亮度 {text_lum:.3f}）")
    print(f"\n{'情绪':<8}{'底亮度范围':>18}{'平均对比度':>12}{'最小对比度':>12}  判定")
    failures = []
    for idx, emo in enumerate(ALL_EMOTIONS):
        left = MARGIN + (idx % COLS) * (BUBBLE + GAP)
        top = MARGIN + (idx // COLS) * (BUBBLE + GAP)
        cx, cy = left + BUBBLE // 2, top + BUBBLE // 2
        mean_lum, lo, hi, c_min = region_stats(
            pixels, w, channels, cx - SAMPLE, cy - SAMPLE, cx + SAMPLE, cy + SAMPLE, text_lum)
        c_mean = contrast_ratio(text_lum, mean_lum)
        ok = "通过" if c_min >= 3.0 else "不足"
        if c_min < 3.0:
            failures.append((emo, c_min))
        print(f"{emo:<8}{f'{lo:.2f}~{hi:.2f}':>18}{c_mean:>12.2f}{c_min:>12.2f}  {ok}")

    print("\n判据：加粗大字按 WCAG 大文本标准需 ≥ 3.0:1（最小对比度 = 采样区内逐像素最小值）")
    if failures:
        print("未达标：" + "、".join(f"{e}({c:.2f}:1)" for e, c in failures))
    else:
        print("全部达标。")
    print(f"\n探针截图：{png}")


if __name__ == "__main__":
    main()
