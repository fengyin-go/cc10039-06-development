"""治理报告领域服务：定期生成湖内治理报告和自动优化建议。

治理报告是跨模块数据流的最终汇聚点：汇总冷热分层迁移、小文件合并、
保留策略清理、质量监控结果和血缘链路状态，生成综合治理报告。
"""
from __future__ import annotations

import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class GovernReportService(BaseModuleService):
    MODULE = "govern_report"
    ENTITY = "治理报告"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["报告周期", "治理指标", "优化建议", "执行状态", "审批状态"]
    REQUIRED_FIELDS = ["报告周期", "治理指标", "优化建议"]
    STATUS_ORDER = ["待生成", "已生成", "已审批", "已执行"]
    ACTION_RULES = {"生成报告": "已生成", "执行优化": "已执行"}
    NEGATIVE_ACTIONS = []

    def generate_report(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """生成治理报告：跨模块聚合数据生成综合治理报告。
        这是整个系统的最终数据产物消费入口。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "治理报告不存在"
        if entry.get("status") != "待生成":
            return None, f"治理报告状态为{entry.get('status')}，不可生成"

        # 1) 汇总冷热分层迁移
        tier_events = store.events_for_module("storage_tier")
        tier_migrations = [e for e in tier_events if e["event_type"] == "tier_migration_executed"]
        total_migrated = sum(
            e["payload"].get("migrated_partition_count", 0) for e in tier_migrations
        )

        # 2) 汇总小文件合并
        compaction_events = store.events_for_module("file_compaction")
        completed_compactions = [e for e in compaction_events if e["event_type"] == "compaction_completed"]

        # 3) 汇总保留策略清理
        retention_events = store.events_for_module("retention_mgr")
        completed_cleanups = [e for e in retention_events if e["event_type"] == "retention_cleanup_executed"]

        # 4) 汇总质量监控结果
        quality_events = store.events_for_module("quality_monitor")
        quality_failures = [e for e in quality_events if e["event_type"] == "quality_failed_to_lineage"]
        quality_passes = [e for e in quality_events if e["event_type"] == "quality_passed_to_govern"]

        # 5) 汇总血缘链路状态
        lineage_rows = store.rows("lineage_graph")
        broken_lineages = [l for l in lineage_rows if l.get("status") == "链路断裂"]
        uncertain_lineages = [l for l in lineage_rows if l.get("status") == "待验证"]

        # 6) 汇总回填任务
        backfill_events = store.events_for_module("backfill_job")
        completed_backfills = [e for e in backfill_events if e["event_type"] == "backfill_completed_to_govern"]

        # 组装报告
        report_data = {
            "report_period": entry.get("报告周期", ""),
            "generated_at": datetime.datetime.now().isoformat(),
            "storage_governance": {
                "tier_migrations": len(tier_migrations),
                "total_partitions_migrated": total_migrated,
                "completed_compactions": len(completed_compactions),
                "completed_cleanups": len(completed_cleanups),
            },
            "quality_monitoring": {
                "quality_checks": len(quality_passes) + len(quality_failures),
                "failures": len(quality_failures),
                "passes": len(quality_passes),
            },
            "lineage_health": {
                "total_lineages": len(lineage_rows),
                "broken": len(broken_lineages),
                "uncertain": len(uncertain_lineages),
                "healthy": len(lineage_rows) - len(broken_lineages) - len(uncertain_lineages),
            },
            "backfill_summary": {
                "completed_backfills": len(completed_backfills),
            },
        }

        # 计算优化建议
        suggestions = []
        if len(broken_lineages) > 0:
            suggestions.append(f"修复 {len(broken_lineages)} 条断裂血缘链路")
        if len(quality_failures) > 0:
            suggestions.append(f"处理 {len(quality_failures)} 个质量不达标指标")
        if total_migrated > 0:
            suggestions.append(f"冷热迁移有效，已迁移 {total_migrated} 个分区")
        if len(completed_compactions) > 0:
            suggestions.append(f"小文件合并完成 {len(completed_compactions)} 个任务")

        entry["status"] = "已生成"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["优化建议"] = "；".join(suggestions) if suggestions else "当前治理状态良好"
        entry["执行状态"] = "报告已生成"

        store.log_event("govern_report_generated", "govern_report", "govern_report", {
            "report_id": entry_id,
            "report_data": report_data,
        })

        return entry, (
            f"治理报告已生成：迁移分区={total_migrated}，质量检查={len(quality_passes) + len(quality_failures)}，"
            f"血缘链路={len(lineage_rows)}，回填任务={len(completed_backfills)}"
        )

    def execute_optimization(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """执行治理优化：根据报告建议触发实际的治理操作。

        依据受影响湖表视图里的可达路径投递复核项目：值班员点开复核记录即可
        看到输入版本与具体要检查的下游湖表；同一湖表多路汇入时只写一条记录，
        记录内保留所有相关路径。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "治理报告不存在"
        if entry.get("status") not in ("已生成", "已审批"):
            return None, f"治理报告状态为{entry.get('status')}，不可执行优化"

        # 触发血缘链路修复
        broken_lineages = [
            l for l in store.rows("lineage_graph")
            if l.get("status") == "链路断裂"
        ]
        for lin in broken_lineages:
            lin["pending"] = True

        # 触发质量监控重新检查
        quality_rules = store.rows("quality_monitor")
        for qr in quality_rules:
            if qr.get("status") == "已启用":
                qr["pending"] = True

        # 依据受影响湖表视图生成实际复核项目（按目标湖表去重）
        from app.services.affected_table import AffectedTableService
        from app.services.review_record import ReviewRecordService

        affected_svc = AffectedTableService()
        review_svc = ReviewRecordService()
        # 执行前先按当前核对过的血缘关系重算视图，保证投递范围最新
        affected_svc.rebuild_affected_view()
        scope = affected_svc.review_scope()
        batch = datetime.datetime.now().strftime("%Y%m%d%H%M")
        created, merged = review_svc.submit_from_scope(scope, batch, entry_id)

        affected_tables = sorted(scope)
        entry["status"] = "已执行"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["执行状态"] = "优化已执行"
        entry["本次复核范围"] = affected_tables
        entry["复核批次"] = batch
        entry["新增复核记录"] = len(created)
        entry["去重合并记录"] = len(merged)

        store.log_event("govern_optimization_executed", "govern_report", "govern_report", {
            "report_id": entry_id,
            "broken_lineages_triggered": len(broken_lineages),
            "quality_rules_retriggered": len(quality_rules),
            "review_batch": batch,
            "review_targets": affected_tables,
            "review_created": len(created),
            "review_merged": len(merged),
        })

        return entry, (
            f"治理优化已执行：触发 {len(broken_lineages)} 条血缘修复、"
            f"{len(quality_rules)} 个质量规则；投递复核项目 {len(created)} 条"
            + (f"，去重合并 {len(merged)} 条" if merged else "")
            + f"，覆盖湖表 {len(affected_tables)} 张"
        )

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "生成报告":
            return self.generate_report(entry_id)
        if action == "执行优化":
            return self.execute_optimization(entry_id)
        return super().run_action(entry_id, action)