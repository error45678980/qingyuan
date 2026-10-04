# -*- coding: utf-8 -*-
"""设计体检：统计 style.css 里颜色/字号/间距/圆角/阴影的散乱程度。

用法：python tools/audit_design.py [--out tools/design_audit.md]
它把结果写到 Markdown 报表，方便每次改动后对比"散乱度是否下降"。
"""
import argparse
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "src" / "static" / "css" / "style.css"


def audit(text: str):
    colors = Counter(re.findall(r'#[0-9a-fA-F]{3,8}\b', text))
    rgbas = Counter(re.findall(r'rgba?\([^)]*\)', text))
    font_sizes = Counter(re.findall(r'font-size:\s*([^;]+)', text))
    radii = Counter(re.findall(r'border-radius:\s*([^;]+)', text))
    shadows = Counter(re.findall(r'box-shadow:\s*([^;]+)', text))
    spacing = Counter(re.findall(r'(?:padding|margin|gap)(?:-[a-z]+)?:\s*([^;]+)', text))
    root_vars = re.findall(r':root\s*\{([^}]*)\}', text, re.S)
    var_count = len(re.findall(r'--[\w-]+\s*:', root_vars[0])) if root_vars else 0
    return dict(
        colors=colors, rgbas=rgbas, font_sizes=font_sizes,
        radii=radii, shadows=shadows, spacing=spacing, root_var_count=var_count,
    )


def render(result) -> str:
    lines = []
    lines.append("# 设计体检报告 · 情感泡泡\n")
    lines.append(f"源文件：`{CSS.relative_to(ROOT)}`\n")
    lines.append("（值后面 `xN` 表示出现次数；种类越少、越集中在语义变量上，越健康）\n")

    def section(title, counter):
        lines.append(f"\n## {title}（{len(counter)} 种）\n")
        for val, n in counter.most_common():
            lines.append(f"- `{val}` x{n}")

    section("颜色 #hex", result["colors"])
    section("颜色 rgba()", result["rgbas"])
    section("字号 font-size", result["font_sizes"])
    section("圆角 border-radius", result["radii"])
    section("阴影 box-shadow", result["shadows"])
    section("间距 padding/margin/gap", result["spacing"])

    lines.append(f"\n## 已在 `:root` 定义的 CSS 变量\n\n**{result['root_var_count']} 个**（其余全是硬编码）\n")
    lines.append(f"\n## 汇总\n")
    lines.append(
        f"- 硬编码颜色：{len(result['colors']) + len(result['rgbas'])} 种\n"
        f"- 字号：{len(result['font_sizes'])} 种\n"
        f"- 圆角：{len(result['radii'])} 种\n"
        f"- 阴影：{len(result['shadows'])} 种\n"
        f"- 间距值：{len(result['spacing'])} 种\n"
        f"- 语义变量：仅 {result['root_var_count']} 个\n"
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "tools" / "design_audit.md"))
    args = parser.parse_args()

    text = CSS.read_text(encoding="utf-8")
    result = audit(text)
    report = render(result)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"报告已生成：{args.out}")
    print(f"硬编码颜色 {len(result['colors']) + len(result['rgbas'])} 种 / "
          f"字号 {len(result['font_sizes'])} 种 / 间距 {len(result['spacing'])} 种 / "
          f"语义变量 {result['root_var_count']} 个")


if __name__ == "__main__":
    main()
