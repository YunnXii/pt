"""V7: V6 strategy + hardened RSS transport/parser shared by Race and Trend."""

import app.box_service_v2 as box_service_v2_module
import app.box_service_v6 as box_service_v6_module
from app.box_rss import fetch_rss
from app.box_service_v6 import BoxControllerV6


# V2 的 run_once() 与 V6 的 Race Lane 都各自在模块全局引用 fetch_rss。
# 在统一入口处替换为同一个健壮实现，避免复制整套控制器代码。
box_service_v2_module.fetch_rss = fetch_rss
box_service_v6_module.fetch_rss = fetch_rss


class BoxControllerV7(BoxControllerV6):
    """V6 + hardened RSS parser/diagnostics."""


controller = BoxControllerV7()
