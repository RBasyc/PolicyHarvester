<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from './AppIcon.vue'
import SelectMenu from './SelectMenu.vue'
import type { FacetOption } from '../api/types'
import {
  currentSource,
  facets,
  facetsLoading,
  form,
  ingestMode,
  isBrowserSource,
  loadFacets,
  loadPipelineConfig,
  pipelineConfig,
  resetPipelineOverrides,
  selectableSources,
  setSource,
  sourceFilterNote,
  sourceSupportsDocTypes,
} from '../store'

const router = useRouter()

const collapsed = ref(false)
const draftKeyword = ref('')

/** 高级设置（流水线参数，预填 .env 默认、可按任务覆盖）默认收起 */
const advancedOpen = ref(false)

// ---------- 抓取来源（Phase 15） ----------

/** 来源下拉的选项：只有一个会命中 label 的名单，条数没有意义（SelectMenu 的 showCount 关掉）。 */
const sourceOptions = computed<FacetOption[]>(() =>
  selectableSources.value.map((item) => ({ name: item.label, count: 0 })),
)

/**
 * 来源一行的说明文案。优先说「没验收过」这件事——它是会让抓取空跑的真问题；
 * 其次是后端给的**口径说明**（配置化来源的「发布机构 = 站点栏目名」）。
 */
const sourceHint = computed(() => {
  const selected = currentSource.value
  if (selected && !selected.builtin && !selected.verified) {
    return `「${selected.label}」没有通过验收的试运行记录：抓取可能取不到内容，建议到「来源管理」里重跑一次接入。`
  }
  return (
    facets.value.supports.note ??
    '不同来源的筛选项口径不同：切换来源会清空已选的发布机构与文种，并按新来源重拉名单。'
  )
})

/** 展开时顺手重拉配置：后端热读 .env（PIPELINE_MODELS 改完即生效，无需重启），
 *  每次展开都能看到最新模型列表；选中项已不在列表里时 store 会把它吸回默认。 */
function toggleAdvanced() {
  advancedOpen.value = !advancedOpen.value
  if (advancedOpen.value) void loadPipelineConfig()
}

/** 当前展开的下拉（发布机构 / 文种）；null = 两个都收起 */
const openMenu = ref<'agency' | 'doc_type' | null>(null)

/** 折叠动画时长（须与 `.collapse-wrap` 的 transition 一致）：动画期间才让配置体裁剪内容 */
const COLLAPSE_MS = 300
const collapseAnimating = ref(false)
let collapseTimer: number | undefined

function toggleCollapse() {
  collapsed.value = !collapsed.value
  // 折叠体收起时浮层会被 overflow 裁剪，状态一并复位避免残留
  if (collapsed.value) openMenu.value = null
  // 裁剪只在**动画进行中**与**已收起**时开（见 `.collapse-wrap` 的 overflow 规则）：
  // 常驻 overflow:hidden 会把绝对定位的浮层也一起裁掉——「高级设置」收起后卡片变矮，
  // 发布机构 / 文种的下拉就在卡片底边被切掉一半。动画一结束立刻放开裁剪。
  collapseAnimating.value = true
  window.clearTimeout(collapseTimer)
  collapseTimer = window.setTimeout(() => {
    collapseAnimating.value = false
  }, COLLAPSE_MS + 60)
}

function toggleMenu(which: 'agency' | 'doc_type') {
  openMenu.value = openMenu.value === which ? null : which
}

/** 点击下拉以外的任何地方就收起；多选下拉不因选中某项而关闭 */
function onDocMouseDown(event: MouseEvent) {
  if (!(event.target as HTMLElement).closest('.select-wrap')) openMenu.value = null
}

onMounted(() => document.addEventListener('mousedown', onDocMouseDown))
onUnmounted(() => {
  document.removeEventListener('mousedown', onDocMouseDown)
  window.clearTimeout(collapseTimer)
})

type RangePreset = 'all' | 'week' | 'month' | 'year'

