<script setup lang="ts">
/**
 * 「事实 / 信息 / 知识」三栏展示（含每栏复制按钮）。
 * 详情抽屉（DetailDrawer）与成果库详情页（LibraryDetailPage）共用。
 */
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { notify } from '../store'
import type { ExtractionOut } from '../api/types'

const props = defineProps<{ extraction: ExtractionOut }>()

async function copy(text: string) {
  if (!navigator.clipboard) {
    notify('当前环境不支持剪贴板，请手动选中复制')
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    notify('已复制到剪贴板')
  } catch {
    notify('复制失败，请手动选中复制')
  }
}

const factsText = computed(() =>
  props.extraction.facts.map((f) => `${f.label}：${f.content}`).join('\n'),
)
const infoText = computed(() =>
  [`摘要：${props.extraction.info.summary ?? ''}`, ...props.extraction.info.points.map((p) => `· ${p}`)].join(
    '\n',
  ),
)
const knowledgeText = computed(() =>
  [
    ...props.extraction.knowledge.concepts.map((c) => `${c.term}：${c.desc}`),
    `适用领域：${props.extraction.knowledge.fields.join('、')}`,
    `行动建议：${props.extraction.knowledge.extension ?? ''}`,
  ].join('\n'),
)
</script>

<template>
  <div class="columns">
    <div class="column">
      <div class="col-head">
        <span class="col-tag facts">
          <AppIcon name="list-checks" :size="13" />
          事实 Facts
        </span>
        <button type="button" class="copy-btn" @click="copy(factsText)">
          <AppIcon name="copy" :size="12" />
          复制
        </button>
      </div>
      <div class="col-body facts-body">
        <p v-if="!extraction.facts.length" class="col-empty">暂无事实条目</p>
        <div v-for="(item, i) in extraction.facts" :key="i" class="fact-item">
          <p class="fact-label">{{ item.label }}</p>
          <p class="fact-content">{{ item.content }}</p>
        </div>
      </div>
    </div>

    <div class="column">
      <div class="col-head">
        <span class="col-tag info">
          <AppIcon name="file-text" :size="13" />
          信息 Information
        </span>
        <button type="button" class="copy-btn" @click="copy(infoText)">
          <AppIcon name="copy" :size="12" />
          复制
        </button>
      </div>
      <div class="col-body info-body">
        <p class="sec-label info-label">摘要</p>
        <p class="summary-text">{{ extraction.info.summary ?? '—' }}</p>
        <p class="sec-label info-label">要点</p>
        <p v-if="!extraction.info.points.length" class="col-empty">暂无要点</p>
        <div v-for="(point, i) in extraction.info.points" :key="i" class="point-item">
          <span class="dot"></span>
          <p class="point-text">{{ point }}</p>
        </div>
      </div>
    </div>

    <div class="column">
      <div class="col-head">
        <span class="col-tag knowledge">
          <AppIcon name="lightbulb" :size="13" />
          知识 Knowledge
        </span>
        <button type="button" class="copy-btn" @click="copy(knowledgeText)">
          <AppIcon name="copy" :size="12" />
          复制
        </button>
      </div>
      <div class="col-body knowledge-body">
        <p class="sec-label knowledge-label">知识点</p>
        <p v-if="!extraction.knowledge.concepts.length" class="col-empty">暂无知识点</p>
        <div
          v-for="(concept, i) in extraction.knowledge.concepts"
          :key="i"
          class="concept-item"
        >
          <p class="concept-term">{{ concept.term }}</p>
          <p class="concept-desc">{{ concept.desc }}</p>
        </div>
        <p class="sec-label knowledge-label">适用领域</p>
        <div class="field-tags">
          <span v-for="field in extraction.knowledge.fields" :key="field" class="field-tag">
            {{ field }}
          </span>
          <span v-if="!extraction.knowledge.fields.length" class="col-empty">—</span>
        </div>
        <p class="sec-label knowledge-label">行动建议</p>
        <p class="concept-desc">{{ extraction.knowledge.extension ?? '—' }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.columns {
  display: flex;
  gap: 14px;
  align-items: flex-start;
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
  min-height: 26px;
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

.col-empty {
  font-size: 12px;
  color: var(--text-tertiary);
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

@media (max-width: 1024px) {
  .columns {
    flex-direction: column;
  }

  .column {
    width: 100%;
  }
}
</style>
