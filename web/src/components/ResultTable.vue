<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { booting, policies, refreshing, retryPolicy, summary, task } from '../store'
import type { ExtractStatus } from '../api/types'

const emit = defineEmits<{ rowClick: [policyId: number] }>()

const statusMap: Record<ExtractStatus, { label: string; icon: string; class: string }> = {
  extracted: { label: '已提取', icon: 'circle-check', class: 'ok' },
  extracting: { label: '提取中', icon: 'loader-circle', class: 'doing' },
  pending: { label: '未提取', icon: 'timer', class: 'todo' },
  failed: { label: '提取失败', icon: 'circle-x', class: 'fail' },
}

const loading = computed(() => booting.value || (refreshing.value && !policies.value.length))
const showEmpty = computed(() => !loading.value && !policies.value.length)

/** 空状态的三种成因：无任务 / 正在抓取 / 抓完了但一条都没有。 */
const emptyState = computed(() => {
  const current = task.value
  if (!current) {
    return {
      title: '还没有抓取结果',
      desc: '配置抓取参数后，点击右上角「开始抓取」获取政策原文',
    }
  }
  if (current.status === 'pending' || (current.status === 'running' && current.fetched_count === 0)) {
    return {
      title: '正在抓取政策列表…',
      desc: current.stage ?? '已连接抓取源，正在检索匹配的政策原文',
    }
  }
  return {
    title: '本次没有抓取到政策',
    desc: `${current.stage ?? '抓取源未返回结果'}，可调整关键词或时间范围后重新点击「开始抓取」`,
  }
})

const summaryText = computed(() => {
  const s = summary.value
  return `已提取 ${s.extracted} · 提取中 ${s.extracting} · 未提取 ${s.pending} · 失败 ${s.failed}`
})

const countText = computed(() => `共 ${summary.value.total} 篇`)

function onRowClick(policyId: number) {
  emit('rowClick', policyId)
}

async function onRetry(policyId: number) {
  await retryPolicy(policyId)
}
</script>

<template>
  <section class="result-card">
    <header class="result-head">
      <div class="head-left">
        <h2 class="head-title">抓取结果</h2>
        <span class="count-tag">{{ countText }}</span>
      </div>
      <p v-if="policies.length" class="summary">{{ summaryText }}</p>
    </header>

    <div class="table-wrap">
      <div class="table-head">
        <div class="col title-col">政策标题</div>
        <div class="col date-col">日期</div>
        <div class="col link-col">原文链接</div>
        <div class="col status-col">提取状态</div>
        <div class="col expand-col"></div>
      </div>

      <!-- 骨架屏 -->
      <template v-if="loading">
        <div v-for="i in 5" :key="`skeleton-${i}`" class="table-row skeleton-row">
          <div class="col title-col"><span class="skeleton skeleton-title"></span></div>
          <div class="col date-col"><span class="skeleton skeleton-date"></span></div>
          <div class="col link-col"><span class="skeleton skeleton-link"></span></div>
          <div class="col status-col"><span class="skeleton skeleton-pill"></span></div>
          <div class="col expand-col"></div>
        </div>
      </template>

      <!-- 空状态 -->
      <div v-else-if="showEmpty" class="empty">
        <AppIcon name="inbox" :size="28" class="empty-icon" />
        <p class="empty-title">{{ emptyState.title }}</p>
        <p class="empty-desc">{{ emptyState.desc }}</p>
      </div>

      <!-- 结果行 -->
      <template v-else>
        <div
          v-for="row in policies"
          :key="row.id"
          class="table-row"
          role="button"
          tabindex="0"
          @click="onRowClick(row.id)"
          @keydown.enter="onRowClick(row.id)"
        >
          <div class="col title-col">
            <span class="row-title">{{ row.title }}</span>
          </div>
          <div class="col date-col">
            <span
              class="row-date"
              :class="{ none: !row.publish_date && !row.signed_date, fallback: !row.publish_date && row.signed_date }"
            >
              {{ row.publish_date ?? row.signed_date ?? '无日期' }}
            </span>
            <span
              v-if="!row.publish_date && row.signed_date"
              class="date-signed"
              title="页面没有可解析的发布日期，此处显示原文落款/成文日期；它不参与时间范围筛选"
            >成文</span>
            <span
              v-else-if="row.publish_date_source === 'model'"
              class="date-model"
              title="页面结构未解析到发布日期，该日期由模型从页面内容提取，建议核对原文"
            >模型提取</span>
          </div>
          <div class="col link-col">
            <a
              v-if="row.source_url"
              class="row-link"
              :href="row.source_url"
              target="_blank"
              rel="noopener noreferrer"
              @click.stop
            >
              <AppIcon name="external-link" :size="12" />
              原文
            </a>
            <span v-else class="row-link muted">—</span>
          </div>
          <div class="col status-col">
            <span class="status-group">
              <span class="status-pill" :class="statusMap[row.status].class">
                <AppIcon
                  :name="statusMap[row.status].icon"
                  :size="12"
                  :class="{ spin: row.status === 'extracting' }"
                />
                {{ statusMap[row.status].label }}
              </span>
              <span
                v-if="row.status === 'failed'"
                class="retry"
                role="button"
                tabindex="0"
                @click.stop="onRetry(row.id)"
                @keydown.enter.stop="onRetry(row.id)"
              >
                重试
              </span>
            </span>
          </div>
          <div class="col expand-col">
            <AppIcon name="chevron-right" :size="16" class="expand-icon" />
          </div>
        </div>
      </template>
    </div>

    <footer class="result-foot">
      <AppIcon name="mouse-pointer-click" :size="13" class="foot-icon" />
      <p class="foot-text">点击任意一行，在右侧抽屉中查看「事实 / 信息 / 知识」三部分提取结果</p>
    </footer>
  </section>