const rangePreset = computed<RangePreset>(() => {
  if (!form.date_from && !form.date_to) return 'all'
  const days = daysBetween(form.date_from, new Date())
  if (days === 6 || days === 7) return 'week'
  if (days === 29 || days === 30) return 'month'
  if (days >= 360 && days <= 366) return 'year'
  return 'all'
})

/** 折叠态标题旁的筛选摘要（如「工信部 · 国办发」），让用户不用展开也能看到已选条件 */
const summary = computed(() => {
  const bits: string[] = []
  for (const a of form.agencies) bits.push(a.length > 6 ? a.slice(0, 5) + '…' : a)
  bits.push(...form.doc_types)
  return bits.slice(0, 3).join(' · ') + (bits.length > 3 ? ` 等${bits.length}项` : '')
})

/** 已选机构（order 保持 facets 顺序，避免跳动） */
const selectedAgencies = computed(() => {
  const order = new Set(facets.value.agencies.map((a) => a.name))
  return [...form.agencies.filter((a) => order.has(a)), ...form.agencies.filter((a) => !order.has(a))]
})

const selectedDocTypes = computed(() => form.doc_types)

/**
 * 下拉头部的「当前口径」说明：条数显示删掉后，列表有没有随条件收窄肉眼看不出来了，
 * 这里用一行汇总（按什么条件 · 共几项）让「实时更新」保持可见。
 */
const facetScope = computed(() => {
  const bits: string[] = []
  if (form.keywords.length) bits.push(`关键词「${form.keywords[0]}」`)
  if (form.date_from || form.date_to) bits.push(`${form.date_from ?? '…'} ~ ${form.date_to ?? '…'}`)
  return bits.join(' · ')
})

function daysBetween(from: string | null, to: Date): number {
  if (!from) return -1
  const start = new Date(`${from}T00:00:00`)
  return Math.round((to.getTime() - start.getTime()) / 86_400_000)
}

function isoDate(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}

function applyPreset(preset: RangePreset) {
  if (preset === 'all') {
    form.date_from = null
    form.date_to = null
    void loadFacets()
    return
  }
  const days = preset === 'week' ? 6 : preset === 'month' ? 29 : 364
  const today = new Date()
  const from = new Date(today.getTime() - days * 86_400_000)
  form.date_from = isoDate(from)
  form.date_to = isoDate(today)
  void loadFacets()
}

function addKeyword() {
  const word = draftKeyword.value.trim()
  if (!word) return
  if (!form.keywords.includes(word)) form.keywords.push(word)
  draftKeyword.value = ''
  void loadFacets()
}

function removeKeyword(word: string) {
  form.keywords = form.keywords.filter((item) => item !== word)
  void loadFacets()
}

function toggleAgency(name: string) {
  const i = form.agencies.indexOf(name)
  if (i >= 0) form.agencies.splice(i, 1)
  else form.agencies.push(name)
}

function toggleDocType(name: string) {
  const i = form.doc_types.indexOf(name)
  if (i >= 0) form.doc_types.splice(i, 1)
  else form.doc_types.push(name)
}

/** 高级设置的五个流水线参数（键与后端 `pipeline_*` 列一一对应）。 */
const PIPELINE_KEYS = [
  'pipeline_max_policies',
  'pipeline_per_keyword_limit',
  'pipeline_extract_concurrency',
  'pipeline_max_body_chars',
  'pipeline_mcp_timeout',
] as const

/** 提取模型下拉框选项（后端 .env PIPELINE_MODELS 注册表；第一项 = 默认模型）。 */
const modelOptions = computed(() => pipelineConfig.value?.models ?? [])

/** 数字输入框 → 表单字段：清空 / 非法输入记为 null（= 该项用服务端 .env 默认）。 */
function setPipelineValue(key: (typeof PIPELINE_KEYS)[number], event: Event) {
  const raw = (event.target as HTMLInputElement).value.trim()
  const value = Number(raw)
  form[key] = raw !== '' && Number.isFinite(value) && value > 0 ? value : null
}
</script>

