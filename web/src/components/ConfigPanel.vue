<script setup lang="ts">
import { ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { keywords } from '../data'

const collapsed = ref(false)
</script>

<template>
  <section class="card config">
    <header class="config-head">
      <div class="head-left">
        <AppIcon name="settings-2" :size="16" class="head-icon" />
        <h2 class="head-title">抓取配置</h2>
        <p class="head-desc">通过表单设置，或从 JSON 配置文件导入</p>
      </div>
      <button class="collapse" type="button" @click="collapsed = !collapsed">
        <span>{{ collapsed ? '展开' : '收起' }}</span>
        <AppIcon :name="collapsed ? 'chevron-down' : 'chevron-up'" :size="16" />
      </button>
    </header>

    <template v-if="!collapsed">
      <div class="form-row">
        <div class="field grow">
          <p class="label">抓取源</p>
          <div class="select">
            <span class="select-value">国务院政策文件库（gov.cn）</span>
            <AppIcon name="chevron-down" :size="16" class="select-arrow" />
          </div>
        </div>
        <div class="field grow">
          <p class="label">内容类型</p>
          <div class="segmented">
            <button type="button" class="segment active">政策文件</button>
            <button type="button" class="segment">通知公告</button>
            <button type="button" class="segment">全部</button>
          </div>
        </div>
      </div>

      <div class="form-row">
        <div class="field grow">
          <p class="label">时间范围</p>
          <div class="chips-row">
            <div class="date-box">
              <AppIcon name="calendar" :size="14" class="date-icon" />
              <span>2025-06-01</span>
            </div>
            <span class="to">至</span>
            <div class="date-box">
              <AppIcon name="calendar" :size="14" class="date-icon" />
              <span>2025-06-08</span>
            </div>
            <button type="button" class="chip active">近 7 天</button>
            <button type="button" class="chip">近 30 天</button>
          </div>
        </div>
        <div class="field grow">
          <p class="label">抓取周期</p>
          <div class="chips-row">
            <button type="button" class="chip active">手动</button>
            <button type="button" class="chip">每天</button>
            <button type="button" class="chip">每周</button>
            <span class="demo-hint">Demo 仅展示，不执行定时任务</span>
          </div>
        </div>
      </div>

      <div class="field">
        <p class="label">关键词（可添加多个）</p>
        <div class="tag-input">
          <span v-for="kw in keywords" :key="kw" class="tag">
            {{ kw }}
            <AppIcon name="x" :size="12" class="tag-close" />
          </span>
          <span class="placeholder">输入关键词，回车添加</span>
        </div>
      </div>

      <div class="divider"></div>

      <div class="config-foot">
        <button type="button" class="import-btn">
          <AppIcon name="upload" :size="14" />
          <span>导入配置（JSON）</span>
        </button>
        <p class="import-hint">支持 .json 文件，导入后将覆盖当前表单配置</p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}

.config {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.config-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.head-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.head-icon {
  color: var(--primary);
}

.head-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.head-desc {
  font-size: 12px;
  color: var(--text-tertiary);
}

.collapse {
  display: flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  padding: 0;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: normal;
}

.form-row {
  display: flex;
  gap: 16px;
  align-items: flex-end;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.grow {
  flex: 1 1 0;
}

.label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
}

.select {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.select-value {
  font-size: 14px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.select-arrow {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.segmented {
  display: flex;
  gap: 2px;
  padding: 3px;
  background: #f1f2f4;
  border-radius: 8px;
  width: fit-content;
}

.segment {
  padding: 7px 16px;
  border: none;
  border-radius: 6px;
  background: transparent;
  font-size: 13px;
  font-weight: 400;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.segment.active {
  background: #ffffff;
  box-shadow: 0 1px 3px #0000001a;
  font-weight: 500;
  color: var(--text-primary);
}

.chips-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.date-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  color: var(--text-primary);
  white-space: nowrap;
}

.date-icon {
  color: var(--text-tertiary);
}

.to {
  font-size: 13px;
  color: var(--text-tertiary);
}

.chip {
  padding: 8px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 400;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.chip.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  font-weight: 500;
  color: var(--accent);
}

.demo-hint {
  padding: 4px 8px;
  background: #f1f2f4;
  border-radius: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
  line-height: normal;
  white-space: nowrap;
}

.tag-input {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.tag {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  background: #f1f2f4;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-primary);
  line-height: normal;
}

.tag-close {
  color: var(--text-tertiary);
  cursor: pointer;
}

.placeholder {
  font-size: 13px;
  color: var(--text-tertiary);
}

.divider {
  height: 1px;
  background: var(--divider);
}

.config-foot {
  display: flex;
  align-items: center;
  gap: 12px;
}

.import-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--primary);
  line-height: normal;
  white-space: nowrap;
}

.import-hint {
  font-size: 12px;
  color: var(--text-tertiary);
}

@media (max-width: 1024px) {
  .form-row {
    flex-direction: column;
    align-items: stretch;
  }

  .head-desc {
    display: none;
  }
}
</style>
