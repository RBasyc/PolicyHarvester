<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import ExtractionColumns from './ExtractionColumns.vue'
import { loadExtraction, notify, reextractPolicy } from '../store'
import type { ExtractStatus, PolicyExtractionResponse } from '../api/types'

const props = defineProps<{ open: boolean; policyId: number | null }>()
const emit = defineEmits<{ close: [] }>()

const detail = ref<PolicyExtractionResponse | null>(null)
const loading = ref(false)
const failed = ref(false)
const reextracting = ref(false)

const STATUS_LABEL: Record<ExtractStatus, string> = {
  pending: '未提取',
  extracting: '提取中',
  extracted: '已提取',
  failed: '提取失败',
}

const TONE: Record<ExtractStatus, string> = {
  pending: 'todo',
  extracting: 'doing',
  extracted: 'ok',
  failed: 'fail',
}

const status = computed<ExtractStatus>(() => detail.value?.extraction.status ?? 'pending')
const meta = computed(() => detail.value?.policy ?? null)
const extraction = computed(() => detail.value?.extraction ?? null)

/** 已提取但三区皆空 → 「暂无提取内容」 */
const emptyContent = computed(() => {
  const data = extraction.value
  if (!data || data.status !== 'extracted') return false
  return (
    !data.facts.length &&
    !data.info.summary &&
    !data.info.points.length &&
    !data.knowledge.concepts.length &&
    !data.knowledge.fields.length &&
    !data.knowledge.extension
  )
})

watch(
  () => [props.open, props.policyId] as const,
  ([open, policyId]) => {
    if (!open || policyId == null) return
    void fetchDetail(policyId)
  },
  { immediate: true },
)

function reload() {
  if (props.policyId != null) void fetchDetail(props.policyId)
}

async function fetchDetail(policyId: number) {
  loading.value = true
  failed.value = false
  detail.value = null
  try {
    detail.value = await loadExtraction(policyId)
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function onReextract() {
  if (props.policyId == null) return
  reextracting.value = true
  try {
    await reextractPolicy(props.policyId)
    notify('已提交重新提取，正在后台重新解析该篇')
    await fetchDetail(props.policyId)
  } catch (error) {
    notify(`重新提取失败：${error instanceof Error ? error.message : String(error)}`)
  } finally {
    reextracting.value = false
  }
}

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="overlay" @click.self="emit('close')">
      <div class="mask"></div>
      <aside class="drawer" role="dialog" aria-modal="true">
        <header class="drawer-head">
          <div class="tag-row">
            <div class="tag-left">
              <span class="source-tag">{{ meta?.source_name ?? '国务院政策文件库' }}</span>
              <span class="status-tag" :class="TONE[status]">
                <AppIcon
                  :name="status === 'failed' ? 'circle-x' : status === 'extracting' ? 'loader-circle' : 'circle-check'"
                  :size="12"
                  :class="{ spin: status === 'extracting' }"
                />
                {{ STATUS_LABEL[status] }}
              </span>
            </div>
            <button class="close-btn" type="button" aria-label="关闭" @click="emit('close')">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <h2 class="drawer-title">{{ meta?.title ?? '加载中…' }}</h2>
          <div class="meta-row">
            <span v-if="meta?.agency">发文机关：{{ meta.agency }}</span>
            <span v-if="meta?.publish_date">发布日期：{{ meta.publish_date }}</span>
            <span v-if="meta?.doc_no">文号：{{ meta.doc_no }}</span>
          </div>
          <div class="action-row">
            <a
              v-if="meta?.source_url"
              class="origin-btn"
              :href="meta.source_url"
              target="_blank"
              rel="noopener noreferrer"
            >
              <AppIcon name="external-link" :size="13" />
              查看原文（gov.cn）
            </a>
            <span v-else class="origin-btn disabled">
              <AppIcon name="external-link" :size="13" />
              暂无原文链接
            </span>
            <span v-if="meta?.fetched_by" class="fetched-by">
              由 {{ meta.fetched_by }} 抓取
            </span>
          </div>
        </header>

        <!-- 加载骨架屏 -->
        <div v-if="loading" class="skeleton-columns">
          <div v-for="i in 3" :key="`col-${i}`" class="skeleton-column">
            <div class="col-head"><span class="skeleton skeleton-tag"></span></div>
            <div class="skeleton-body">
              <span v-for="j in 3" :key="j" class="skeleton skeleton-line"></span>
            </div>
          </div>
        </div>

        <!-- 加载失败 -->
        <div v-else-if="failed" class="state-block">
          <AppIcon name="circle-x" :size="24" class="state-icon fail" />
          <p class="state-title">详情加载失败</p>
          <p class="state-desc">无法读取该篇的提取结果，请检查后端服务后重试</p>
          <button class="state-btn" type="button" @click="reload">
            <AppIcon name="refresh-ccw" :size="12" />
            重新加载
          </button>
        </div>

        <!-- 单篇提取失败 -->
        <div v-else-if="status === 'failed'" class="state-block">
          <AppIcon name="circle-x" :size="24" class="state-icon fail" />
          <p class="state-title">提取失败</p>
          <p class="state-desc">{{ extraction?.error ?? '未知原因' }}</p>
          <button class="state-btn" type="button" :disabled="reextracting" @click="onReextract">
            <AppIcon name="refresh-ccw" :size="12" />
            点击重试
          </button>
        </div>

        <!-- 提取中（重新提取后） -->
        <div v-else-if="status === 'pending' || status === 'extracting'" class="state-block">
          <AppIcon name="loader-circle" :size="24" class="state-icon spin" />
          <p class="state-title">{{ status === 'extracting' ? '正在提取…' : '尚未提取' }}</p>
          <p class="state-desc">
            {{
              status === 'extracting'
                ? '大模型正在解析该篇政策，提取完成后可重新打开查看'
                : '该篇还没有提取结果，可点击下方按钮立即提取'
            }}
          </p>
          <button
            v-if="status === 'pending'"
            class="state-btn"
            type="button"
            :disabled="reextracting"
            @click="onReextract"
          >
            <AppIcon name="sparkles" :size="12" />
            立即提取
          </button>
        </div>

        <!-- 已提取但三区皆空 -->
        <div v-else-if="emptyContent" class="state-block">
          <AppIcon name="inbox" :size="24" class="state-icon" />
          <p class="state-title">暂无提取内容</p>
          <p class="state-desc">该篇已提取完成，但三栏都没有内容，可尝试重新提取</p>
          <button class="state-btn" type="button" :disabled="reextracting" @click="onReextract">
            <AppIcon name="refresh-ccw" :size="12" />
            重新提取
          </button>
        </div>

        <!-- 三栏（共享组件） -->
        <div v-else class="drawer-columns">
          <ExtractionColumns :extraction="extraction!" />
        </div>

        <footer class="drawer-foot">
          <div class="foot-left">
            <span v-if="extraction?.model">提取模型：{{ extraction.model }}</span>
            <span v-if="extraction?.extracted_at">
              提取时间：{{ formatDateTime(extraction.extracted_at) }}
            </span>
            <span v-if="extraction?.retry_count">重试次数：{{ extraction.retry_count }}</span>
          </div>
          <button
            type="button"
            class="reextract-btn"
            :disabled="reextracting || status === 'extracting'"
            @click="onReextract"
          >
            <AppIcon name="refresh-ccw" :size="12" />
            重新提取
          </button>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
}

.mask {
  position: absolute;
  inset: 0;
  background: #1f232999;
}

.drawer {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 960px;
  max-width: 100%;
  background: var(--card);
  box-shadow: -8px 0 32px #0000002e;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.drawer-head {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px 24px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.tag-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.tag-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.source-tag {
  padding: 4px 10px;
  background: #f1f2f4;
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: normal;
}

.status-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  line-height: normal;
}

