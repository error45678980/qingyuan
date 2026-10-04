# -*- coding: utf-8 -*-
"""布局探针：找出把页面撑宽（横向溢出）的元凶。

原理：渲染真实页面 → 注入脚本测量 documentElement.scrollWidth 与每个元素的
getBoundingClientRect() → 用 chrome --dump-dom 把结果读回来。

用法：python tools/probe_layout.py [--width 390]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

DIAG = """
<script>
window.addEventListener('load', function () {
  var vw = document.documentElement.clientWidth;
  var out = [];
  out.push('VIEWPORT = ' + vw);
  out.push('INNER_W  = ' + window.innerWidth);
  out.push('OUTER_W  = ' + window.outerWidth);
  out.push('SCROLLW  = ' + document.documentElement.scrollWidth);
  out.push('MEDIA_560_matches = ' + window.matchMedia('(max-width: 560px)').matches);
  out.push('');
  var offenders = [];
  document.querySelectorAll('body *').forEach(function (el) {
    var r = el.getBoundingClientRect();
    if (r.right > vw + 1) {
      var cls = (el.className || '').toString().trim().replace(/\\s+/g, '.');
      offenders.push(el.tagName.toLowerCase() + (cls ? '.' + cls : '')
        + '  w=' + Math.round(r.width) + ' right=' + Math.round(r.right)
        + ' pos=' + getComputedStyle(el).position);
    }
  });
  out.push('OVERFLOWING ELEMENTS: ' + offenders.length);
  out.push('');
  offenders.slice(0, 14).forEach(function (s) { out.push('  ' + s); });

  // Chrome 的 stdout 拿不到，只能把报告画出来再截图读
  var pre = document.createElement('pre');
  pre.style.cssText = 'position:fixed;inset:0;z-index:99999;margin:0;padding:14px;'
    + 'background:#fff;color:#000;font:13px/1.45 Consolas,monospace;white-space:pre-wrap;'
    + 'overflow:auto;';
  pre.textContent = out.join('\\n');
  document.body.appendChild(pre);
});
</script>
"""


# -*- coding: utf-8 -*-
"""布局探针：找出把页面撑宽（横向溢出）的元凶。

原理：渲染真实页面 → 注入脚本测量 documentElement.scrollWidth 与每个元素的
getBoundingClientRect() → 用 chrome --dump-dom 把结果读回来。

用法：python tools/probe_layout.py [--width 390] [--widths 390,768,1024,1440]
"""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

DIAG = """
<script>
window.addEventListener('load', function () {
  var vw = document.documentElement.clientWidth;
  var scrollW = document.documentElement.scrollWidth;
  var out = [];
  out.push('VIEWPORT = ' + vw);
  out.push('SCROLLW  = ' + scrollW);
  // 真溢出 = 可滚动区域变宽。getBoundingClientRect 会把 transform 移出屏的
  // 元素（如关着的抽屉侧栏）也算进来，但它们不撑宽 scrollWidth——以 scrollW 为准。
  out.push('REAL_OVERFLOW = ' + (scrollW > vw + 1));
  var offenders = [];
  document.querySelectorAll('body *').forEach(function (el) {
    if (el.closest('.sidebar')) return;   // 关着的抽屉侧栏：设计使然，不参与判定
    var r = el.getBoundingClientRect();
    if (r.right > vw + 1) {
      var cls = (el.className || '').toString().trim().replace(/\\s+/g, '.');
      offenders.push(el.tagName.toLowerCase() + (cls ? '.' + cls : '')
        + '  w=' + Math.round(r.width) + ' right=' + Math.round(r.right)
        + ' pos=' + getComputedStyle(el).position);
    }
  });
  out.push('OVERFLOWING ELEMENTS: ' + offenders.length);
  offenders.slice(0, 14).forEach(function (s) { out.push('  ' + s); });
  var pre = document.createElement('pre');
  pre.id = '__probe_report__';
  pre.textContent = out.join('\\n');
  document.body.appendChild(pre);
});
</script>
"""


def probe(width: int) -> str:
    html = shoot.to_static(_wall_html())
    html = html.replace("</body>", DIAG + "</body>")
    page = shoot.HTML_DIR / f"probe_{width}.html"
    page.write_text(html, encoding="utf-8")
    # --dump-dom 同步等待 --virtual-time-budget 指定的虚拟时间，load 事件必触发
    cmd = [
        shoot.CHROME, "--headless", "--disable-gpu", "--no-first-run",
        "--allow-file-access-from-files", "--virtual-time-budget=4000",
        f"--window-size={width},800", "--dump-dom", page.as_uri(),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    m = re.search(r'<pre id="__probe_report__">(.*?)</pre>', r.stdout, re.S)
    return m.group(1).strip() if m else f"[{width}px] 报告提取失败（DOM 里没找到探针输出）"


_wall_cache = None


def _wall_html() -> str:
    global _wall_cache
    if _wall_cache is None:
        from src.server_app import app
        client = app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = 1
        _wall_cache = client.get("/").get_data(as_text=True)
    return _wall_cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--widths", default="390,768,1024,1440")
    args = ap.parse_args()
    widths = [args.width] if args.width else [int(w) for w in args.widths.split(",")]

    shoot.prepare()
    shoot.seed()

    bad = 0
    for w in widths:
        report = probe(w)
        print(f"===== {w}px =====")
        print(report)
        m = re.search(r'OVERFLOWING ELEMENTS: (\d+)', report)
        real = re.search(r'REAL_OVERFLOW = (\w+)', report)
        n = int(m.group(1)) if m else 99
        if (real and real.group(1) == 'true') or n:
            bad += max(n, 1)
        print()
    if bad:
        print(f"存在横向溢出（{bad} 处）")
        sys.exit(1)
    print("全部视口无横向溢出。")


if __name__ == "__main__":
    main()
