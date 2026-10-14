<script setup lang="ts">
/**
 * 单选下拉（成果库筛选用）：按钮 + 浮层列表，选项带条数。
 * 与 ConfigPanel 的多选下拉不同，这里选中即生效、再点一次已选项即清除。
 */
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import type { FacetOption } from '../api/types'

const props = defineProps<{
  modelValue: string
  options: FacetOption[]
  placeholder: string
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const open = ref(false)

const current = computed(
  () => props.options.find((o) => o.name === props.modelValue)?.name ?? '',
)

function choose(name: string) {
  emit('update:modelValue', name === props.modelValue ? '' : name)
  open.value = false
}
</script>

<template>
  <div class="select">
    <button
      class="select-btn"
      :class="{ chosen: current }"
      type="button"
      :title="current || placeholder"
      @click="open = !open"
    >
      <span class="select-text">{{ current || placeholder }}</span>
      <AppIcon name="chevron-down" :size="14" :class="{ flip: open }" />
    </button>
    <div v-if="open" class="backdrop" @click="open = false"></div>
    <Transition name="drop">
      <div v-if="open" class="menu" role="listbox">
        <button
          class="menu-item"
          :class="{ active: !current }"
          type="button"
          @click="choose('')"
        >
          <span class="menu-name">{{ placeholder }}</span>
        </button>
        <button
          v-for="option in options"
          :key="option.name"
          class="menu-item"
          :class="{ active: option.name === current }"
          type="button"
          :title="option.name"
          @click="choose(option.name)"
        >
          <span class="menu-name">{{ option.name }}</span>
          <span class="count">{{ option.count }}</span>
        </button>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.select {
  position: relative;
}

.select-btn {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  width: 100%;
  padding: 9px 12px;
  background: var(--bg);
  border: none;
  border-radius: 8px;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

/* 多机构联合发布名称极长（如「发展改革委 工业和信息化部 …」九机关连排），
   按钮文本一律省略号截断，宽度由外层（工具栏）固定，选中不再撑开换行 */
.select-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.select-btn:hover {
  color: var(--text-primary);
}

.select-btn.chosen {
  color: var(--text-primary);
  font-weight: 500;
}

.select-btn svg {
  transition: transform 0.15s ease;
}

.select-btn svg.flip {
  transform: rotate(180deg);
}

.backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
}

.menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 41;
  min-width: 100%;
  max-width: 320px;
  max-height: 320px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  padding: 6px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 8px 24px #0000001f;
  transform-origin: top left;
}

/* 下拉浮层动画：淡入 + 自上向下浮现 + 微缩放 */
.drop-enter-active,
.drop-leave-active {
  transition:
    opacity 0.18s ease,
    transform 0.18s cubic-bezier(0.3, 0.9, 0.4, 1);
}

.drop-enter-from,
.drop-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.98);
}

.menu-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 10px;
  background: none;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: normal;
  text-align: left;
}

.menu-item:hover {
  background: var(--bg);
  color: var(--text-primary);
}

.menu-item.active {
  color: var(--accent);
  font-weight: 500;
}

/* 选项名称同样省略号截断（完整名称悬浮 title 可见），条数始终可见不被挤掉 */
.menu-item > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.menu-name {
  min-width: 0;
}

.count {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
