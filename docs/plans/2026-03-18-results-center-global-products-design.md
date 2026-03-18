# Results Center Global Products Design

**Date:** 2026-03-18

## Background

`ui/app.py` 当前的 Results 页入口按 `task` 选择，但其中“产品评分榜”展示的是 `ProductAssessment` 的当前全局状态，而不是任务执行时快照。这会把两类问题混在一起：

- 任务诊断：这次 task 抓到了什么、映射成什么、失败在哪里
- 产品经营：当前产品池有哪些产品、累计证据多少、当前评分如何

## Design Goal

把 Results 拆成两个层次，保持信息边界清晰：

- `产品总览`
  - 面向长期产品池
  - 展示全局 `Product` / `ProductAssessment` / `ProductOpportunity` 聚合结果
  - 默认按当前产品评分和累计关联 note 展示
- `任务结果`
  - 保留当前 task 维度的漏斗、note 决策、失败排查
  - 明确标注其中产品分数是“当前产品态”，不是历史快照

## Scope

In scope:

- 新增全局产品结果查询函数
- 在 Results 下增加“产品总览 / 任务结果”双 tab
- 优化任务结果中产品区的文案，避免历史快照误解
- 为全局产品总览添加过滤、排序和详情视图

Out of scope:

- 不新增 `product_assessment_history`
- 不回填历史任务执行时的产品快照
- 不调整后端 opportunity 评分逻辑

## Selected Approach

采用“单页双视图”方案，而不是新开一级页面：

- 优点：改动小，沿用现有 Results 使用习惯
- 优点：任务排障与产品总览仍在同一语境下，切换成本低
- 优点：无需新增路由或持久化 schema
- 代价：Results 页内部结构会稍复杂，但仍可通过 helper 函数保持清晰

## Data Design

新增一个全局产品聚合 helper，按产品输出：

- 基本信息：`product_id` / `name` / `status` / `source_keyword`
- 当前产品态：`total_score` / `assessment_updated_at` / `assessment_evidence`
- 全局累计：`linked_notes` / `matched_notes` / `created_notes` / `last_opportunity_at`
- 生命周期：`generation_note_count` / `next_regenerate_at_linked_notes` / `regenerated_this_round`

其中：

- `linked_notes` 使用按 `note_id` 去重后的全局累计值，与后端评分逻辑保持一致
- `matched_notes` / `created_notes` 统计全局 decision 次数
- 产品详情展示当前 `ProductAssessment.evidence`

## UX Design

Results 页调整为：

1. `产品总览`
   - 顶部 6 个全局指标
   - 中部产品列表，支持状态筛选、关键词筛选、最低分过滤、Top N 展示
   - 底部单产品详情，展示简介、生命周期和评分证据
2. `任务结果`
   - 保留 task selector
   - 保留漏斗 / note 决策 / 失败专区
   - 将产品榜重命名为“任务涉及产品（当前产品态）”
   - 增加说明：这里展示的是当前产品状态，不是任务执行时快照

## Risks

- 用户可能仍然把任务结果里的产品分数理解为历史值
  - 通过 tab 命名和 caption 明确声明
- 全局产品总览会暴露重复 task 关联同一 note 的累计 decision 次数
  - `linked_notes` 做 note 去重，decision 次数保留原始流水，兼顾产品强度和处理量

## Validation

- helper 单测验证全局产品聚合和排序
- targeted pytest 验证现有 UI helper 不回归
- 手工通过 Streamlit 页面查看双 tab 是否可读
