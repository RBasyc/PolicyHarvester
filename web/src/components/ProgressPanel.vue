<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { logs } from '../data'
</script>

<template>
  <section class="card progress">
    <header class="progress-head">
      <div class="head-left">
        <h2 class="head-title">抓取进度</h2>
        <div class="status-tag">
          <AppIcon name="loader-circle" :size="12" />
          <span>运行中</span>
        </div>
      </div>
      <p class="start-time">开始于 2025-06-08 10:23:41</p>
    </header>

    <div class="progress-line">
      <div class="track">
        <div class="fill" style="width: 60%"></div>
      </div>
      <span class="percent">60%</span>
    </div>

    <div class="stage">
      <AppIcon name="sparkles" :size="15" class="stage-icon" />
      <p class="stage-text">正在调用大模型提取：第 3/10 篇 ——《关于促进人工智能产业高质量发展的若干措施》</p>
    </div>

    <p class="stats">已抓取 10/10 篇 · 已提取 6/10 篇 · 失败 0 · 预计剩余约 2 分钟</p>

    <div class="log-stream">
      <div v-for="(line, i) in logs" :key="i" class="log-line">
        <span class="log-time">{{ line.time }}</span>
        <span class="log-text" :class="`tone-${line.tone}`">{{ line.text }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}

.progress {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
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

.status-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: var(--blue-bg);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  color: var(--blue);
  line-height: normal;
}

.start-time {
  font-size: 12px;
  color: var(--text-tertiary);
}

.progress-line {
  display: flex;
  align-items: center;
  gap: 12px;
}

.track {
  flex: 1 1 0;
  height: 8px;
  background: #edeff2;
  border-radius: 999px;
  overflow: hidden;
}

.fill {
  height: 8px;
  background: var(--accent);
  border-radius: 999px;
}

.percent {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: normal;
}

.stage {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stage-icon {
  color: var(--accent);
  flex-shrink: 0;
}

.stage-text {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  line-height: normal;
}

.stats {
  font-size: 12px;
  color: var(--text-tertiary);
}

.log-stream {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 16px;
  background: var(--log-bg);
  border-radius: 10px;
  overflow-x: auto;
}

.log-line {
  display: flex;
  gap: 10px;
  width: fit-content;
  min-width: 100%;
}

.log-time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--log-time);
  white-space: nowrap;
}

.log-text {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--log-text);
  white-space: nowrap;
}

.tone-success {
  color: var(--log-success);
}

.tone-active {
  color: #ffffff;
}

@media (max-width: 640px) {
  .progress-head {
    flex-wrap: wrap;
    gap: 8px;
  }

  .stage-text {
    white-space: normal;
    line-height: 1.5;
  }
}
</style>
