# PRODUCT OPPORTUNITY GUIDE

目标：把采集到的 `note/comment` 交给大模型完成全链路评估：
初筛 -> 匹配已有产品或新建产品 -> 输出完整产品描述 -> 多维打分。

## 1. 流程总览（全 LLM）

1. 初筛（Prescreen）
- 输入：`note + comments + thresholds`
- 输出：`decision=ignored|matched|created` + `prescreen_score`

2. 已有产品匹配（Match）
- 输入：当前 `product` 表（active）的摘要列表（`id/name/short_description`）
- 输出：若匹配则返回 `matched_product_id`

3. 新建产品（Create）
- 不匹配时，大模型返回：
  - `new_product.name`
  - `new_product.short_description`
  - `new_product.full_description`

4. 多维评分（Scoring）
- 评分对象是“产品”，不是单条 note
- 触发式执行，不再每轮都评分：
  - 新产品（`decision=created`）会做首次评分（`score_origin=initial_assessment`）
  - 已有关联产品（`decision=matched`）只有在满足以下任一条件才重评：
    - 该产品还没有 `product_assessment`
    - `linked_note_count >= generation_note_count * 2`（关联证据量达到上次定义基线 2 倍）
  - 未达阈值时直接复用历史评分快照（`score_origin=cached_assessment`）
- 触发重评时，评分输入使用该产品的“历史已映射 note/comment + 本批新增 note/comment”
- 产品级分数入库到 `product_assessment`
- `product_opportunity` 保留 note 到 product 的映射与评分快照，便于追溯
- `ignored` 仅计数，不写入 `product_opportunity`

5. 失败记录（Failure Zone）
- note 级失败会写入 `opportunity_note_failure`
- UI 的“失败 Note 专区”直接读取该表
- 升级后需执行一次 `python scripts/init_schema.py` 补齐表结构
- 对 `status=done` 的任务再次执行评估时，默认只重试失败表中的 note；已映射与已忽略 note 不会重复进入 LLM。
- 失败重试采用指数退避（由 `run_product_opportunity_cycle.py` 控制）：
  - `--retry-backoff-base-minutes`（默认 5）
  - `--retry-backoff-max-minutes`（默认 720）

6. Ignored 证据区（Ignored Zone）
- `ignored` 不进入 `product_opportunity`，会写入 `opportunity_note_ignored`
- 最小证据字段：
  - `task_id`
  - `note_id`
  - `prescreen_score`
  - `prescreen_threshold`
  - `reason`

## 2. 独立开发者导向原则

- 当前默认画像：个人独立开发者 + 算法工程师。
- 输出优先：小而深、可快速上线、低维护、可直接收费。
- 自动规避：重资质、重线下交付、重客服运维、重资金投入。
- 典型偏好：AI/LLM、自动化、数据处理、信息整合、工具型产品形态。

## 3. 评分维度（已落库）

评分范围全部是 1-5（`total_score` 为 0-100）：

- 个人开发友好度：
  - `development_difficulty`
  - `cold_start_cost`
  - `monetization_simplicity`
  - `maintenance_cost`
  - `vertical_focus`

- 价值维度：
  - `pain_severity`
  - `pain_investment`
  - `pain_frequency`
  - `pain_subjective`
  - `tam` / `sam` / `som`
  - `market_price`
  - `payment_habit`
  - `price_bandwidth`
  - `conversion`

- 竞争维度：
  - `competition_direct`
  - `competition_head`
  - `competition_entry`
  - `competition_satisfaction`
  - `competition_complaint`
  - `competition_unmet`
  - `substitute_free`
  - `substitute_offline`

- 自身优势维度：
  - `self_skill`
  - `self_channel`
  - `self_resource`
  - `diff_core`
  - `diff_moat`
  - `diff_perceived`
  - `mvp_speed`
  - `validation_cost`
  - `iteration_speed`

## 4. 配置项（.env）

```bash
OPPORTUNITY_LLM_PROVIDER=openai
OPPORTUNITY_LLM_API_KEY=sk-...
OPPORTUNITY_LLM_BASE_URL=https://api.openai.com/v1
OPPORTUNITY_LLM_MODEL=gpt-4.1-mini
OPPORTUNITY_LLM_TIMEOUT_SECONDS=600
OPPORTUNITY_LLM_TEMPERATURE=0.1
```

说明：
- 默认 provider 是 `openai`。
- `mock` 仅用于离线测试与 CI。

## 5. 使用方式（异步）

单任务运行：

```bash
python scripts/run_product_opportunity_cycle.py --task-id 123
```

批量运行（最近 done 任务）：

```bash
python scripts/run_product_opportunity_cycle.py --limit-tasks 20
```

自定义阈值：

```bash
python scripts/run_product_opportunity_cycle.py \
  --limit-tasks 50 \
  --prescreen-threshold 3.4 \
  --match-threshold 0.28 \
  --retry-backoff-base-minutes 5 \
  --retry-backoff-max-minutes 720
```

## 6. 输出结构（核心字段）

- `decision`: `ignored | matched | created`
- `matched_product_id`: 仅 `matched` 有值
- `new_product`: 仅 `created` 有值（含 name/short/full）
- `prescreen_score`
- `personal_fit_score`
- `value_score`
- `competition_opportunity_score`
- `self_control_score`
- `total_score`
- `dimensions`（全部细项）
- `evidence`（模型理由、样本片段）
  - `product_opportunity.evidence`：记录 note->product 的决策追踪（`decision_trace`）+ 产品级评分证据（`product_evidence`）
  - 不再存储 note 片段或 comment 样本，避免“按 note 打分”的误解

说明：
- 运行时 JSON 序列化使用 `ensure_ascii=False`，默认按中文直出。
- `product_assessment.evidence.product_lifecycle` 包含：
  - `linked_note_count`
  - `generation_note_count`
  - `next_regenerate_at_linked_notes`
  - `regenerated_this_round`
- 兼容策略：不向后兼容旧结构，不再读取 `product_assessment.evidence.generation_note_count`（root 级 legacy 字段）。
- 评分提示词包含分数校准规则，避免“普遍高分”。
