-- Agent Demo 演示数据：与 web/src/data.ts 的 mock 保持一致，方便前端直接对接真实接口
-- 可重复执行（显式 ID + ON CONFLICT DO NOTHING）

-- ---------- 抓取任务（对应 data.ts 的抓取配置 + 进度面板） ----------

INSERT INTO crawl_task (id, source, content_type, date_from, date_to, schedule, keywords,
                        status, total_count, fetched_count, extracted_count, failed_count, stage,
                        started_at)
VALUES (1, '国务院政策文件库（gov.cn）', 'policy',
        '2025-06-01', '2025-06-08', 'manual',
        ARRAY['人工智能', '中小企业', '产业基金'],
        'running', 10, 10, 6, 1,
        '正在调用大模型提取：第 3/10 篇 ——《关于促进人工智能产业高质量发展的若干措施》',
        '2025-06-08 10:23:41+08')
ON CONFLICT (id) DO NOTHING;

-- ---------- 政策文件（对应 results 列表，前 7 篇） ----------

INSERT INTO policy_document (id, task_id, title, publish_date, source_name, source_url,
                             agency, doc_no, fetched_by, fetched_at)
VALUES
    (1, 1, '关于促进人工智能产业高质量发展的若干措施', '2025-06-05',
     '国务院政策文件库', 'https://www.gov.cn/zhengce/content/2025-06/05/content_1234567.htm',
     '国务院', '国发〔2025〕12 号', 'China-Central-Policy-MCP', '2025-06-08 10:23:47+08'),
    (2, 1, '关于进一步做好中小企业纾困工作的通知', '2025-06-04',
     '国务院政策文件库', 'https://www.gov.cn/zhengce/content/2025-06/04/content_1234566.htm',
     '工业和信息化部', NULL, 'China-Central-Policy-MCP', '2025-06-08 10:23:48+08'),
    (3, 1, '数字政府建设一体化政务服务实施方案', '2025-06-03',
     '国务院政策文件库', 'https://www.gov.cn/zhengce/content/2025-06/03/content_1234565.htm',
     '国务院办公厅', NULL, 'China-Central-Policy-MCP', '2025-06-08 10:23:48+08'),
    (4, 1, '关于加强数据要素市场培育的指导意见', '2025-06-02',
     '国务院政策文件库', 'https://www.gov.cn/zhengce/content/2025-06/02/content_1234564.htm',
     '国家数据局', NULL, 'China-Central-Policy-MCP', '2025-06-08 10:23:49+08'),
    (5, 1, '2025 年节能减排重点工作安排', '2025-06-01',
     '国务院政策文件库', 'https://www.gov.cn/zhengce/content/2025-06/01/content_1234563.htm',
     '国家发展改革委', NULL, 'China-Central-Policy-MCP', '2025-06-08 10:23:49+08'),
    (6, 1, '关于优化口岸营商环境促进跨境贸易便利化的公告', '2025-05-30',
     '国务院政策文件库', 'https://www.gov.cn/zhengce/content/2025-05/30/content_1234562.htm',
     '海关总署', NULL, 'China-Central-Policy-MCP', '2025-06-08 10:23:50+08'),
    (7, 1, '关于推进养老服务体系建设提质增效的意见', '2025-05-29',
     '国务院政策文件库', 'https://www.gov.cn/zhengce/content/2025-05/29/content_1234561.htm',
     '民政部', NULL, 'China-Central-Policy-MCP', '2025-06-08 10:23:50+08')
ON CONFLICT (id) DO NOTHING;

-- ---------- 提取结果（第 1 篇完整提取；第 3 篇提取中；第 6 篇失败；其余未提取） ----------

INSERT INTO extraction (id, policy_id, status, error, model,
                        info_summary, info_points,
                        knowledge_concepts, knowledge_fields, knowledge_extension, extracted_at)
