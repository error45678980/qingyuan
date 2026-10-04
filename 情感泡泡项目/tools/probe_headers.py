# -*- coding: utf-8 -*-
"""走真实 HTTP 验证安全响应头与 CSP 是否真的不挡自家资源。

为什么需要它：截图默认走 file://，不经过响应头，测不到 CSP。
CSP 一旦写错（比如挡掉内联脚本），页面会在浏览器里悄悄坏掉。

用法：python tools/probe_headers.py
输出：tools/shots/_http_login.png（人工看一眼：只应有登录表单，不该同时出现注册表单）
"""
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

PORT = 5066


def main():
    shoot.prepare()
    shoot.seed()

    from werkzeug.serving import make_server
    from src.server_app import app

    httpd = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}"
    print(f"服务已启动：{base}")

    try:
        # 1. 真实响应头
        with urllib.request.urlopen(f"{base}/login") as resp:
            print(f"\nHTTP {resp.status} {resp.headers.get('Content-Type')}")
            for key in ("X-Content-Type-Options", "X-Frame-Options",
                        "Referrer-Policy", "Content-Security-Policy"):
                print(f"  {key}: {resp.headers.get(key)}")
            print(f"  Set-Cookie: {resp.headers.get('Set-Cookie')}")

        # 2. CSP 是否挡住自家 CSS
        req = urllib.request.Request(f"{base}/static/css/style.css")
        with urllib.request.urlopen(req) as resp:
            size = len(resp.read())
        print(f"\n  CSS 可取：{size} 字节（同源静态资源正常）")

        # 3. 用浏览器实际加载：若内联脚本被 CSP 挡掉，
        #    login.html 里的 showTab() 不会执行，登录与注册两个表单会同时可见。
        out = shoot.SHOTS / "_http_login.png"
        ok = shoot.chrome_shot(f"{base}/login", out, 1000, 800, scale=1)
        print(f"\n  浏览器截图：{'成功' if ok else '失败'} -> {out}")
        print("  请确认图中只有【登录】表单；若登录与注册表单同时出现，说明内联脚本被 CSP 挡了。")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
