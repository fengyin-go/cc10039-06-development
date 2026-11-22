<template>
  <section class="page impact-page" data-testid="impact-review">
    <header class="page-head">
      <div>
        <h2>断链影响复核台</h2>
        <p class="page-desc">
          沿本次核对过的血缘关系呈现转换节点、受影响下游湖表与可复查路径；
          治理报告执行优化后在此按路径复核，值班员点开项目即可看到输入版本与目标湖表。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="rebuild-view" type="button" @click="rebuildView">
          刷新受影响视图
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">起始断点</span>
        <strong class="stat-value">{{ view.breakpoints.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">受影响湖表</span>
        <strong class="stat-value">{{ view.affected_table_count }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">视图条目</span>
        <strong class="stat-value">{{ view.total }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">复核项目（去重后）</span>
        <strong class="stat-value">{{ reviewTotal }}</strong>
      </article>
    </div>

    <h3 class="section-title">受影响湖表视图</h3>
    <p v-if="view.breakpoints.length" class="breakpoint-line" data-testid="breakpoint-line">
      起始断点：
      <span v-for="point in view.breakpoints" :key="point" class="breakpoint-tag">{{ point }}</span>
    </p>
    <table class="data-table" data-testid="affected-table">
      <thead>
        <tr>
          <th>受影响湖表</th>
          <th>转换节点</th>
          <th>起始断点</th>
          <th>本次核对过的可达路径</th>
          <th>输入版本</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in view.items" :key="row.id" :data-testid="`affected-row-${row.id}`">
          <td>{{ row.受影响湖表 }}</td>
          <td>{{ row.转换节点 }}</td>
          <td>{{ row.起始断点 }}</td>
          <td>
            <ol class="path-list">
              <li v-for="path in row.可达路径" :key="path" class="path-item">{{ path }}</li>
            </ol>
          </td>
          <td class="version-cell">{{ row.输入版本 }}</td>
          <td>
            <span class="status-pill" :data-status="row.status">{{ row.status }}</span>
          </td>
        </tr>
        <tr v-if="!view.items.length">
          <td colspan="6" class="empty-state">当前没有断链影响，先在「湖表血缘」刷新血缘关系</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">复核记录（同一湖表仅一条，内含全部相关路径）</h3>
    <table class="data-table" data-testid="review-table">
      <thead>
        <tr>
          <th>复核编号</th>
          <th>目标湖表</th>
          <th>起始断点</th>
          <th>关联路径</th>
          <th>输入版本</th>
          <th>处置批次</th>
          <th>复核状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="record in reviewRecords" :key="record.id" :data-testid="`review-row-${record.id}`">
          <td>{{ record.复核编号 }}</td>
          <td>{{ record.目标湖表 }}</td>
          <td>
            <span v-for="point in record.起始断点" :key="point" class="breakpoint-tag">{{ point }}</span>
          </td>
          <td>
            <ol class="path-list compact">
              <li v-for="path in record.关联路径" :key="path" class="path-item">{{ path }}</li>
            </ol>
          </td>
          <td class="version-cell">
            <span v-for="version in record.输入版本" :key="version">{{ version }}</span>
          </td>
          <td>{{ record.处置批次 }}</td>
          <td>
            <span class="status-pill" :data-status="record.status">{{ record.复核状态 }}</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" data-testid="open-detail" @click="openDetail(record.id)">
              查看项目
            </button>
            <button
              v-if="record.status !== '已复核'"
              class="link"
              type="button"
              :data-testid="`complete-${record.id}`"
              @click="completeReview(record.id)"
            >
              完成复核
            </button>
          </td>
        </tr>
        <tr v-if="!reviewRecords.length">
          <td colspan="8" class="empty-state">暂无复核项目，执行治理报告的「执行优化」后自动投递</td>
        </tr>
      </tbody>
    </table>

    <div v-if="detail" class="modal-backdrop" data-testid="review-detail-modal" @click.self="detail = null">
      <div class="modal detail-modal">
        <h3>复核项目 {{ detail.record.复核编号 }}</h3>
        <dl class="detail-grid">
          <dt>目标湖表</dt>
          <dd>{{ detail.record.目标湖表 }}</dd>
          <dt>输入版本</dt>
          <dd>
            <span v-for="version in detail.record.输入版本" :key="version" class="version-tag">{{ version }}</span>
          </dd>
          <dt>起始断点</dt>
          <dd>
            <span v-for="point in detail.record.起始断点" :key="point" class="breakpoint-tag">{{ point }}</span>
          </dd>
          <dt>涉及转换节点</dt>
          <dd>{{ detail.record.涉及转换节点.join('、') }}</dd>
          <dt>处置批次</dt>
          <dd>{{ detail.record.处置批次 }}</dd>
          <dt>合并次数</dt>
          <dd>{{ detail.record.合并次数 ?? 0 }}</dd>
        </dl>
        <h4>需要回看的可达路径</h4>
        <ol class="path-list">
          <li v-for="path in detail.record.关联路径" :key="path" class="path-item">{{ path }}</li>
        </ol>
        <h4>受影响湖表视图对应行</h4>
        <ul class="linked-view-list">
          <li v-for="row in detail.linked_view_rows" :key="row.id">
            {{ row.转换节点 }} → {{ row.受影响湖表 }}（{{ row.可达路径数 }} 条路径，最近核对 {{ row.最近核对时间 }}）
          </li>
        </ul>
        <div class="page-actions">
          <button class="btn" type="button" @click="detail = null">关闭</button>
          <button
            v-if="detail.record.status !== '已复核'"
            class="btn primary"
            type="button"
            data-testid="modal-complete"
            @click="completeReview(detail.record.id)"
          >
            完成复核
          </button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>受影响视图 {{ view.total }} 行 · 复核记录 {{ reviewTotal }} 条</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  loadAffectedView,
  loadReviewRecordDetail,
  loadReviewRecords,
  rebuildAffectedView,
  runReviewAction,
  type AffectedViewResult,
  type ReviewRecord,
  type ReviewRecordDetail,
} from '@/api/backend-service'

const emptyView: AffectedViewResult = {
  total: 0,
  affected_table_count: 0,
  breakpoints: [],
  affected_tables: [],
  items: [],
}

const view = ref<AffectedViewResult>(emptyView)
const reviewRecords = ref<ReviewRecord[]>([])
const reviewTotal = ref(0)
const detail = ref<ReviewRecordDetail | null>(null)
const errorMessage = ref('')

async function reload() {
  errorMessage.value = ''
  try {
    const [viewResult, reviewResult] = await Promise.all([
      loadAffectedView(),
      loadReviewRecords(),
    ])
    view.value = viewResult
    reviewRecords.value = reviewResult.items
    reviewTotal.value = reviewResult.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '断链影响数据读取失败'
  }
}

async function rebuildView() {
  errorMessage.value = ''
  try {
    const result = await rebuildAffectedView()
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '视图刷新失败'
  }
}

async function openDetail(id: number) {
  errorMessage.value = ''
  try {
    detail.value = await loadReviewRecordDetail(id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核项目详情读取失败'
  }
}

async function completeReview(id: number) {
  errorMessage.value = ''
  try {
    const result = await runReviewAction(id, '完成复核')
    if (!result.ok) throw new Error(result.message)
    await reload()
    if (detail.value?.record.id === id && result.entry) {
      detail.value = { ...detail.value, record: result.entry as ReviewRecord }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核完成操作失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.impact-page { display: flex; flex-direction: column; gap: 12px; }
.section-title { margin: 8px 0 0; font-size: 15px; }
.breakpoint-line { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 0; }
.breakpoint-tag,
.version-tag {
  display: inline-block;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 10px;
  background: #f8fafc;
  font-size: 12px;
}
.breakpoint-tag { color: #b91c1c; border-color: #fecaca; background: #fef2f2; }
.path-list { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; }
.path-list.compact .path-item { font-size: 12px; }
.path-item { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.version-cell { font-size: 12px; white-space: nowrap; }
.status-pill {
  display: inline-block;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  background: #f1f5f9;
}
.status-pill[data-status='已复核'] { background: #dcfce7; color: #166534; }
.status-pill[data-status='待复核'] { background: #fef9c3; color: #854d0e; }
.status-pill[data-status='复核中'] { background: #e0f2fe; color: #075985; }
.detail-modal { width: min(720px, 100%); }
.detail-grid {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 8px 12px;
  margin: 12px 0;
  font-size: 13px;
}
.detail-grid dt { color: #64748b; }
.detail-grid dd { margin: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.linked-view-list { margin: 0; padding-left: 18px; font-size: 13px; display: flex; flex-direction: column; gap: 4px; }
</style>
