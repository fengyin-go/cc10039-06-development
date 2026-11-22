"""断链影响复核测试：受影响湖表视图 + 治理报告复核项目投递。

覆盖处置场景：
同一条转换之后有两条不同的下游路径——一条只到下一级，另一条绕过两个节点后
汇入同一张湖表；首段关系确认断开后，验证受影响湖表视图的目标范围、起始断点，
以及共享下游在复核记录里没有重复项且保留全部相关路径。

store 是全局单例，测试顺序执行并共享状态，因此本模块在开头自行把场景血缘
统一刷新到确定状态。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.store import store
from app.services.lineage_graph import LineageGraphService
from app.services.affected_table import AffectedTableService
from app.services.review_record import ReviewRecordService
from app.services.govern_report import GovernReportService

SCENARIO_LINEAGE_IDS = range(4, 10)
TRANSFORM = "dwd_order_di"
BREAKPOINT = "ods_order_raw → dwd_order_di"
SHARED_DOWNSTREAM = "ads_gmv_daily"
EXPECTED_TARGETS = {"ads_gmv_daily", "ads_order_quality", "dwm_order_agg", "dws_order_summary"}


def _refresh_scenario() -> None:
    svc = LineageGraphService()
    for lineage_id in SCENARIO_LINEAGE_IDS:
        svc.refresh_lineage(lineage_id)


@pytest.fixture(scope="module", autouse=True)
def _scenario_ready():
    _refresh_scenario()
    AffectedTableService().rebuild_affected_view()
    yield


class TestAffectedTableView:
    """受影响湖表视图：沿核对过的血缘关系呈现转换节点、下游表和可复查路径。"""

    def test_refresh_confirms_first_segment_breakpoint(self):
        """首段关系确认断开，起始断点可定位到具体边。"""
        broken = store.find("lineage_graph", 4)
        assert broken["status"] == "链路断裂"
        assert broken.get("断点说明")
        view_rows = store.rows("affected_table")
        assert view_rows, "刷新后应生成受影响湖表视图"
        assert all(row["起始断点"] == BREAKPOINT for row in view_rows)

    def test_view_target_scope_is_reachable_downstream_tables(self):
        """目标范围：转换节点经已建立边可达的全部远端湖表。"""
        view_rows = store.rows("affected_table")
        targets = {row["受影响湖表"] for row in view_rows}
        assert targets == EXPECTED_TARGETS
        # 转换节点自身与断链上游不应进入受影响范围
        assert TRANSFORM not in targets
        assert "ods_order_raw" not in targets

    def test_every_review_table_has_verified_reachable_path(self):
        """复核的每张表都带本次核对过的可达路径，且路径从转换节点起步。"""
        for row in store.rows("affected_table"):
            paths = row["可达路径"]
            assert paths, f"{row['受影响湖表']} 缺少可达路径"
            for path in paths:
                nodes = path.split(" → ")
                assert nodes[0] == TRANSFORM
                assert nodes[-1] == row["受影响湖表"]
            # 输入版本可点开复查
            assert row["输入版本"].startswith("v20261120@")

    def test_shared_downstream_keeps_both_path_shapes(self):
        """共享下游同时持有：只到下一级的路径 + 绕过两个节点的路径。"""
        shared_rows = [
            row for row in store.rows("affected_table")
            if row["受影响湖表"] == SHARED_DOWNSTREAM
        ]
        assert len(shared_rows) == 1
        paths = set(shared_rows[0]["可达路径"])
        one_hop = f"{TRANSFORM} → {SHARED_DOWNSTREAM}"
        bypass = f"{TRANSFORM} → dwm_order_agg → dws_order_summary → {SHARED_DOWNSTREAM}"
        assert one_hop in paths
        assert bypass in paths
        assert shared_rows[0]["可达路径数"] == 2

    def test_unverified_relations_do_not_create_paths(self):
        """待验证关系未核对，不参与可达路径；把一条已建立关系改回待验证后视图收窄。"""
        svc = LineageGraphService()
        direct = store.find("lineage_graph", 9)
        original_status = direct["status"]
        direct["status"] = "待验证"
        try:
            view = AffectedTableService().rebuild_affected_view()
            shared = next(row for row in view if row["受影响湖表"] == SHARED_DOWNSTREAM)
            assert f"{TRANSFORM} → {SHARED_DOWNSTREAM}" not in shared["可达路径"]
            # 绕过两个节点的长路径仍在
            assert any(
                "dwm_order_agg" in path and "dws_order_summary" in path
                for path in shared["可达路径"]
            )
        finally:
            direct["status"] = original_status
            svc.refresh_lineage(9)

    def test_mark_broken_rebuilds_view(self):
        """手动标记断裂同样刷新视图。"""
        before = len(store.rows("affected_table"))
        entry, msg = LineageGraphService().mark_broken(4)
        assert entry is not None
        assert entry["status"] == "链路断裂"
        assert len(store.rows("affected_table")) == before
        # 复位
        LineageGraphService().refresh_lineage(4)


class TestGovernOptimizationDelivery:
    """治理报告执行优化：用视图路径生成实际复核项目。"""

    @pytest.fixture(autouse=True)
    def _delivery_ready(self):
        """直接按当前受影响湖表视图投递一批复核项目，不依赖种子报告剩余状态。"""
        _refresh_scenario()
        AffectedTableService().rebuild_affected_view()
        self.scope = AffectedTableService().review_scope()
        ReviewRecordService().submit_from_scope(self.scope, "unit-delivery", -100)
        yield

    def test_optimization_delivers_review_records_for_full_scope(self):
        """投递目标范围与受影响湖表视图一致，起始断点写入复核项目。"""
        records = [
            r for r in store.rows("review_record")
            if r.get("处置批次") == "unit-delivery"
        ]
        record_tables = {r["目标湖表"] for r in records}
        assert record_tables == EXPECTED_TARGETS
        for record in records:
            assert any(BREAKPOINT == bp for bp in record["起始断点"])
            assert record["输入版本"], "值班员点开项目应能找到输入版本"
            assert record["关联路径"], "复核项目必须带具体要检查的路径"

    def test_optimization_action_records_delivery_on_report(self):
        """治理报告执行优化后，报告上记录本次复核范围与去重统计。"""
        gov = GovernReportService()
        report = next(
            (gr for gr in store.rows("govern_report") if gr["status"] == "待生成"),
            None,
        )
        if report is None:
            pytest.skip("种子报告已被其他测试消费")
        generated, msg = gov.generate_report(report["id"])
        assert generated is not None, msg
        entry, msg = gov.execute_optimization(report["id"])
        assert entry is not None, msg
        assert set(entry["本次复核范围"]) == EXPECTED_TARGETS
        # 视图里的目标此前已投递过，本次应全部命中去重
        assert entry["新增复核记录"] == 0
        assert entry["去重合并记录"] == len(EXPECTED_TARGETS)

    def test_shared_downstream_single_record_keeps_all_paths(self):
        """两路汇入同一湖表：只有一条复核记录，记录里能看到所有相关路径。"""
        shared = [
            r for r in store.rows("review_record")
            if r["目标湖表"] == SHARED_DOWNSTREAM
        ]
        assert len(shared) == 1
        paths = set(shared[0]["关联路径"])
        assert f"{TRANSFORM} → {SHARED_DOWNSTREAM}" in paths
        assert f"{TRANSFORM} → dwm_order_agg → dws_order_summary → {SHARED_DOWNSTREAM}" in paths

    def test_duplicate_submission_merges_instead_of_inserting(self):
        """重复投递不新增复核记录，只把路径并入原有记录。"""
        review_svc = ReviewRecordService()
        before_ids = {r["id"] for r in store.rows("review_record")}
        created, merged = review_svc.submit_from_scope(self.scope, "recheck-dup", -1)
        after_ids = {r["id"] for r in store.rows("review_record")}
        assert created == []
        assert before_ids == after_ids
        assert len(merged) == len(self.scope)
        shared = next(r for r in store.rows("review_record") if r["目标湖表"] == SHARED_DOWNSTREAM)
        assert len(shared["关联路径"]) == 2
        assert -1 in shared["来源报告id列表"]

    def test_review_record_detail_links_back_to_view(self):
        """复核记录详情能回到受影响湖表视图的逐节点路径。"""
        record = next(
            r for r in store.rows("review_record") if r["目标湖表"] == SHARED_DOWNSTREAM
        )
        linked = [
            row for row in store.rows("affected_table")
            if row["受影响湖表"] == record["目标湖表"]
        ]
        assert linked and all(row["转换节点"] == TRANSFORM for row in linked)

    def test_complete_review_syncs_view_status(self):
        """完成复核后受影响湖表视图同步为已复核。"""
        review_svc = ReviewRecordService()
        record = next(
            r for r in store.rows("review_record")
            if r["目标湖表"] == "ads_order_quality" and r["status"] != "已复核"
        )
        review_svc.start_review(record["id"])
        entry, msg = review_svc.complete_review(record["id"])
        assert entry is not None
        view_rows = [
            row for row in store.rows("affected_table")
            if row["受影响湖表"] == "ads_order_quality"
        ]
        assert view_rows and all(row["status"] == "已复核" for row in view_rows)
        # 已复核项目不允许重复开始
        again, message = review_svc.start_review(record["id"])
        assert again is None
        assert "已完成" in message
