"""湖表血缘领域服务：维护数据从原始源到湖表再到消费端的完整血缘链路。

上游接收入湖管道和质量监控事件，下游联动治理报告。
支持血缘链路完整性检查和断裂修复。

刷新（核对）规则：
- 血缘三段中的湖表节点若已在「湖表注册」登记，按注册湖表核对：
  首段上游没有「可用」快照即确认首段断开；首段可用而下游无可用快照时记待验证。
- 未登记的节点沿用旧的模块占位核对，保证种子占位数据行为不变。
- 每次刷新或标记断裂后都重建受影响湖表视图，让断链影响立刻可查。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class LineageGraphService(BaseModuleService):
    MODULE = "lineage_graph"
    ENTITY = "血缘关系"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["上游源", "中间转换", "下游消费", "血缘类型", "记录时间"]
    REQUIRED_FIELDS = ["上游源", "中间转换", "下游消费"]
    STATUS_ORDER = ["已建立", "待验证", "链路断裂"]
    ACTION_RULES = {"刷新血缘": "已建立", "标记断裂": "链路断裂"}
    NEGATIVE_ACTIONS = []

    def _registered_table_names(self) -> set[str]:
        return {str(row.get("表名", "")) for row in store.rows("lake_table")}

    def _has_available_snapshot(self, table_name: str) -> bool:
        return any(
            snap.get("湖表名") == table_name and snap.get("status") == "可用"
            for snap in store.rows("snapshot_version")
        )

    def _latest_snapshot_label(self, table_name: str) -> str:
        snapshots = [
            snap for snap in store.rows("snapshot_version")
            if snap.get("湖表名") == table_name
        ]
        if not snapshots:
            return "无快照"
        latest = sorted(snapshots, key=lambda item: str(item.get("快照时间戳", "")))[-1]
        return f"{latest.get('版本号')}@{latest.get('快照时间戳')}（{latest.get('status')}）"

    def _rebuild_affected_view(self) -> None:
        # 局部导入，避免服务注册阶段出现循环依赖
        from app.services.affected_table import AffectedTableService

        AffectedTableService().rebuild_affected_view()

    def refresh_lineage(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """刷新血缘链路：核对上下游注册湖表、快照版本的可用性。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "血缘关系不存在"

        upstream = entry.get("上游源", "")
        transform = entry.get("中间转换", "")
        downstream = entry.get("下游消费", "")
        registered = self._registered_table_names()
        scenario_nodes = {upstream, transform, downstream}

        # 已登记湖表场景：按快照版本核对首段与下游
        if scenario_nodes <= registered:
            entry["上游可用快照"] = self._latest_snapshot_label(upstream)
            entry["转换可用快照"] = self._latest_snapshot_label(transform)
            entry["下游可用快照"] = self._latest_snapshot_label(downstream)

            if not self._has_available_snapshot(upstream):
                entry["status"] = "链路断裂"
                entry["abnormal"] = True
                entry["pending"] = False
                entry["断点说明"] = f"首段上游 {upstream} 无可用输入版本"
                store.log_event("lineage_broken", "lineage_graph", "govern_report", {
                    "lineage_id": entry_id,
                    "reason": f"上游湖表 {upstream} 无可用快照",
                    "breakpoint": f"{upstream} → {transform}",
                })
                self._rebuild_affected_view()
                return entry, (
                    f"血缘链路断裂：首段上游 {upstream} 无可用快照，"
                    f"起始断点 {upstream} → {transform}"
                )

            if not self._has_available_snapshot(downstream) and downstream != transform:
                entry["status"] = "待验证"
                entry["abnormal"] = True
                store.log_event("lineage_uncertain", "lineage_graph", "ingest_pipeline", {
                    "lineage_id": entry_id,
                    "reason": f"下游湖表 {downstream} 无可用快照",
                })
                self._rebuild_affected_view()
                return entry, f"血缘链路待验证：下游湖表 {downstream} 无可用快照"

            entry["status"] = "已建立"
            entry["pending"] = False
            entry["abnormal"] = False
            entry["断点说明"] = ""
            store.log_event("lineage_refreshed_to_govern", "lineage_graph", "govern_report", {
                "lineage_id": entry_id,
                "upstream": upstream,
                "transform": transform,
                "downstream": downstream,
                "upstream_snapshot": entry["上游可用快照"],
            })
            self._rebuild_affected_view()
            return entry, f"血缘链路已刷新：{upstream} → {transform} → {downstream}"

        # 占位/跨模块场景：沿用模块数据有无的核对方式
        module_names = set(store.module_names())
        upstream_count = len(store.rows(upstream)) if upstream in module_names else 0
        down_count = len(store.rows(downstream)) if downstream in module_names else 0

        if upstream_count == 0:
            entry["status"] = "链路断裂"
            entry["abnormal"] = True
            store.log_event("lineage_broken", "lineage_graph", "govern_report", {
                "lineage_id": entry_id,
                "reason": f"上游模块 {upstream} 无数据",
            })
            self._rebuild_affected_view()
            return entry, f"血缘链路断裂：上游模块 {upstream} 无数据"

        if down_count == 0:
            entry["status"] = "待验证"
            entry["abnormal"] = True
            store.log_event("lineage_uncertain", "lineage_graph", "ingest_pipeline", {
                "lineage_id": entry_id,
                "reason": f"下游模块 {downstream} 无数据",
            })
            self._rebuild_affected_view()
            return entry, f"血缘链路待验证：下游模块 {downstream} 无数据"

        entry["status"] = "已建立"
        entry["pending"] = False
        entry["abnormal"] = False
        store.log_event("lineage_refreshed_to_govern", "lineage_graph", "govern_report", {
            "lineage_id": entry_id,
            "upstream": upstream,
            "transform": transform,
            "downstream": downstream,
            "upstream_count": upstream_count,
            "downstream_count": down_count,
        })
        self._rebuild_affected_view()
        return entry, f"血缘链路已刷新：{upstream} → {transform} → {downstream}"

    def mark_broken(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """标记血缘链路断裂：通知下游模块并刷新受影响湖表视图。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "血缘关系不存在"

        entry["status"] = "链路断裂"
        entry["pending"] = False
        entry["abnormal"] = True
        entry["断点说明"] = "值班员手动确认断裂"

        store.log_event("lineage_marked_broken", "lineage_graph", "govern_report", {
            "lineage_id": entry_id,
            "downstream": entry.get("下游消费", ""),
            "breakpoint": f"{entry.get('上游源')} → {entry.get('中间转换')}",
        })
        self._rebuild_affected_view()

        return entry, f"血缘链路 {entry.get('上游源')} → {entry.get('下游消费')} 已标记为断裂"

    def lineage_for_table(self, table_name: str) -> list[dict[str, Any]]:
        """查询指定湖表的血缘链路。"""
        return [
            lin for lin in store.rows(self.MODULE)
            if table_name in (lin.get("上游源", ""), lin.get("中间转换", ""),
                              lin.get("下游消费", ""))
        ]

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "刷新血缘":
            return self.refresh_lineage(entry_id)
        if action == "标记断裂":
            return self.mark_broken(entry_id)
        return super().run_action(entry_id, action)
