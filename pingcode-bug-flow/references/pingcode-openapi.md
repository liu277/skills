# PingCode OpenAPI Notes

脚本默认使用以下接口：

- `GET /open/v1/project/work_items`
- `GET /open/v1/project/work_items/{id}`
- `GET /v1/project/work_item_states?project_id=...`
- `PATCH /v1/project/work_items/{id}` with `{ "state_id": "..." }`
- `POST /v1/project/work_items/{id}/comments` with `{ "content": "..." }`

鉴权头：
- `Authorization: Bearer <TOKEN>`

## 环境变量

- `PINGCODE_TOKEN`: 必填
- `PINGCODE_BASE_URL`: 默认 `https://open.pingcode.com`
- `PINGCODE_TRACKER_FILE`: 本地待验证台账（可选）

## 推荐流程

1. `list --belong ... --status 待处理` 先按归属筛选。
2. `get --identifier ...` 获取真实数据。
3. 修复后执行 `mark-pending`，评论区自动追加“已解决未验证”，并将状态同步到“已修改”。
4. 验收通过后：`mark-verified --passed` + `set-status --status 已处理`。

## 常见错误

1. 401/403
- token 错误或无权限。
- base URL 不对（私有化域名）。

2. 找不到工作项
- 编号不在权限范围。
- 增大 `--max-pages`。

3. 状态更新失败
- 项目流程没有该状态名。
- 先查看报错里返回的可用状态名称，再改 `--status` / `--sync-status`。
