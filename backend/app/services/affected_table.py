"""受影响湖表视图领域服务。

湖表血缘完成刷新后，本服务沿**本次核对过（状态为已建立）**的血缘关系构图，
从断裂首段指向的转换节点出发，枚举仍可继续产出的下游湖表和全部可达路径，
形成「受影响湖表视图」。治理报告执行优化时按视图里的路径投递复核项目。

关键约定：
- 只有「已建立」的血缘边可用于可达路径；「待验证」的关系尚未核对，不参与；
  「链路断裂」关系的首段边记为起始断点，其下游边若被另一条已建立关系覆盖，
  仍可通行，对应远端湖表继续产出、仍需回看。
- 视图按「转换节点 × 受影响下游湖表」逐行呈现，每行带全部可达路径；
  复核记录再按目标湖表去重合并（见 review_record 服务）。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 单条可达路径最多经过的边数，防止异常成环数据造成无限枚举
MAX_PATH_DEPTH = 12


class AffectedTableService(BaseModuleService):
    MODULE = "affected_table"
    ENTITY = "受影响湖表"
    OPERATION_TERM = "刷新视图"
    LIST_FIELDS = [
        "受影响湖表", "转换节点", "起始断点", "可达路径数", "输入版本", "最近核对时间",
    ]
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER = ["待复核", "已复核"]
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS: list[str] = []

    # ---- 基础参考数据 ----

    def _registered_tables(self) -> dict[str, dict[str, Any]]:
        return {str(row.get("表名", "")): row for row in store.rows("lake_table")}

    def _latest_snapshot(self, table_name: str, only_available: bool = True) -> dict[str, Any] | None:
        """取湖表最近一次快照；only_available 时只看「可用」快照。"""
        snapshots = [
            snap for snap in store.rows("snapshot_version")
            if snap.get("湖表名") == table_name
            and (not only_available or snap.get("status") == "可用")
        ]
        if not snapshots:
            return None
        return sorted(snapshots, key=lambda item: str(item.get("快照时间戳", "")))[-1]

    def _build_lineage_graph(self) -> tuple[
        dict[str, list[dict[str, Any]]],
        list[dict[str, Any]],
        str,
    ]:
        """从血缘关系构图。

        返回：
        - adjacency：仅含已建立边的邻接表 src -> [{dst, lineage_ids, 血缘类型}]
        - broken_first_edges：断裂关系的首段边 [{src, dst(转换节点), lineage_id}]
        - latest_checked_at：参与构图关系的最近记录时间
        """
        edge_index: dict[tuple[str, str], dict[str, Any]] = {}
        broken_first_edges: list[dict[str, Any]] = []
        latest_checked_at = ""

        for lin in store.rows("lineage_graph"):
            upstream = str(lin.get("上游源", "")).strip()
            transform = str(lin.get("中间转换", "")).strip()
            downstream = str(lin.get("下游消费", "")).strip()
            lineage_id = int(lin.get("id", 0))
            status = lin.get("status")
            recorded_at = str(lin.get("记录时间", ""))
            if recorded_at > latest_checked_at:
                latest_checked_at = recorded_at

            if status == "链路断裂":
                # 首段关系确认断开：记录起始断点
                broken_first_edges.append({
                    "src": upstream,
                    "transform": transform,
                    "lineage_id": lineage_id,
                })
                continue
            if status != "已建立":
                # 待验证等状态：本次尚未核对，不参与可达路径
                continue

            for src, dst in ((upstream, transform), (transform, downstream)):
                if not src or not dst or src == dst:
                    # 自环（同表转换占位）不构成可复查的下游路径
                    continue
                edge = edge_index.setdefault((src, dst), {
                    "src": src,
                    "dst": dst,
                    "lineage_ids": [],
                    "血缘类型": lin.get("血缘类型", ""),
                })
                if lineage_id not in edge["lineage_ids"]:
                    edge["lineage_ids"].append(lineage_id)

        adjacency: dict[str, list[dict[str, Any]]] = {}
        for edge in edge_index.values():
            adjacency.setdefault(edge["src"], []).append(edge)
        return adjacency, broken_first_edges, latest_checked_at

    def _enumerate_paths(
        self, adjacency: dict[str, list[dict[str, Any]]], source: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """枚举 source 到每个可达下游节点的全部简单路径（含路径经过的血缘边 id）。"""
        results: dict[str, list[dict[str, Any]]] = {}

        def walk(node: str, nodes: list[str], edge_ids: list[int], depth: int) -> None:
            if depth >= MAX_PATH_DEPTH:
                return
            for edge in adjacency.get(node, []):
                nxt = edge["dst"]
                if nxt in nodes:
                    # 成环保护：DAG 正常不会命中
                    continue
                next_nodes = [*nodes, nxt]
                next_edge_ids = [*edge_ids, *edge["lineage_ids"]]
                path = {"nodes": next_nodes, "edge_ids": next_edge_ids}
                results.setdefault(nxt, []).append(path)
                walk(nxt, next_nodes, next_edge_ids, depth + 1)

        walk(source, [source], [], 0)
        return results

    @staticmethod
    def _format_path(nodes: list[str]) -> str:
        return " → ".join(nodes)

    # ---- 视图构建 ----

    def rebuild_affected_view(self) -> list[dict[str, Any]]:
        """按当前核对过的血缘关系重建受影响湖表视图，返回新视图行。"""
        adjacency, broken_edges, latest_checked_at = self._build_lineage_graph()
        registered = self._registered_tables()

        view_rows: list[dict[str, Any]] = []
        # 同一转换节点可能被多条断裂首段指向，按转换节点归并枚举一次
        seen_transforms: set[str] = set()
        for broken in broken_edges:
            transform = broken["transform"]
            if not transform or transform in seen_transforms:
                continue
            seen_transforms.add(transform)

            paths_by_table = self._enumerate_paths(adjacency, transform)
            transform_snapshot = self._latest_snapshot(transform, only_available=True)
            input_version = (
                f"{transform_snapshot.get('版本号')}@{transform_snapshot.get('快照时间戳')}"
                if transform_snapshot else "无可用快照"
            )

            for table, paths in sorted(paths_by_table.items()):
                formatted_paths = [self._format_path(path["nodes"]) for path in paths]
                # 同一对 (转换节点, 湖表) 下去重路径，保留稳定顺序
                unique_paths = list(dict.fromkeys(formatted_paths))
                unique_edge_chains: list[list[int]] = []
                for path in paths:
                    if path["edge_ids"] not in unique_edge_chains:
                        unique_edge_chains.append(path["edge_ids"])

                row = {
                    "受影响湖表": table,
                    "转换节点": transform,
                    "起始断点": f"{broken['src']} → {transform}",
                    "断裂血缘id": broken["lineage_id"],
                    "断链上游源": broken["src"],
                    "湖表已注册": table in registered,
                    "可达路径": unique_paths,
                    "可达路径数": len(unique_paths),
                    "路径血缘边": unique_edge_chains,
                    "输入版本": input_version,
                    "最近核对时间": latest_checked_at,
                    "status": "待复核",
                    "pending": True,
                    "abnormal": False,
                }
                view_rows.append(row)

        # 视图整体重算：清空旧行后按稳定顺序重写
        table_rows = store.rows(self.MODULE)
        table_rows.clear()
        for index, row in enumerate(view_rows, start=1):
            row["id"] = index
            table_rows.append(row)

        store.log_event("affected_view_rebuilt", "lineage_graph", self.MODULE, {
            "view_rows": len(view_rows),
            "breakpoints": sorted({row["起始断点"] for row in view_rows}),
            "affected_tables": sorted({row["受影响湖表"] for row in view_rows}),
        })
        return view_rows

    def review_scope(self) -> dict[str, dict[str, Any]]:
        """供治理报告投递复核项目：按目标湖表归并视图行，路径跨转换节点合并。"""
        scope: dict[str, dict[str, Any]] = {}
        for row in store.rows(self.MODULE):
            table = str(row.get("受影响湖表", ""))
            item = scope.setdefault(table, {
                "目标湖表": table,
                "转换节点": [],
                "起始断点": [],
                "可达路径": [],
                "输入版本": [],
                "断裂血缘id": [],
                "湖表已注册": bool(row.get("湖表已注册")),
            })
            for field in ("转换节点", "起始断点", "输入版本"):
                value = row.get(field, "")
                if value and value not in item[field]:
                    item[field].append(value)
            for lineage_id in (
                row.get("断裂血缘id", [])
                if isinstance(row.get("断裂血缘id"), list)
                else [row.get("断裂血缘id")]
            ):
                if lineage_id and lineage_id not in item["断裂血缘id"]:
                    item["断裂血缘id"].append(lineage_id)
            for path in row.get("可达路径", []):
                if path not in item["可达路径"]:
                    item["可达路径"].append(path)
        return scope

    def mark_reviewed(self, table_name: str) -> int:
        """复核完成后回写视图状态，返回更新行数。"""
        updated = 0
        for row in store.rows(self.MODULE):
            if row.get("受影响湖表") == table_name:
                row["status"] = "已复核"
                row["pending"] = False
                updated += 1
        return updated
