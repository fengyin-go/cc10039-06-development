"""复核记录领域服务。

治理报告执行优化时，依据受影响湖表视图里的可达路径生成实际复核项目，
值班员点开项目即可看到输入版本和具体要检查的下游湖表。

去重规则（对应处置场景「两路汇到同一个下游时重复提交只写入一条复核记录」）：
- 同一处置批次内按「目标湖表」唯一；再次提交命中已有记录时不新增，
  只把新出现的路径、起始断点、血缘边并入同一条记录，记录里仍能看到所有相关路径。
"""
from __future__ import annotations

import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ReviewRecordService(BaseModuleService):
    MODULE = "review_record"
    ENTITY = "复核记录"
    OPERATION_TERM = "投递"
    LIST_FIELDS = [
        "复核编号", "目标湖表", "起始断点", "关联路径数", "输入版本", "复核状态", "处置批次",
    ]
    REQUIRED_FIELDS = ["目标湖表"]
    STATUS_ORDER = ["待复核", "复核中", "已复核"]
    ACTION_RULES = {"开始复核": "复核中", "完成复核": "已复核"}
    NEGATIVE_ACTIONS: list[str] = []

    def _next_id(self) -> int:
        rows = store.rows(self.MODULE)
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    def submit_from_scope(
        self, scope: dict[str, dict[str, Any]], batch: str, report_id: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按受影响湖表视图的复核范围投递复核记录。

        返回 (新增记录列表, 命中去重的更新记录列表)。
        """
        rows = store.rows(self.MODULE)
        created: list[dict[str, Any]] = []
        merged: list[dict[str, Any]] = []
        now = datetime.datetime.now().isoformat()

        for table, item in sorted(scope.items()):
            existing = next((row for row in rows if row.get("目标湖表") == table), None)
            if existing is None:
                entry_id = self._next_id()
                entry = {
                    "id": entry_id,
                    "复核编号": f"RV-{batch}-{entry_id:03d}",
                    "目标湖表": table,
                    "起始断点": list(item.get("起始断点", [])),
                    "关联路径": list(item.get("可达路径", [])),
                    "关联路径数": len(item.get("可达路径", [])),
                    "输入版本": list(item.get("输入版本", [])),
                    "涉及转换节点": list(item.get("转换节点", [])),
                    "关联血缘id": list(item.get("断裂血缘id", [])),
                    "处置批次": batch,
                    "来源报告id": report_id,
                    "复核状态": "待复核",
                    "status": "待复核",
                    "pending": True,
                    "abnormal": False,
                    "投递时间": now,
                    "复核时间": "",
                }
                rows.append(entry)
                created.append(entry)
                continue

            # 重复提交：只写入一条复核记录，合并新出现的路径/断点/边
            for field in ("关联路径", "起始断点", "输入版本", "涉及转换节点"):
                source_key = {
                    "关联路径": "可达路径",
                    "起始断点": "起始断点",
                    "输入版本": "输入版本",
                    "涉及转换节点": "转换节点",
                }[field]
                merged_values = list(existing.get(field, []))
                for value in item.get(source_key, []):
                    if value not in merged_values:
                        merged_values.append(value)
                existing[field] = merged_values
            for lineage_id in item.get("断裂血缘id", []):
                if lineage_id not in existing.get("关联血缘id", []):
                    existing.setdefault("关联血缘id", []).append(lineage_id)
            if report_id not in existing.get("来源报告id列表", []):
                existing.setdefault("来源报告id列表", []).append(report_id)
            existing["关联路径数"] = len(existing.get("关联路径", []))
            existing["合并次数"] = int(existing.get("合并次数", 0)) + 1
            merged.append(existing)

        store.log_event("review_records_submitted", "govern_report", self.MODULE, {
            "batch": batch,
            "report_id": report_id,
            "created": len(created),
            "merged": len(merged),
            "target_tables": sorted(scope),
        })
        return created, merged

    def start_review(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "复核记录不存在"
        if entry["status"] == "已复核":
            return None, "该复核记录已完成，不可重新开始"
        entry["status"] = "复核中"
        entry["复核状态"] = "复核中"
        entry["pending"] = True
        return entry, f"复核项目 {entry.get('复核编号')} 已开始，可按关联路径逐项回看"

    def complete_review(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        from app.services.affected_table import AffectedTableService

        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "复核记录不存在"
        entry["status"] = "已复核"
        entry["复核状态"] = "已复核"
        entry["pending"] = False
        entry["复核时间"] = datetime.datetime.now().isoformat()
        AffectedTableService().mark_reviewed(str(entry.get("目标湖表", "")))

        store.log_event("review_record_completed", self.MODULE, "lineage_graph", {
            "review_id": entry_id,
            "target_table": entry.get("目标湖表"),
            "paths": entry.get("关联路径", []),
        })
        return entry, f"复核项目 {entry.get('复核编号')} 已完成，受影响湖表视图已同步状态"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "开始复核":
            return self.start_review(entry_id)
        if action == "完成复核":
            return self.complete_review(entry_id)
        return super().run_action(entry_id, action)