<template>
  <section class="card config">
    <header class="config-head">
      <div class="head-left">
        <AppIcon name="settings-2" :size="16" class="head-icon" />
        <h2 class="head-title">抓取配置</h2>
        <p class="head-desc">设置时间范围、关键词与筛选条件</p>
        <span v-if="summary" class="head-summary">{{ summary }}</span>
      </div>
      <button class="collapse" type="button" @click="toggleCollapse">
        <span>{{ collapsed ? '展开' : '收起' }}</span>
        <AppIcon name="chevron-down" :size="16" :class="{ flip: !collapsed }" />
      </button>
    </header>

    <!-- 整个配置体：grid-rows 0fr↔1fr 折叠动画（内容高度不固定，无法用 max-height 预设） -->
    <div class="collapse-wrap" :class="{ closed: collapsed, animating: collapseAnimating }">
      <div class="config-body">
      <!-- 抓取来源：决定后面所有筛选项的口径，所以排在最前 -->
      <div class="field">
        <p class="label">抓取来源</p>
        <div class="source-row">
          <!-- :key 让收起面板时重挂一次：浮层（含它的全屏 backdrop）会被 overflow:hidden 裁掉，
               不重挂就会留下一个看不见却吃掉点击的遮罩（与上面 openMenu 复位同一个理由） -->
          <SelectMenu
            :key="collapsed ? 'collapsed' : 'open'"
            :model-value="form.source"
            :options="sourceOptions"
            placeholder="选择抓取来源"
            :show-count="false"
            :clearable="false"
            @update:model-value="setSource"
          />
          <button
            class="chip source-import"
            type="button"
            title="接入一个新网站（勘察 → 生成配置 → 试运行验收）"
            @click="router.push({ name: 'sources', query: { tab: 'import' } })"
          >
            <AppIcon name="plus" :size="12" />
            <span>导入网址</span>
          </button>
        </div>
        <div class="hint-row">
          <AppIcon name="info" :size="12" class="hint-icon" />
          <p class="hint-text">{{ sourceHint }}</p>
        </div>
        <!-- 筛选口径第二行：配置化来源的「关键词/时间范围在哪一层生效」必须说清楚，
             否则用户会把「筛过了」当成「筛全了」（口径来自后端，见 store.sourceFilterNote） -->
        <div v-if="sourceFilterNote" class="hint-row">
          <AppIcon name="info" :size="12" class="hint-icon" />
          <p class="hint-text">{{ sourceFilterNote }}</p>
        </div>
      </div>

      <!-- 任意网站（无头浏览器）：用「入口网址 + 自然语言目标」取代站点检索；
           机构 / 文种这类站点筛选对它没有意义，下面的整行筛选项会隐藏 -->
      <div v-if="isBrowserSource" class="field browse-field">
        <p class="label">入口网址（必填）</p>
        <input
          v-model.trim="form.browse_url"
          type="url"
          class="browse-input"
          placeholder="https://example.com/policy/"
          autocomplete="off"
          spellcheck="false"
        />
        <p class="label">浏览目标（可选）</p>
        <textarea
          v-model="form.browse_goal"
          class="browse-textarea"
          rows="2"
          maxlength="500"
          placeholder="例如：找最近三个月与新能源相关的政策原文，收集 10 条"
        ></textarea>
        <div class="hint-row">
          <AppIcon name="info" :size="12" class="hint-icon" />
          <p class="hint-text">
            无头浏览器会打开入口页并按目标浏览；仅公网站点，遵守 robots.txt，只读（不登录、不提交表单、不下载）。
            关键词与时间范围仍作为筛选条件。
          </p>
        </div>
      </div>

      <div class="divider"></div>

    <div class="field">
        <p class="label">时间范围</p>
        <div class="chips-row">
          <div class="date-box">
            <AppIcon name="calendar" :size="14" class="date-icon" />
            <input v-model="form.date_from" type="date" class="date-input" aria-label="开始日期" @change="loadFacets" />
          </div>
          <span class="to">至</span>
          <div class="date-box">
            <AppIcon name="calendar" :size="14" class="date-icon" />
            <input v-model="form.date_to" type="date" class="date-input" aria-label="结束日期" @change="loadFacets" />
          </div>
          <button
            type="button"
            class="chip"
            :class="{ active: rangePreset === 'all' }"
            @click="applyPreset('all')"
          >
            不限
          </button>
          <button
            type="button"
            class="chip"
            :class="{ active: rangePreset === 'week' }"
            @click="applyPreset('week')"
          >
            近 7 天
          </button>
          <button
            type="button"
            class="chip"
            :class="{ active: rangePreset === 'month' }"
            @click="applyPreset('month')"
          >
            近 30 天
          </button>
          <button
            type="button"
            class="chip"
            :class="{ active: rangePreset === 'year' }"
            @click="applyPreset('year')"
          >
            近一年
          </button>
        </div>
      </div>

      <div class="field">
        <p class="label">关键词（可选，可添加多个）</p>
        <div class="tag-input">
          <span v-for="kw in form.keywords" :key="kw" class="tag">
            {{ kw }}
            <AppIcon name="x" :size="12" class="tag-close" @click="removeKeyword(kw)" />
          </span>
          <input
            v-model="draftKeyword"
            class="tag-field"
            type="text"
            placeholder="输入关键词，回车添加；留空则按下方筛选条件抓取最新发文"
            @keydown.enter.prevent="addKeyword"
          />
        </div>
      </div>

      <div v-if="!isBrowserSource" class="filter-row">
        <div class="form-row">
          <div class="field grow">
            <p class="label">发布机构</p>
            <div class="select-wrap">
              <button
                type="button"
                class="select"
                :class="{ open: openMenu === 'agency' }"
                :aria-expanded="openMenu === 'agency'"
                @click="toggleMenu('agency')"
              >
                <span class="select-value">
                  <AppIcon name="building-2" :size="14" class="select-icon" />
                  <span class="select-text">
                    {{ form.agencies.length ? `已选 ${form.agencies.length} 项` : '全部发布机构' }}
                  </span>
                </span>
                <AppIcon name="chevron-down" :size="16" class="select-arrow" />
              </button>
              <Transition name="drop">
                <div v-if="openMenu === 'agency'" class="menu">
                  <p v-if="facetsLoading" class="menu-hint">加载中…</p>
                  <p v-else-if="!facets.agencies.length" class="menu-hint">没有相关内容</p>
                  <template v-else>
                    <p class="menu-meta">
                      {{ facetScope ? `${facetScope} · 共 ${facets.agencies.length} 项` : `全部 · 共 ${facets.agencies.length} 项` }}
                    </p>
                    <button
                      v-for="opt in facets.agencies"
                      :key="opt.name"
                      type="button"
                      class="menu-item"
                      :class="{ active: form.agencies.includes(opt.name) }"
                      @click="toggleAgency(opt.name)"
                    >
                      <span class="menu-name">{{ opt.name }}</span>
                      <AppIcon
                        v-if="form.agencies.includes(opt.name)"
                        name="check"
                        :size="14"
                        class="menu-check"
                      />
                    </button>
                  </template>
                  <p v-if="facets.source === 'fallback'" class="menu-foot">
                    实时名单暂不可用，已回退内置名单
                  </p>
                </div>
              </Transition>
            </div>
            <div v-if="selectedAgencies.length" class="chips-row">
              <span v-for="a in selectedAgencies" :key="a" class="tag accent">
                {{ a }}
                <AppIcon name="x" :size="12" class="tag-close" @click="toggleAgency(a)" />
              </span>
            </div>
          </div>
          <!-- 配置化来源没有「文种」这个口径：留着它只会让人筛出一个静默的空结果 -->
          <div v-if="sourceSupportsDocTypes" class="field grow">
            <p class="label">文种</p>
            <div class="select-wrap">
              <button
                type="button"
                class="select"
                :class="{ open: openMenu === 'doc_type' }"
                :aria-expanded="openMenu === 'doc_type'"
                @click="toggleMenu('doc_type')"
              >
                <span class="select-value">
                  <AppIcon name="file-text" :size="14" class="select-icon" />
                  <span class="select-text">
                    {{ form.doc_types.length ? `已选 ${form.doc_types.length} 项` : '全部文种' }}
                  </span>
                </span>
                <AppIcon name="chevron-down" :size="16" class="select-arrow" />
              </button>
              <Transition name="drop">
                <div v-if="openMenu === 'doc_type'" class="menu">
                  <p v-if="facetsLoading" class="menu-hint">加载中…</p>
                  <p v-else-if="!facets.doc_types.length" class="menu-hint">没有相关内容</p>
                  <template v-else>
                    <p class="menu-meta">
                      {{ facetScope ? `${facetScope} · 共 ${facets.doc_types.length} 项` : `全部 · 共 ${facets.doc_types.length} 项` }}
                    </p>
                    <button
                      v-for="opt in facets.doc_types"
                      :key="opt.name"
                      type="button"
                      class="menu-item"
                      :class="{ active: form.doc_types.includes(opt.name) }"
                      @click="toggleDocType(opt.name)"
                    >
                      <span class="menu-name">{{ opt.name }}</span>
                      <AppIcon
                        v-if="form.doc_types.includes(opt.name)"
                        name="check"
                        :size="14"
                        class="menu-check"
                      />
                    </button>
                  </template>
                  <p v-if="facets.source === 'fallback'" class="menu-foot">
                    实时名单暂不可用，已回退内置名单
                  </p>
                </div>
              </Transition>
            </div>
            <div v-if="selectedDocTypes.length" class="chips-row">
              <span v-for="d in selectedDocTypes" :key="d" class="tag accent">
                {{ d }}
                <AppIcon name="x" :size="12" class="tag-close" @click="toggleDocType(d)" />
              </span>
            </div>
          </div>
        </div>
        <div class="hint-row">
          <AppIcon name="info" :size="12" class="hint-icon" />
          <p class="hint-text">
            关键词回车提交后，选项将按检索结果实时收窄（下拉顶部显示当前口径）；无匹配时提示「没有相关内容」；留空可直接浏览全部机构{{ sourceSupportsDocTypes ? '与文种' : '' }}
          </p>
        </div>
      </div>

      <div class="divider"></div>

      <!-- 入库方式：一次性 = 顶栏「开始抓取」立即跑；定时 = 打开弹窗配置调度参数，之后自动触发。
           浏览器来源需要每轮提供入口网址，定时任务暂不支持（后端也会拒绝）。 -->
      <div v-if="!isBrowserSource" class="field">
        <p class="label">入库方式</p>
        <div class="chips-row">
          <button
            type="button"
            class="chip"
            :class="{ active: ingestMode === 'once' }"
            @click="ingestMode = 'once'"
          >
            一次性入库
          </button>
          <button
            type="button"
            class="chip"
            :class="{ active: ingestMode === 'schedule' }"
            @click="ingestMode = 'schedule'"
          >
            定时自动入库
          </button>
        </div>
        <div v-if="ingestMode === 'schedule'" class="hint-row">
          <AppIcon name="info" :size="12" class="hint-icon" />
          <p class="hint-text">
            点顶栏「定时入库」配置运行周期：到达触发时间自动检索并全流程入库（无需人工确认），
            已入库的原文不会重复抓取
          </p>
        </div>
      </div>

      <!-- 高级设置：流水线参数，预填 .env 默认值，可按任务覆盖，默认收起 -->
      <div class="advanced">
        <button
          type="button"
          class="advanced-toggle"
          :aria-expanded="advancedOpen"
          @click="toggleAdvanced"
        >
          <span class="advanced-title">高级设置</span>
          <span class="advanced-hint">抓取 / 提取流水线参数（预填 .env 默认值，可按任务覆盖）</span>
          <AppIcon name="chevron-down" :size="16" class="advanced-arrow" :class="{ flip: advancedOpen }" />
        </button>
        <!-- 折叠体常驻 DOM，grid-rows 0fr↔1fr 做高度动画（高度随内容自适应，不用写死 max-height） -->
        <div class="advanced-body-wrap" :class="{ closed: !advancedOpen }">
          <div class="advanced-body">
          <div class="advanced-row">
            <code class="advanced-env">PIPELINE_MODEL</code>
            <span class="advanced-label">提取用大模型（结构化提取时调用）</span>
            <select v-model="form.pipeline_model" class="advanced-input advanced-select">
              <!-- 后端未启动 / 配置拉取失败时的兜底显示；正常情况下列表来自 .env 注册表 -->
              <option v-if="!modelOptions.length" :value="null">默认（服务端 .env）</option>
              <option v-for="opt in modelOptions" :key="opt.id" :value="opt.id">
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div class="advanced-row">
            <code class="advanced-env">PIPELINE_MAX_POLICIES</code>
            <span class="advanced-label">单次任务最多处理篇数</span>
            <input
              class="advanced-input"
              type="number"
              min="1"
              :value="form.pipeline_max_policies ?? ''"
              @input="setPipelineValue('pipeline_max_policies', $event)"
            />
          </div>
          <div class="advanced-row">
            <code class="advanced-env">PIPELINE_PER_KEYWORD_LIMIT</code>
            <span class="advanced-label">每个关键词向 MCP 请求条数</span>
            <input
              class="advanced-input"
              type="number"
              min="1"
              :value="form.pipeline_per_keyword_limit ?? ''"
              @input="setPipelineValue('pipeline_per_keyword_limit', $event)"
            />
          </div>
          <div class="advanced-row">
            <code class="advanced-env">PIPELINE_EXTRACT_CONCURRENCY</code>
            <span class="advanced-label">并发提取篇数（1 = 顺序）</span>
            <input
              class="advanced-input"
              type="number"
              min="1"
              :value="form.pipeline_extract_concurrency ?? ''"
              @input="setPipelineValue('pipeline_extract_concurrency', $event)"
            />
          </div>
          <div class="advanced-row">
            <code class="advanced-env">PIPELINE_MAX_BODY_CHARS</code>
            <span class="advanced-label">送进大模型的正文上限（字）</span>
            <input
              class="advanced-input"
              type="number"
              min="500"
              :value="form.pipeline_max_body_chars ?? ''"
              @input="setPipelineValue('pipeline_max_body_chars', $event)"
            />
          </div>
          <div class="advanced-row">
            <code class="advanced-env">PIPELINE_MCP_TIMEOUT</code>
            <span class="advanced-label">单次 MCP 调用超时（秒）</span>
            <input
              class="advanced-input"
              type="number"
              min="1"
              :value="form.pipeline_mcp_timeout ?? ''"
              @input="setPipelineValue('pipeline_mcp_timeout', $event)"
            />
          </div>
          <div class="advanced-foot">
            <p class="advanced-note">
              {{ pipelineConfig
                  ? '仅对下一次抓取任务生效；留空或填非法值时该项用服务端默认'
                  : '默认值暂不可用（后端未启动？），可直接手动填写' }}
            </p>
            <button
              type="button"
              class="advanced-reset"
              :disabled="!pipelineConfig"
              @click="resetPipelineOverrides"
            >
              恢复默认
            </button>
          </div>
          </div>
        </div>
      </div>
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
  min-width: 0;
}

