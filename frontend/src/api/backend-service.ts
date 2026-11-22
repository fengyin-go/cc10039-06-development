import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export type AffectedViewRow = {
  id: number
  受影响湖表: string
  转换节点: string
  起始断点: string
  断裂血缘id: number
  断链上游源: string
  湖表已注册: boolean
  可达路径: string[]
  可达路径数: number
  路径血缘边: number[][]
  输入版本: string
  最近核对时间: string
  status: string
}

export type AffectedViewResult = {
  total: number
  affected_table_count: number
  breakpoints: string[]
  affected_tables: string[]
  items: AffectedViewRow[]
}

export type ReviewRecord = {
  id: number
  复核编号: string
  目标湖表: string
  起始断点: string[]
  关联路径: string[]
  关联路径数: number
  输入版本: string[]
  涉及转换节点: string[]
  关联血缘id: number[]
  处置批次: string
  来源报告id: number
  来源报告id列表?: number[]
  合并次数?: number
  status: string
  复核状态: string
  投递时间: string
  复核时间: string
}

export type ReviewRecordDetail = {
  record: ReviewRecord
  linked_view_rows: AffectedViewRow[]
}

export async function loadAffectedView(table = ''): Promise<AffectedViewResult> {
  const query = new URLSearchParams(table ? { table } : {}).toString()
  const response = await request(`/api/impact/affected-view?${query}`)
  if (!response.ok) throw new Error('受影响湖表视图读取失败')
  return await response.json()
}

export async function rebuildAffectedView(): Promise<ActionResult> {
  const response = await request('/api/impact/affected-view/rebuild', { method: 'POST' })
  return await response.json()
}

export async function loadReviewRecords(): Promise<{ total: number; items: ReviewRecord[] }> {
  const response = await request('/api/impact/review-records')
  if (!response.ok) throw new Error('复核记录读取失败')
  return await response.json()
}

export async function loadReviewRecordDetail(id: number): Promise<ReviewRecordDetail> {
  const response = await request(`/api/impact/review-records/${id}`)
  if (!response.ok) throw new Error('复核记录详情读取失败')
  return await response.json()
}

export async function runReviewAction(id: number, action: string): Promise<ActionResult> {
  const response = await request(`/api/impact/review-records/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}
