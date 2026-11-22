"""断链影响复核端点：暴露受影响湖表视图与复核记录。

- GET  /api/impact/affected-view        受影响湖表视图（转换节点 × 下游湖表 × 可达路径）
- POST /api/impact/affected-view/rebuild 按当前核对过的血缘关系重算视图
- GET  /api/impact/review-records        复核记录（按目标湖表去重，内含全部相关路径）
- GET  /api/impact/review-records/{id}   复核记录详情，值班员点开即可看到输入版本
- POST /api/impact/review-records/{id}/actions  开始复核 / 完成复核
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionPayload, ActionResult
from app.services.affected_table import AffectedTableService
from app.services.review_record import ReviewRecordService
from app.store import store

router = APIRouter(prefix="/api/impact", tags=["断链影响复核"])


@router.get("/affected-view")
def get_affected_view(
    breakpoint: str | None = Query(default=None, description="按起始断点筛选"),
    table: str | None = Query(default=None, description="按受影响湖表筛选"),
) -> dict[str, Any]:
    """查看受影响湖表视图：每个受影响下游湖表都带本次核对过的可达路径。"""
    svc = AffectedTableService()
    rows = list(store.rows(svc.MODULE))
    if breakpoint:
        rows = [row for row in rows if breakpoint in str(row.get("起始断点", ""))]
    if table:
        rows = [row for row in rows if table.casefold() in str(row.get("受影响湖表", "")).casefold()]

    affected_tables = sorted({str(row.get("受影响湖表", "")) for row in rows})
    breakpoints = sorted({str(row.get("起始断点", "")) for row in rows})
    return {
        "total": len(rows),
        "affected_table_count": len(affected_tables),
        "breakpoints": breakpoints,
        "affected_tables": affected_tables,
        "items": rows,
    }


@router.post("/affected-view/rebuild", response_model=ActionResult)
def rebuild_affected_view() -> ActionResult:
    """手动按最新血缘核对结果重算受影响湖表视图。"""
    items = AffectedTableService().rebuild_affected_view()
    return ActionResult(
        ok=True,
        message=f"受影响湖表视图已刷新：{len(items)} 行，覆盖 {len({i['受影响湖表'] for i in items})} 张湖表",
    )


@router.get("/review-records")
def list_review_records(
    status: str | None = Query(default=None),
    batch: str | None = Query(default=None),
) -> dict[str, Any]:
    """复核记录列表：同一湖表只占一条，记录内含所有相关路径。"""
    svc = ReviewRecordService()
    rows = list(store.rows(svc.MODULE))
    if status:
        rows = [row for row in rows if row.get("status") == status]
    if batch:
        rows = [row for row in rows if row.get("处置批次") == batch]
    return {
        "total": len(rows),
        "items": rows,
    }


@router.get("/review-records/{entry_id}")
def get_review_record(entry_id: int) -> dict[str, Any]:
    """复核项目详情：值班员点开后可看到输入版本与要检查的下游表、路径。"""
    record = store.find("review_record", entry_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"复核记录 {entry_id} 不存在")
    # 附上视图中同湖表的逐转换节点路径，方便回看
    linked_view_rows = [
        row for row in store.rows("affected_table")
        if row.get("受影响湖表") == record.get("目标湖表")
    ]
    return {
        "record": record,
        "linked_view_rows": linked_view_rows,
    }


@router.post("/review-records/{entry_id}/actions", response_model=ActionResult)
def run_review_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    entry, message = ReviewRecordService().run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