</template>

<style scoped>
.result-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}

.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px 14px;
}

.head-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.head-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.count-tag {
  padding: 4px 10px;
  background: #f1f2f4;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
}

.summary {
  font-size: 12px;
  color: var(--text-tertiary);
}

.table-wrap {
  overflow-x: auto;
}

.table-head,
.table-row {
  display: flex;
  align-items: center;
  gap: 0;
  min-width: 760px;
}

.table-head {
  padding: 10px 20px;
  background: #f8f9fa;
  border-top: 1px solid var(--divider);
  border-bottom: 1px solid var(--divider);
}

.table-head .col {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-tertiary);
  line-height: normal;
}

.table-row {
  padding: 13px 20px;
  border-bottom: 1px solid var(--divider);
  cursor: pointer;
}

.table-row:hover {
  background: #fafbfc;
}

.skeleton-row {
  cursor: default;
}

.col {
  display: flex;
  align-items: center;
}

.title-col {
  flex: 1 1 0;
  min-width: 0;
}

.date-col {
  width: 150px;
  flex-shrink: 0;
  gap: 6px;
}

.link-col {
  width: 80px;
  flex-shrink: 0;
}

.status-col {
  width: 132px;
  flex-shrink: 0;
}

.expand-col {
  width: 28px;
  flex-shrink: 0;
}

.row-title {
  font-size: 14px;
  font-weight: 400;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-date {
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.row-date.none {
  color: var(--text-tertiary);
}

/* 没有发布日期、显示原文成文日期：弱化一级 */
.row-date.fallback {
  color: var(--text-tertiary);
}

.date-signed {
  padding: 1px 6px;
  border-radius: 999px;
  background: #eef1f4;
  color: var(--text-tertiary);
  font-size: 10px;
  line-height: 1.6;
  white-space: nowrap;
}

.date-model {
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--blue-bg);
  color: var(--blue);
  font-size: 10px;
  line-height: 1.6;
  white-space: nowrap;
}

.row-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--blue);
  text-decoration: none;
  white-space: nowrap;
}

.row-link.muted {
  color: var(--text-tertiary);
}

.status-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  line-height: normal;
  white-space: nowrap;
}

.status-pill.ok {
  background: var(--green-bg);
  color: var(--green);
}

.status-pill.doing {
  background: var(--blue-bg);
  color: var(--blue);
}

.status-pill.todo {
  background: #f1f2f4;
  color: var(--text-secondary);
}

.status-pill.fail {
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

.retry {
  font-size: 12px;
  font-weight: 500;
  color: var(--accent);
  white-space: nowrap;
  cursor: pointer;
}

.expand-icon {
  color: var(--text-tertiary);
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 44px 20px;
  border-bottom: 1px solid var(--divider);
}

.empty-icon {
  color: var(--text-tertiary);
}

.empty-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
}

.empty-desc {
  font-size: 12px;
  color: var(--text-tertiary);
  text-align: center;
  max-width: 460px;
  line-height: 1.6;
}

.skeleton {
  display: inline-block;
  height: 12px;
  border-radius: 4px;
  background: linear-gradient(90deg, #eef0f2 25%, #f6f7f9 37%, #eef0f2 63%);
  background-size: 400% 100%;
  animation: shimmer 1.4s ease infinite;
}

.skeleton-title {
  width: 70%;
}

.skeleton-date {
  width: 72px;
}

.skeleton-link {
  width: 40px;
}

.skeleton-pill {
  width: 68px;
  height: 20px;
  border-radius: 999px;
}

@keyframes shimmer {
  0% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0 50%;
  }
}

.result-foot {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 12px 20px;
}

.foot-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.foot-text {
  font-size: 12px;
  color: var(--text-tertiary);
}

@media (max-width: 640px) {
  .result-head {
    flex-wrap: wrap;
    gap: 8px;
  }

  .summary {
    display: none;
  }
}
</style>
