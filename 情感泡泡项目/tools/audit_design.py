# -*- coding: utf-8 -*-
"""设计体检：量化 CSS 的 token 化程度与类名健康度。

三类指标：
1. token 化：消费处（:root 之外）还有多少**真正硬编码**的值（不含 var() 引用）。
2. 断链：模板用到的类名，CSS 里没有定义（会掉样式）。
3. 死代码：CSS 定义了但没有任何模板/脚本用到的类名。

用法：python tools/audit_design.py [--out tools/design_audit.md]
"""
import argparse
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "src" / "static" / "css" / "style.css"
TEMPLATES = ROOT / "src" / "templates"

_TOKEN_KEYS = ("font_sizes", "radii", "shadows", "spacing")


# ==================== token 指标 ====================
def _split_root(text):
    """返回 (消费处, :root块)。"""
    m = re.search(r':root\s*\{', text)
    if not m:
        return text, ""
    start = m.end()
    depth, i = 1, start
    while i < len(text) and depth > 0:
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        i += 1
    return text[:m.start()] + text[i:], text[m.start():i]


def _scan(text):
    return dict(
        colors=Counter(re.findall(r'#[0-9a-fA-F]{3,8}\b', text)),
        rgbas=Counter(re.findall(r'rgba?\([^)]*\)', text)),
        font_sizes=Counter(re.findall(r'font-size:\s*([^;]+)', text)),
        radii=Counter(re.findall(r'border-radius:\s*([^;]+)', text)),
        shadows=Counter(re.findall(r'box-shadow:\s*([^;]+)', text)),
        spacing=Counter(re.findall(r'(?:padding|margin|gap)(?:-[a-z]+)?:\s*([^;]+)', text)),
    )


def _non_token(counter):
    """只保留不含 var() 的值——这才是真正硬编码的。"""
    return Counter({k: v for k, v in counter.items() if 'var(' not in k})


def audit_tokens(text):
    consumer_text, root_block = _split_root(text)
    consumer = _scan(consumer_text)
    token_names = re.findall(r'(--[\w-]+)\s*:', root_block)
    referenced = set(re.findall(r'var\((--[\w-]+)', text))
    unused = sorted({n for n in token_names if n not in referenced})
    return dict(
        consumer=consumer,
        hardcoded={k: _non_token(consumer[k]) for k in _TOKEN_KEYS},
        token_count=len(token_names),
        var_refs=len(re.findall(r'var\(--[\w-]+', text)),
        unused_tokens=unused,
    )


# ==================== 类名指标 ====================
def _classes_in_css(text):
    text = re.sub(r':root\s*\{[^}]*\}', '', text)
    text = re.sub(r'@keyframes\s+[\w-]+\s*\{(?:[^{}]|\{[^{}]*\})*\}', '', text, flags=re.S)
    found = set()
    for selector in re.findall(r'([^{}]+)\{', text):
        found.update(re.findall(r'\.([A-Za-z][\w-]*)', selector))
    return found


def _classes_used_by_templates():
    used = set()
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for attr in re.findall(r'class="([^"]*)"', text):
            # jinja 条件里的字面类名；反向断言跳过 m['key'] 这类字典下标
            used.update(re.findall(r"(?<![\[.])'([A-Za-z][\w-]*)'", attr))
            used.update(re.findall(r'[A-Za-z][\w-]*', re.sub(r'\{\{.*?\}\}', ' ', attr)))
        used.update(re.findall(r"classList\.(?:add|remove|toggle)\(\s*'([^']+)'", text))
    return used


def _classes_defined_inline():
    defined = set()
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for block in re.findall(r'<style[^>]*>(.*?)</style>', text, re.S):
            defined |= _classes_in_css(block)
    return defined


def audit_classes():
    defined = _classes_in_css(CSS.read_text(encoding="utf-8")) | _classes_defined_inline()
    used = _classes_used_by_templates()
    return defined, used, (used - defined), (defined - used)


# ==================== 报告 ====================
def render(tok, defined, used, missing, dead) -> str:
    c, h = tok["consumer"], tok["hardcoded"]
    hardcoded_colors = len(c["colors"]) + len(c["rgbas"])

    lines = ["# 设计体检报告 · 情感泡泡\n"]
    lines.append("\n## 一、token 化程度\n")
    lines.append(f"- `:root` 语义变量：**{tok['token_count']} 个**")
    lines.append(f"- `var()` 引用：**{tok['var_refs']} 次**")
    lines.append(f"- 消费处硬编码颜色：**{hardcoded_colors} 种**")
    lines.append(f"- 消费处硬编码字号：**{len(h['font_sizes'])} 种**")
    lines.append(f"- 消费处硬编码圆角：**{len(h['radii'])} 种**")
    lines.append(f"- 消费处硬编码阴影：**{len(h['shadows'])} 种**")
    lines.append(f"- 消费处硬编码间距：**{len(h['spacing'])} 种**\n")

    lines.append(f"- 定义了但没人用的 token（死 token）：**{len(tok['unused_tokens'])} 个**")
    if tok["unused_tokens"]:
        for n in tok["unused_tokens"]:
            lines.append(f"  - `{n}`")

    lines.append("\n## 二、类名健康度\n")
    lines.append(f"- CSS 定义类名：**{len(defined)} 个** / 模板使用：**{len(used)} 个**")
    lines.append(f"- 断链（用到但没定义）：**{len(missing)} 个**")
    lines.append(f"- 死代码（定义了但没用）：**{len(dead)} 个**\n")

    if missing:
        lines.append("\n### ⚠️ 断链类名（会掉样式）\n")
        for n in sorted(missing):
            lines.append(f"- `.{n}`")
    if dead:
        lines.append("\n### 死代码类名（可删）\n")
        lines.append("```\n" + "  ".join("." + n for n in sorted(dead)) + "\n```")

    lines.append("\n## 三、仍硬编码的值\n")
    for label, key in (("颜色 #hex", "colors"), ("颜色 rgba", "rgbas")):
        if c[key]:
            lines.append(f"\n### {label}（{len(c[key])} 种）\n")
            for val, n in c[key].most_common():
                lines.append(f"- `{val}` x{n}")
    for label, key in (("字号", "font_sizes"), ("圆角", "radii"),
                       ("阴影", "shadows"), ("间距", "spacing")):
        if h[key]:
            token_only = len(c[key]) - len(h[key])
            lines.append(f"\n### {label}（硬编码 {len(h[key])} 种，另有 {token_only} 种纯 token 引用）\n")
            for val, n in h[key].most_common():
                lines.append(f"- `{val}` x{n}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "tools" / "design_audit.md"))
    args = parser.parse_args()

    tok = audit_tokens(CSS.read_text(encoding="utf-8"))
    defined, used, missing, dead = audit_classes()
    Path(args.out).write_text(render(tok, defined, used, missing, dead), encoding="utf-8")

    c, h = tok["consumer"], tok["hardcoded"]
    print(f"报告：{args.out}")
    print(f"token={tok['token_count']} var引用={tok['var_refs']} "
          f"硬编码颜色={len(c['colors'])+len(c['rgbas'])} "
          f"字号={len(h['font_sizes'])} 圆角={len(h['radii'])} "
          f"阴影={len(h['shadows'])} 间距={len(h['spacing'])}")
    print(f"断链={len(missing)} 死代码={len(dead)}")
    if missing:
        print("断链类名：" + ", ".join(sorted(missing)))


if __name__ == "__main__":
    main()
