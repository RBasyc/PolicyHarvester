<script setup lang="ts">
import AppIcon from './AppIcon.vue'
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <div class="logo">
        <AppIcon name="wheat" :size="20" />
      </div>
      <div class="titles">
        <p class="product">PolicyHarvester</p>
        <p class="subtitle">国务院政策抓取 · 大模型结构化提取 Demo</p>
      </div>
    </div>
    <div class="actions">
      <div class="status-tag">
        <AppIcon name="loader-circle" :size="12" />
        <span>运行中</span>
      </div>
      <button class="start-btn" type="button">
        <AppIcon name="play" :size="14" />
        <span>开始抓取</span>
      </button>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 32px;
  background: var(--card);
  border-bottom: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--primary);
  border-radius: 9px;
  color: #ffffff;
}

.titles {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.product {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: normal;
}

.subtitle {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: normal;
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  background: var(--blue-bg);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  color: var(--blue);
}

.start-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 16px;
  background: var(--accent);
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  color: #ffffff;
  line-height: normal;
}

@media (max-width: 640px) {
  .topbar {
    flex-wrap: wrap;
    gap: 12px;
    padding: 14px 16px;
  }

  .subtitle {
    display: none;
  }
}
</style>
