from pathlib import Path

from fastapi.responses import HTMLResponse

from app.main import app
import app.box_router as box_router_module
from app.box_service_v2 import controller as box_controller

ROOT = Path(__file__).resolve().parent.parent

# 盒子路由沿用原 API，但切换到增强版控制器。
box_router_module.controller = box_controller
app.include_router(box_router_module.router)

# app.main 原本直接 FileResponse static/index.html。
# 盒子版入口只在运行时注入一份 CSS/JS，不复制整套主页面，也不破坏原前端。
for _route in list(app.router.routes):
    if getattr(_route, "path", None) == "/" and "GET" in (getattr(_route, "methods", None) or set()):
        app.router.routes.remove(_route)
        break


@app.get("/", include_in_schema=False)
def _integrated_index():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = '<link rel="stylesheet" href="/static/box-integrated.css?v=2" />'
    js = '<script src="/static/box-integrated.js?v=2"></script>'
    html = html.replace("</head>", css + "\n</head>", 1)
    html = html.replace("</body>", js + "\n</body>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.on_event("startup")
def _start_box_controller():
    box_controller.start_if_enabled()


@app.on_event("shutdown")
def _stop_box_controller():
    box_controller.stop()