VALUES
    (1, 1, 'extracted', NULL, 'Qwen3-235B-A22B',
     '文件部署推动人工智能与实体经济深度融合，从产业基金、创新先导区、人才引育、场景开放四个方面提出 16 条具体措施，并明确 2027 年阶段性目标与部门分工。',
     '[
        {"point": "设立产业基金，重点投向基础大模型与行业应用"},
        {"point": "先导区实行「一区一策」，先行先试监管沙盒机制"},
        {"point": "对中小企业智能化改造给予最高 30% 费用补贴"},
        {"point": "建立跨部门推进机制，每年发布产业发展报告"}
      ]'::jsonb,
     '[
        {"term": "国家人工智能产业投资基金", "desc": "政府引导基金，通过市场化运作支持 AI 初创企业与关键技术攻关。"},
        {"term": "监管沙盒", "desc": "在受控环境中测试创新业务，降低合规不确定性，常见于金融科技与 AI 治理。"}
      ]'::jsonb,
     ARRAY['产业政策', '人工智能', '财政补贴'],
     '与 2024 年《新一代人工智能发展规划》衔接，政策工具从「研发资助」转向「基金 + 场景」组合，释放产业化加速信号。',
     '2025-06-08 10:24:03+08'),
    (2, 3, 'extracting', NULL, NULL, NULL, '[]'::jsonb, '[]'::jsonb, '{}', NULL, NULL),
    (3, 6, 'failed', '模型调用超时（60s），已自动重试 2 次', 'Qwen3-235B-A22B',
     NULL, '[]'::jsonb, '[]'::jsonb, '{}', NULL, '2025-06-08 10:26:12+08')
ON CONFLICT (id) DO NOTHING;

UPDATE extraction SET retry_count = 2 WHERE id = 3;

-- ---------- 事实明细（第 1 篇，对应 facts） ----------

INSERT INTO fact_item (id, extraction_id, label, content, position)
VALUES
    (1, 1, '时间',       '2025 年 6 月 5 日印发，2025 年 9 月 1 日起施行', 1),
    (2, 1, '发文机构',   '国务院（国发〔2025〕12 号）',                    2),
    (3, 1, '涉及机构',   '工业和信息化部、财政部、科技部',                  3),
    (4, 1, '政策动作',   '设立 600 亿元国家人工智能产业投资基金',          4),
    (5, 1, '政策动作',   '建设 10 个国家级人工智能创新应用先导区',         5),
    (6, 1, '数字指标',   '到 2027 年核心产业规模突破 1.2 万亿元',          6),
    (7, 1, '数字指标',   '培育 100 家以上行业龙头企业',                    7),
    (8, 1, '数字指标',   '规上企业智能化改造覆盖率达 80%',                8)
ON CONFLICT (id) DO NOTHING;

-- ---------- 任务日志（对应 logs） ----------

INSERT INTO task_log (id, task_id, tone, message, created_at)
VALUES
    (1, 1, 'info',   '已连接 China-Central-Policy-MCP（抓取源：国务院政策文件库）', '2025-06-08 10:23:41+08'),
    (2, 1, 'info',   '抓取完成：共获取 10 篇政策原文',                        '2025-06-08 10:23:47+08'),
    (3, 1, 'success', '第 1 篇提取成功（事实 12 · 信息 5 · 知识 4）',           '2025-06-08 10:23:52+08'),
    (4, 1, 'success', '第 2 篇提取成功（事实 9 · 信息 4 · 知识 3）',            '2025-06-08 10:23:55+08'),
    (5, 1, 'active',  '正在提取第 3 篇 …',                                     '2025-06-08 10:24:01+08')
ON CONFLICT (id) DO NOTHING;

-- ---------- 序列对齐（允许后续 INSERT 不显式指定 id） ----------

SELECT setval('crawl_task_id_seq',      (SELECT MAX(id) FROM crawl_task));
SELECT setval('policy_document_id_seq', (SELECT MAX(id) FROM policy_document));
SELECT setval('extraction_id_seq',      (SELECT MAX(id) FROM extraction));
SELECT setval('fact_item_id_seq',       (SELECT MAX(id) FROM fact_item));
SELECT setval('task_log_id_seq',        (SELECT MAX(id) FROM task_log));
