"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, cross_module, impact

# 含具体子路径的路由（impact/cross_module）必须先于通用资源路由
# /api/{module}/{entry_id} 注册，否则固定路径会被动态路径抢先匹配。
ROUTERS = [impact, cross_module, resource]