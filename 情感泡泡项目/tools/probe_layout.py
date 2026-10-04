# -*- coding: utf-8 -*-
"""布局探针：找出把页面撑宽（横向溢出）的元凶，在 4 个视口各测一次。

两个必须绕开的坑（都在本机实测过）：

1. **不能靠 `chrome --dump-dom` 读结果。** Windows 上 Chrome 是 GUI 子系统程序，
   stdout 会被丢弃——实测 returncode=0 但 stdout 长度为 0。
   改为让页面把报告"回传"到一个本地 HTTP 端口：网络副作用不依赖 stdout。

2. **不能用 `--window-size` 直接测窄视口。** Chrome 在 Windows 上窗口有最小宽度
   （约 500px），`--window-size=390` 时布局视口其实是 500，测出来的是假数据。
   窄视口必须套一层 iframe，iframe 内部才有真正独立的视口。

用法：python tools/probe_layout.py [--width W] [--widths 390,768,1024,1440]
"""
import argparse
import http.server
import re
import sys
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

NARROW = 520          # 低于此宽度必须套 iframe（Chrome 最小窗口宽度约 500）

DIAG = """
<script>
window.addEventListener('load', function () {
  var vw = document.documentElement.clientWidth;
  var scrollW = document.documentElement.scrollWidth;
  var out = [];
  out.push('VIEWPORT = ' + vw);
  out.push('SCROLLW  = ' + scrollW);
  // 真溢出 = 可滚动区域变宽。关着的抽屉侧栏被 transform 移出屏幕，
  // getBoundingClientRect 会把它算进来，但它不撑宽 scrollWidth——以 scrollW 为准。
  out.push('REAL_OVERFLOW = ' + (scrollW > vw + 1));
  var offenders = [];
  document.querySelectorAll('body *').forEach(function (el) {
    if (el.closest('.sidebar')) return;
    var r = el.getBoundingClientRect();
    if (r.right > vw + 1) {
      var cls = (el.className || '').toString().trim().replace(/\\s+/g, '.');
      offenders.push(el.tagName.toLowerCase() + (cls ? '.' + cls : '')
        + '  w=' + Math.round(r.width) + ' right=' + Math.round(r.right));
    }
  });
  out.push('OVERFLOWING ELEMENTS: ' + offenders.length);
  offenders.slice(0, 14).forEach(function (s) { out.push('  ' + s); });
  new Image().src = 'http://127.0.0.1:__PORT__/beacon?r='
    + encodeURIComponent(out.join('\\n'));
});
</script>
"""


def start_beacon():
    """起一个只收报告的小 HTTP 服务，返回 (port, box, done)。"""
    box, done = {}, threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            raw = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("r", [""])[0]
            if raw:
                box["report"] = raw
                done.set()
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return port, box, done


_wall_cache = None


def _wall_html() -> str:
    global _wall_cache
    if _wall_cache is None:
        from src.server_app import app
        client = app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = 1
        _wall_cache = shoot.to_static(client.get("/").get_data(as_text=True))
    return _wall_cache


def probe(width: int, port: int, box: dict, done: threading.Event) -> str:
    html = _wall_cache.replace("</body>", DIAG.replace("__PORT__", str(port)) + "</body>")

    if width < NARROW:
        # 真窄视口：外层窗口受最小宽度限制，所以把页面塞进一个定宽 iframe
        inner = shoot.HTML_DIR / f"probe_{width}__inner.html"
        inner.write_text(html, encoding="utf-8")
        page = shoot.HTML_DIR / f"probe_{width}.html"
        page.write_text(
            '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
            'html,body{margin:0;padding:0}'
            f'iframe{{width:{width}px;height:900px;border:0;display:block}}'
            f'</style></head><body><iframe src="{inner.name}"></iframe></body></html>',
            encoding="utf-8")
        shot_w = NARROW
    else:
        page = shoot.HTML_DIR / f"probe_{width}.html"
        page.write_text(html, encoding="utf-8")
        shot_w = width

    done.clear()
    box.pop("report", None)
    shoot.chrome_shot(page, shoot.WORK / f"probe_{width}.png", shot_w, 900, scale=1)
    if done.wait(8):
        return box["report"]
    return f"[{width}px] 报告回传失败（beacon 未收到）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--widths", default="390,768,1024,1440")
    args = ap.parse_args()
    widths = [args.width] if args.width else [int(w) for w in args.widths.split(",")]

    shoot.prepare()
    port, box, done = start_beacon()
    _wall_html()

    bad = 0
    for w in widths:
        report = probe(w, port, box, done)
        print(f"===== {w}px =====")
        print(report)
        m = re.search(r'OVERFLOWING ELEMENTS: (\d+)', report)
        real = re.search(r'REAL_OVERFLOW = (\w+)', report)
        n = int(m.group(1)) if m else 99
        if (real and real.group(1) == "true") or n:
            bad += max(n, 1)
        print()

    if bad:
        print(f"存在横向溢出（{bad} 处）")
        sys.exit(1)
    print("全部视口无横向溢出。")


if __name__ == "__main__":
    main()
