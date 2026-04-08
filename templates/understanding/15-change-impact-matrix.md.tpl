---
title: 变更影响矩阵
taskId: ${task_id}
module: ${module_path}
status: draft
updatedAt: ${updated_at}
sourceScope:
  - ${module_path}
confidence: medium
primaryEvidence: []
---

# 变更影响矩阵

## 使用说明

- 这一页不是“有哪些模块”，而是“实际改动面会波及什么”。
- 一行应对应一个真实的改动切口，例如“修改 SceneSessionRequest 字段语义”而不是“改会话系统”。
- 优先写：
  - 会改哪些代码锚点
  - 会影响哪些契约 / 路径 / 产物 / 消费方
  - 必须验证哪些回归项

## 影响矩阵

| 变更面 | 典型编辑位置 | 直接影响层 | 下游影响 | 必查契约 / 产物 | 建议回归 | 风险等级 | 代码证据 | 主要证据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
${matrix_rows}

## 高风险改动模板

${high_risk_sections}

## 当前最危险的三个改动面

${top_risk_items}
