---
name: pingcode-bug-flow
description: 使用 PingCode OpenAPI 真实读取工作项（Bug/需求/任务），按“所属/项目”过滤并执行本地修复闭环；修复后先标记“AI已修复待验证”，待人工验证通过再更新为“已处理”等最终状态。适用于工作项混杂、需要区分项目归属且要追踪 AI 修复与人工验收状态的场景。
---

# PingCode Bug Flow

## Overview

先按项目归属筛选 bug，再修复，再标记“待验证”，最后人工验收后再改成最终完成状态。不要跳过“待验证”环节。

## Step 1: 配置鉴权

```bash
export PINGCODE_TOKEN='你的_openapi_token'
export PINGCODE_BASE_URL='https://open.pingcode.com'  # 私有化部署改成对应域名
```

可选：自定义本地台账路径。

```bash
export PINGCODE_TRACKER_FILE='/absolute/path/pending_verify.json'
```

## Step 2: 按所属筛选工作项

先看某个所属（项目/模块）下的工作项，避免杂糅。

```bash
python3 scripts/pingcode_bug.py list --belong 友电之星-移动端
```

可叠加状态过滤：

```bash
python3 scripts/pingcode_bug.py list --belong 友电之星-移动端 --status 待处理
```

## Step 3: 读取单条并修复

```bash
python3 scripts/pingcode_bug.py get --identifier YDZ-279
```

根据真实返回数据实施修复并验证代码行为。

## Step 4: 标记“已解决未验证”

修复完成但人工还没验收时，不直接标“已处理”。先执行（会在工单评论里加 `已解决未验证`，并把状态设为 `已修改`）：

```bash
python3 scripts/pingcode_bug.py mark-pending --identifier YDZ-279 --note '已完成修复，待你回归验证' --skip-local-track
```

默认动作：
- PingCode 评论区新增一条：`已解决未验证`（可叠加 note）
- 尝试把 PingCode 状态同步为 `已修改`（可用 `--sync-status` 改名，或传空跳过）
- 本地台账默认保留；如不需要本地台账可加 `--skip-local-track`

查看所有“AI已修复待验证”项：

```bash
python3 scripts/pingcode_bug.py pending-list
```

按所属过滤待验证项：

```bash
python3 scripts/pingcode_bug.py pending-list --belong 友电之星-移动端
```

## Step 5: 人工验证后收口

验证通过：

```bash
python3 scripts/pingcode_bug.py mark-verified --identifier YDZ-279 --passed --note '回归通过'
python3 scripts/pingcode_bug.py set-status --identifier YDZ-279 --status 已处理
```

验证不通过：

```bash
python3 scripts/pingcode_bug.py mark-verified --identifier YDZ-279 --note '仍有复现，退回继续修复'
```

## Commands

- `extract-identifier`: 从文本/链接提取编号（如 `YDZ-279`）
- `list`: 列出工作项并支持 `--belong`、`--status`
- `get`: 查询单个工作项详情
- `set-status`: 更新 PingCode 状态
- `mark-pending`: 标记 AI 已修复待验证（评论区标记 + 状态改已修改，台账可选）
- `pending-list`: 查询未验收台账
- `mark-verified`: 记录人工验收结果

## Resources

- `scripts/pingcode_bug.py`: 全流程脚本
- `references/pingcode-openapi.md`: PingCode OpenAPI 说明
