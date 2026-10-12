export type ExtractStatus = 'extracted' | 'extracting' | 'pending' | 'failed'

export type LogTone = 'info' | 'success' | 'active'

export interface PolicyResult {
  title: string
  date: string
  status: ExtractStatus
}

export interface LogLine {
  time: string
  text: string
  tone: LogTone
}

export interface FactItem {
  label: string
  content: string
}

export const keywords = ['人工智能', '中小企业', '产业基金']

export const results: PolicyResult[] = [
  { title: '关于促进人工智能产业高质量发展的若干措施', date: '2025-06-05', status: 'extracted' },
  { title: '关于进一步做好中小企业纾困工作的通知', date: '2025-06-04', status: 'extracted' },
  { title: '数字政府建设一体化政务服务实施方案', date: '2025-06-03', status: 'extracting' },
  { title: '关于加强数据要素市场培育的指导意见', date: '2025-06-02', status: 'extracted' },
  { title: '2025 年节能减排重点工作安排', date: '2025-06-01', status: 'pending' },
  { title: '关于优化口岸营商环境促进跨境贸易便利化的公告', date: '2025-05-30', status: 'failed' },
  { title: '关于推进养老服务体系建设提质增效的意见', date: '2025-05-29', status: 'pending' },
]

export const logs: LogLine[] = [
  { time: '[10:23:41]', text: '已连接 China-Central-Policy-MCP（抓取源：国务院政策文件库）', tone: 'info' },
  { time: '[10:23:47]', text: '抓取完成：共获取 10 篇政策原文', tone: 'info' },
  { time: '[10:23:52]', text: '第 1 篇提取成功（事实 12 · 信息 5 · 知识 4）', tone: 'success' },
  { time: '[10:23:55]', text: '第 2 篇提取成功（事实 9 · 信息 4 · 知识 3）', tone: 'success' },
  { time: '[10:24:01]', text: '正在提取第 3 篇 …', tone: 'active' },
]

export const drawerMeta = {
  source: '国务院政策文件库',
  title: '关于促进人工智能产业高质量发展的若干措施',
  agency: '发文机关：国务院',
  date: '发布日期：2025-06-05',
  docNo: '文号：国发〔2025〕12 号',
  fetchedBy: '由 China-Central-Policy-MCP 抓取',
  model: '提取模型：Qwen3-235B-A22B',
  extractedAt: '提取时间：2025-06-08 10:24:03',
}

export const facts: FactItem[] = [
  { label: '时间', content: '2025 年 6 月 5 日印发，2025 年 9 月 1 日起施行' },
  { label: '发文机构', content: '国务院（国发〔2025〕12 号）' },
  { label: '涉及机构', content: '工业和信息化部、财政部、科技部' },
  { label: '政策动作', content: '设立 600 亿元国家人工智能产业投资基金' },
  { label: '政策动作', content: '建设 10 个国家级人工智能创新应用先导区' },
  { label: '数字指标', content: '到 2027 年核心产业规模突破 1.2 万亿元' },
  { label: '数字指标', content: '培育 100 家以上行业龙头企业' },
  { label: '数字指标', content: '规上企业智能化改造覆盖率达 80%' },
]

export const infoSummary =
  '文件部署推动人工智能与实体经济深度融合，从产业基金、创新先导区、人才引育、场景开放四个方面提出 16 条具体措施，并明确 2027 年阶段性目标与部门分工。'

export const infoPoints = [
  '设立产业基金，重点投向基础大模型与行业应用',
  '先导区实行「一区一策」，先行先试监管沙盒机制',
  '对中小企业智能化改造给予最高 30% 费用补贴',
  '建立跨部门推进机制，每年发布产业发展报告',
]

export const knowledgeConcepts = [
  {
    term: '国家人工智能产业投资基金',
    desc: '政府引导基金，通过市场化运作支持 AI 初创企业与关键技术攻关。',
  },
  {
    term: '监管沙盒',
    desc: '在受控环境中测试创新业务，降低合规不确定性，常见于金融科技与 AI 治理。',
  },
]

export const knowledgeFields = ['产业政策', '人工智能', '财政补贴']

export const knowledgeExtension =
  '与 2024 年《新一代人工智能发展规划》衔接，政策工具从「研发资助」转向「基金 + 场景」组合，释放产业化加速信号。'