.head-icon {
  color: var(--primary);
  flex-shrink: 0;
}

.head-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.head-desc {
  font-size: 12px;
  color: var(--text-tertiary);
}

.head-summary {
  font-size: 12px;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 3px 8px;
  border-radius: 999px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 40ch;
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

.collapse svg {
  transition: transform 0.2s ease;
}

.collapse svg.flip {
  transform: rotate(180deg);
}

/* 整个配置体的收起/展开动画：grid-rows 0fr↔1fr —— 高度跟随内容自适应，
   比写死 max-height 过渡更顺滑（不会收尾抖动）；margin-top 抵消 .config 的 gap，
   收起后卡片上下不留 16px 空档 */
.collapse-wrap {
  display: grid;
  grid-template-rows: 1fr;
  margin-top: 0;
  transition:
    grid-template-rows 0.3s cubic-bezier(0.3, 0.9, 0.4, 1),
    margin-top 0.3s cubic-bezier(0.3, 0.9, 0.4, 1);
}

.collapse-wrap.closed {
  grid-template-rows: 0fr;
  margin-top: -16px;
}

.collapse-wrap > .config-body {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 裁剪只在真正需要挡住内容时开：**已收起**（内容不该溢出卡片）与**折叠动画进行中**。
   常驻 overflow:hidden 会把绝对定位的浮层（发布机构 / 文种下拉、抓取来源下拉）一起裁掉——
   「高级设置」收起后卡片变矮，那两个下拉就在卡片底边被切成一半。动画结束由
   `collapseAnimating` 摘掉该类，浮层恢复可见。 */
.collapse-wrap.closed > .config-body,
.collapse-wrap.animating > .config-body {
  overflow: hidden;
}

.form-row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
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
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
}

.chips-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.filter-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 抓取来源一行：下拉占满剩余宽度，右侧跟一个「导入网址」入口 */
.source-row {
  display: flex;
  align-items: stretch;
  gap: 8px;
}

.source-row > :first-child {
  flex: 1 1 auto;
  min-width: 0;
}

/* SelectMenu 是按成果库工具栏（无边框、浅底）写的，这里对齐本卡片里有边框的输入框：
   只覆盖按钮外观，浮层与选中逻辑仍是那一份（改一处不会漂出第二个单选下拉） */
.source-row :deep(.select-btn) {
  padding: 10px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 14px;
  color: var(--text-primary);
}

.source-row :deep(.select-btn:hover) {
  border-color: var(--text-tertiary);
}

.source-import {
  display: flex;
  align-items: center;
  gap: 5px;
}

/* 「任意网站（无头浏览器）」的入口 / 目标输入框：与来源下拉、日期框同一观感 */
.browse-field {
  gap: 8px;
}

.browse-input,
.browse-textarea {
  width: 100%;
  padding: 10px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  font-size: 14px;
  color: var(--text-primary);
  outline: none;
}

.browse-textarea {
  min-height: 56px;
  resize: vertical;
  font-size: 13px;
  line-height: 1.5;
}

.browse-input:focus,
.browse-textarea:focus {
  border-color: var(--accent);
}

.browse-input::placeholder,
.browse-textarea::placeholder {
  color: var(--text-tertiary);
}

.select-wrap {
  position: relative;
}

.select {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  text-align: left;
  line-height: normal;
}

.select:hover {
  border-color: var(--text-tertiary);
}

.select.open {
  border-color: var(--accent);
}

.select-value {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.select-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.select-text {
  font-size: 14px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.select-arrow {
  color: var(--text-tertiary);
  flex-shrink: 0;
  transition: transform 0.15s;
}

.select.open .select-arrow {
  transform: rotate(180deg);
}

.menu {
  position: absolute;
  z-index: 20;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  max-height: 240px;
  overflow-y: auto;
  padding: 4px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
  transform-origin: top left;
}

/* 下拉浮层动画：淡入 + 自上向下浮现 + 微缩放（与 SelectMenu 一致） */
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
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  background: none;
  border: none;
  border-radius: 6px;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: left;
  line-height: normal;
}

/* 头部口径行：按什么条件过滤 · 共几项（条数显示删掉后，用它体现「实时更新」） */
.menu-meta {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 7px 10px 5px;
  background: var(--card);
  border-bottom: 1px solid var(--divider);
  font-size: 11px;
  color: var(--text-tertiary);
  line-height: normal;
}

.menu-item:hover {
  background: #f1f2f4;
}

.menu-item.active {
  background: var(--accent-soft);
  color: var(--accent);
}

.menu-name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.menu-check {
  flex-shrink: 0;
}

.menu-hint {
  padding: 14px 10px;
  font-size: 12px;
  color: var(--text-tertiary);
  text-align: center;
}

.menu-foot {
  padding: 6px 10px;
  border-top: 1px solid var(--divider);
  font-size: 11px;
  color: var(--text-tertiary);
}

.hint-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.hint-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.hint-text {
  font-size: 12px;
  color: var(--text-tertiary);
}

.date-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  white-space: nowrap;
}

.date-icon {
  color: var(--text-tertiary);
}

.date-input {
  width: 118px;
  border: none;
  outline: none;
  background: none;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-primary);
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

.tag.accent {
  background: var(--accent-soft);
  color: var(--accent);
}

.tag-close {
  color: var(--text-tertiary);
  cursor: pointer;
}

.tag.accent .tag-close {
  color: var(--accent);
}

.tag-field {
  flex: 1 1 140px;
  min-width: 140px;
  border: none;
  outline: none;
  background: none;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-primary);
}

