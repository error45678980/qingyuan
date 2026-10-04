# -*- coding: utf-8 -*-
"""截「材质试验台」(/preview) 的图——打磨泡泡材质时反复用。

不依赖正在运行的服务（waitress + Jinja 缓存不会自动重载新模板），
改为用 test_client 现场渲染当前代码，再把 CSS 指向本地文件交给 Chrome。

用法：python tools/shoot_lab.py
输出：tools/shots/_lab.png
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import shoot  # noqa: E402


def main():
    shoot.prepare()

    from src.server_app import app
    html = app.test_client().get("/preview").get_data(as_text=True)
    page = shoot.HTML_DIR / "lab.html"
    page.write_text(shoot.to_static(html), encoding="utf-8")

    out = shoot.SHOTS / "_lab.png"
    if out.exists():
        out.unlink()
    if shoot.chrome_shot(page, out, 1440, 1200, scale=1):
        print(f"已生成 {out}  ({out.stat().st_size // 1024} KB)")
    else:
        print("截图失败")


if __name__ == "__main__":
    main()
