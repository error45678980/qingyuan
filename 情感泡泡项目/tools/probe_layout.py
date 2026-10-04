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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=390)
    args = ap.parse_args()

    shoot.prepare()
    shoot.seed()

    from src.server_app import app
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = 1

    html = shoot.to_static(client.get("/").get_data(as_text=True))
    html = html.replace("</body>", DIAG + "</body>")
    page = shoot.HTML_DIR / "probe.html"
    page.write_text(html, encoding="utf-8")

    out = shoot.SHOTS / f"_probe_{args.width}.png"
    if shoot.chrome_shot(page, out, args.width, 620, scale=1):
        print(f"诊断报告已生成（读图查看）：{out}")
    else:
        print("截图失败")


if __name__ == "__main__":
    main()