.tag-field::placeholder {
  color: var(--text-tertiary);
}

.divider {
  height: 1px;
  background: var(--divider);
}

.advanced {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.advanced-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  background: none;
  border: none;
  font-family: inherit;
  text-align: left;
  line-height: normal;
}

.advanced-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  white-space: nowrap;
}

.advanced-hint {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-tertiary);
}

.advanced-arrow {
  flex-shrink: 0;
  color: var(--text-tertiary);
  transition: transform 0.2s ease;
}

.advanced-arrow.flip {
  transform: rotate(180deg);
}

/* 高级设置折叠动画：同样 grid-rows 0fr↔1fr；margin-top 抵消 .advanced 的 gap */
.advanced-body-wrap {
  display: grid;
  grid-template-rows: 1fr;
  margin-top: 0;
  transition:
    grid-template-rows 0.28s cubic-bezier(0.3, 0.9, 0.4, 1),
    margin-top 0.28s cubic-bezier(0.3, 0.9, 0.4, 1);
}

.advanced-body-wrap.closed {
  grid-template-rows: 0fr;
  margin-top: -10px;
}

.advanced-body-wrap > .advanced-body {
  min-height: 0;
  overflow: hidden;
}

.advanced-body {
  display: flex;
  flex-direction: column;
}

.advanced-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--divider);
}

