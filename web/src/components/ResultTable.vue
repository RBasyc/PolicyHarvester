<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { results, type ExtractStatus } from '../data'

const emit = defineEmits<{ rowClick: [index: number] }>()

const statusMap: Record<
  ExtractStatus,
  { label: string; icon: string; class: string }
> = {
  extracted: { label: '已提取', icon: 'circle-check', class: 'ok' },
  extracting: { label: '提取中', icon: 'loader-circle', class: 'doing' },
  pending: { label: '未提取', icon: 'timer', class: 'todo' },
  failed: { label: '提取失败', icon: 'circle-x', class: 'fail' },
}
</script>

<template>
  <section class="result-card">
    <header class="result-head">
      <div class="head-left">
        <h2 class="head-title">抓取结果</h2>
        <span class="count-tag">共 10 篇</span>
      </div>
      <p class="summary">已提取 6 · 提取中 1 · 未提取 2 · 失败 1</p>
    </header>

    <div class="table-wrap">
      <div class="table-head">
        <div class="col title-col">政策标题</div>
        <div class="col date-col">发布日期</div>
        <div class="col link-col">原文链接</div>
        <div class="col status-col">提取状态</div>
        <div class="col expand-col"></div>
      </div>

      <div
        v-for="(row, i) in results"
        :key="row.title"
        class="table-row"
        :class="{ active: i === 0 }"
        role="button"
        tabindex="0"
        @click="emit('rowClick', i)"
        @keydown.enter="emit('rowClick', i)"
      >
        <div class="col title-col">
          <span class="row-title" :class="{ strong: i === 0 }">{{ row.title }}</span>
        </div>
        <div class="col date-col">
          <span class="row-date">{{ row.date }}</span>
        </div>
        <div class="col link-col">
          <a class="row-link" href="#" @click.stop>
            <AppIcon name="external-link" :size="12" />
            原文
          </a>
        </div>
        <div class="col status-col">
          <span class="status-group">
            <span class="status-pill" :class="statusMap[row.status].class">
              <AppIcon :name="statusMap[row.status].icon" :size="12" />
              {{ statusMap[row.status].label }}
            </span>
            <span v-if="row.status === 'failed'" class="retry">重试</span>
          </span>
        </div>
        <div class="col expand-col">
          <AppIcon name="chevron-right" :size="16" class="expand-icon" :class="{ open: i === 0 }" />
        </div>
      </div>
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
  min-width: 720px;
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

.table-row.active {
  background: #fbf1f2;
  border-left: 3px solid var(--accent);
  padding-left: 17px;
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
  width: 110px;
  flex-shrink: 0;
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

.row-title.strong {
  font-weight: 600;
}

.row-date {
  font-size: 13px;
  color: var(--text-secondary);
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

.retry {
  font-size: 12px;
  font-weight: 500;
  color: var(--accent);
  white-space: nowrap;
  cursor: pointer;
}

.expand-icon {
  color: var(--text-tertiary);
  transition: transform 0.2s;
}

.expand-icon.open {
  transform: rotate(90deg);
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