.status-tag.ok {
  background: var(--green-bg);
  color: var(--green);
}

.status-tag.doing {
  background: var(--blue-bg);
  color: var(--blue);
}

.status-tag.todo {
  background: #f1f2f4;
  color: var(--text-secondary);
}

.status-tag.fail {
  background: var(--red-bg);
  color: var(--accent);
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.close-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  border-radius: 8px;
  color: var(--text-secondary);
}

.close-btn:hover {
  background: #f1f2f4;
}

.drawer-title {
  font-size: 18px;
  line-height: 25px;
  font-weight: 600;
  color: var(--text-primary);
}

.meta-row {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--text-secondary);
}

.action-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.origin-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  background: var(--card);
  border: 1px solid var(--accent);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--accent);
  line-height: normal;
  white-space: nowrap;
  text-decoration: none;
}

.origin-btn.disabled {
  border-color: var(--border);
  color: var(--text-tertiary);
}

.fetched-by {
  font-size: 12px;
  color: var(--text-tertiary);
}

.drawer-columns {
  padding: 20px 24px;
  flex-shrink: 0;
}

.skeleton-columns {
  display: flex;
  gap: 14px;
  padding: 20px 24px;
  align-items: flex-start;
  flex-shrink: 0;
}

.skeleton-column {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.col-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 26px;
}

.skeleton-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border-radius: 10px;
  background: var(--bg);
}

.state-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 56px 24px;
  margin: auto;
}

.state-icon {
  color: var(--text-tertiary);
}

.state-icon.fail {
  color: var(--accent);
}

.state-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.state-desc {
  font-size: 13px;
  color: var(--text-tertiary);
  text-align: center;
  max-width: 420px;
  line-height: 1.6;
}

.state-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  padding: 8px 14px;
  background: var(--card);
  border: 1px solid var(--accent);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--accent);
  line-height: normal;
}

.state-btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.skeleton {
  display: inline-block;
  border-radius: 4px;
  background: linear-gradient(90deg, #e9ebee 25%, #f3f5f7 37%, #e9ebee 63%);
  background-size: 400% 100%;
  animation: shimmer 1.4s ease infinite;
}

.skeleton-tag {
  width: 96px;
  height: 26px;
  border-radius: 999px;
}

.skeleton-line {
  width: 100%;
  height: 12px;
}

@keyframes shimmer {
  0% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0 50%;
  }
}

.drawer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 24px;
  border-top: 1px solid var(--border);
  margin-top: auto;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.foot-left {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--text-tertiary);
}

.reextract-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  background: none;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--accent);
  line-height: normal;
}

.reextract-btn:disabled {
  color: var(--text-tertiary);
  cursor: default;
}

.reextract-btn:hover:not(:disabled) {
  background: var(--accent-soft);
}
</style>