.advanced-row:last-of-type {
  border-bottom: none;
}

.advanced-env {
  flex: 0 0 260px;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.advanced-label {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 13px;
  color: var(--text-secondary);
}

.advanced-input {
  flex: 0 0 96px;
  padding: 6px 10px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-primary);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.advanced-input:focus {
  outline: none;
  border-color: var(--accent);
}

/* 提取模型下拉框：与其他自定义下拉（抓取配置 .select / 成果库 SelectMenu）视觉对齐。
   原生 <select> 去掉浏览器默认外观（appearance:none），自绘 chevron 背景图；
   圆角 / 内边距 / 字号 / hover·focus 边框逐项对齐（原生弹层本身不可样式化，保留） */
.advanced-select {
  flex: 0 0 200px;
  padding: 8px 34px 8px 12px;
  appearance: none;
  -webkit-appearance: none;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
  border-radius: 8px;
  background-color: var(--card);
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%238f959e' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
}

.advanced-select:hover {
  border-color: var(--text-tertiary);
}

.advanced-select:focus {
  outline: none;
  border-color: var(--accent);
}

.advanced-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 10px;
}

.advanced-note {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 12px;
  color: var(--text-tertiary);
}

.advanced-reset {
  flex-shrink: 0;
  padding: 6px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.advanced-reset:hover:not(:disabled) {
  border-color: var(--text-tertiary);
}

.advanced-reset:disabled {
  opacity: 0.5;
  cursor: default;
}

@media (max-width: 1024px) {
  .form-row {
    flex-direction: column;
    align-items: stretch;
  }

  .head-desc {
    display: none;
  }

  .advanced-hint {
    display: none;
  }

  .advanced-env {
    flex-basis: 150px;
  }
}
</style>
