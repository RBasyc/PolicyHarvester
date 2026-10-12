<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import {
  drawerMeta,
  facts,
  infoSummary,
  infoPoints,
  knowledgeConcepts,
  knowledgeFields,
  knowledgeExtension,
} from '../data'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="overlay" @click.self="emit('close')">
      <div class="mask"></div>
      <aside class="drawer" role="dialog" aria-modal="true">
        <header class="drawer-head">
          <div class="tag-row">
            <div class="tag-left">
              <span class="source-tag">{{ drawerMeta.source }}</span>
              <span class="status-tag">
                <AppIcon name="circle-check" :size="12" />
                已提取
              </span>
            </div>
            <button class="close-btn" type="button" aria-label="关闭" @click="emit('close')">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <h2 class="drawer-title">{{ drawerMeta.title }}</h2>
          <div class="meta-row">
            <span>{{ drawerMeta.agency }}</span>
            <span>{{ drawerMeta.date }}</span>
            <span>{{ drawerMeta.docNo }}</span>
          </div>
          <div class="action-row">
            <button type="button" class="origin-btn">
              <AppIcon name="external-link" :size="13" />
              查看原文（gov.cn）
            </button>
            <span class="fetched-by">{{ drawerMeta.fetchedBy }}</span>
          </div>
        </header>

        <div class="columns">
          <div class="column">
            <div class="col-head">
              <span class="col-tag facts">事实 Facts</span>
              <button type="button" class="copy-btn">
                <AppIcon name="copy" :size="12" />
                复制
              </button>
            </div>
            <div class="col-body facts-body">
              <div v-for="(item, i) in facts" :key="i" class="fact-item">
                <p class="fact-label">{{ item.label }}</p>
                <p class="fact-content">{{ item.content }}</p>
              </div>
            </div>
          </div>

          <div class="column">
            <div class="col-head">
              <span class="col-tag info">信息 Information</span>
              <button type="button" class="copy-btn">
                <AppIcon name="copy" :size="12" />
                复制
              </button>
            </div>
            <div class="col-body info-body">
              <p class="sec-label info-label">摘要</p>
              <p class="summary-text">{{ infoSummary }}</p>
              <p class="sec-label info-label">要点</p>
              <div v-for="(point, i) in infoPoints" :key="i" class="point-item">
                <span class="dot"></span>
                <p class="point-text">{{ point }}</p>
              </div>
            </div>
          </div>

          <div class="column">
            <div class="col-head">
              <span class="col-tag knowledge">知识 Knowledge</span>
              <button type="button" class="copy-btn">
                <AppIcon name="copy" :size="12" />
                复制
              </button>
            </div>
            <div class="col-body knowledge-body">
              <p class="sec-label knowledge-label">背景概念</p>
              <div v-for="(concept, i) in knowledgeConcepts" :key="i" class="concept-item">
                <p class="concept-term">{{ concept.term }}</p>
                <p class="concept-desc">{{ concept.desc }}</p>
              </div>
              <p class="sec-label knowledge-label">相关领域</p>
              <div class="field-tags">
                <span v-for="field in knowledgeFields" :key="field" class="field-tag">
                  {{ field }}
                </span>
              </div>
              <p class="sec-label knowledge-label">延伸解释</p>
              <p class="concept-desc">{{ knowledgeExtension }}</p>
            </div>
          </div>
        </div>

        <footer class="drawer-foot">
          <div class="foot-left">
            <span>{{ drawerMeta.model }}</span>
            <span>{{ drawerMeta.extractedAt }}</span>
          </div>
          <button type="button" class="reextract-btn">
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
  background: var(--green-bg);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  color: var(--green);
  line-height: normal;
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
}

.fetched-by {
  font-size: 12px;
  color: var(--text-tertiary);
}

.columns {
  display: flex;
  gap: 14px;
  padding: 20px 24px;
  align-items: flex-start;
  flex-shrink: 0;
}

.column {
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
}

.col-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  line-height: normal;
  white-space: nowrap;
}

.col-tag.facts {
  background: var(--blue-bg);
  color: var(--blue);
}

.col-tag.info {
  background: var(--green-bg);
  color: var(--green);
}

.col-tag.knowledge {
  background: var(--purple-bg);
  color: var(--purple);
}

.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 8px;
  background: none;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 400;
  color: var(--text-tertiary);
  line-height: normal;
}

.copy-btn:hover {
  background: #f1f2f4;
}

.col-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border-radius: 10px;
}

.facts-body {
  background: var(--blue-soft);
  border: 1px solid var(--blue-border);
}

.info-body {
  background: var(--green-soft);
  border: 1px solid var(--green-border);
}

.knowledge-body {
  background: var(--purple-soft);
  border: 1px solid var(--purple-border);
}

.fact-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.fact-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--blue);
  line-height: normal;
}

.fact-content {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-primary);
}

.sec-label {
  font-size: 11px;
  font-weight: 600;
  line-height: normal;
}

.info-label {
  color: var(--green);
}

.knowledge-label {
  color: var(--purple);
}

.summary-text {
  font-size: 13px;
  line-height: 21px;
  color: var(--text-primary);
}

.point-item {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.dot {
  width: 5px;
  height: 5px;
  border-radius: 999px;
  background: var(--green);
  margin-top: 8px;
  flex-shrink: 0;
}

.point-text {
  flex: 1 1 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-primary);
}

.concept-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.concept-term {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: normal;
}

.concept-desc {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
}

.field-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.field-tag {
  padding: 4px 10px;
  background: var(--card);
  border: 1px solid var(--purple-border);
  border-radius: 999px;
  font-size: 12px;
  color: var(--purple);
  line-height: normal;
  white-space: nowrap;
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

.reextract-btn:hover {
  background: var(--accent-soft);
}

@media (max-width: 1024px) {
  .columns {
    flex-direction: column;
  }

  .column {
    width: 100%;
  }
}
</style>
