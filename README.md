# 04-changecast（变投影）

Changecast — 变更日志投影：源变更流 → 按序投影 → 乱序/晚到策略钩子 → 快照可查互证。

与 Feedstitch（多源 Feed 归并）、Batchgate（作业依赖编排）、Syncledger（双端对账）主链路不同。夹具全本地，不连活库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5400 |
| API | http://localhost:10400 |

后端测试：

```bash
cd backend && python -m pytest tests -q
```

## 主链

配置投影流 → 读取 CDC 夹具并按**该流选定的唯一应用序**投影 → 批次计数与快照互证。

## 种子投影流

- **member_profile**：会员档案变更（`member_id`，字段 `display_name` / `tier` / `region`）
- **store_archive**：门店档案变更（`store_id`，字段 `name` / `status` / `city`）
- **channel_attr**：渠道属性变更（`channel_code`，字段 `label` / `active`）

三条夹具都故意让文件里的到达顺序与 `seq` 不一致（低 `seq` 事件排在文件末尾）。

## 应用序（互斥策略）

每条流只能启用一种应用序，切换后丢弃另一套的中间态：

| 值 | 界面文案 |
| --- | --- |
| `by_arrival` | 按夹具到达顺序依次投影（保留文件顺序，绝不回退按 seq 重排） |
| `by_seq` | 按事件 seq 升序投影 |

一条批次只会写一种应用序（`project_batches.apply_order`），事件留痕逐条记录
`apply_order` / `arrival_index` / `applied_index`；核验会拒绝混序批次。

### 切换路径（有未清算缓冲/进行中批次时）

选择「先按旧策略清空或失败掉缓冲再切换，且清空过程单独留痕」：

1. `buffer_drain` 批次：**按旧应用序**清算全部 open 缓冲，独立批次 + 独立留痕；
2. 未完成的批次置 `failed_on_switch`，在 `policy_switches` 中记 `running_failed`；
3. 翻转该流应用序；
4. `policy_rebuild` 批次：**按新应用序**从全量夹具重建终态；
5. `policy_switches` 记录切换前后序、清算/失败数量、清算批次号与重建批次号。

全程不会在切换瞬间混用两套序：清算批次与重建批次各自只有一个 `apply_order`。

### 正常投影与缓冲

- 不带 `hold_late` 的投影会连同 open 缓冲一起按**当前应用序**清算，缓冲行标记
  `drained` 并写入 `drained_batch_id`（同一批次内不混序）。
- 带 `hold_late` 时，按到达序判定为晚到（`seq` 落后于已见最大 `seq`）的事件进入
  `ooo_buffer` 挂起，本批不应用，批次记 `held_events`。

## 页面

| 页面 | 用途 |
| --- | --- |
| 总览 `/` | 统计与各流当前应用序、缓冲数、最近批次 |
| 投影流 `/streams` | 查看/切换互斥应用序，查看策略切换留痕 |
| 投影台 `/run` | 按当前序跑一趟，可挂起晚到，跑完即做同源核验 |
| 投影快照 `/projections` | 当前投影行（载荷、applied_seq、墓碑） |
| 应用批次 `/batches` | 每批的应用序、源事件数、写入数、删除数、挂起数 |
| 事件留痕 `/events` | 每条事件的应用序、到达序、执行序、结果 |
| 缓冲区 `/buffer` | 缓冲事件状态与清算批次 |
| 应用序对照 `/compare` | 同一夹具两种序的只读终态对照 + 两批真实批次对照 |
| 同源核验 `/verify` | 计数自洽 / 留痕可重放快照 / 单批不混序 / 当前投影=最近快照 |

## 同源核验口径

- 批次计数自洽：源事件 = 写入 + 删除 + 跳过 + 挂起 + 失败
- 留痕条数与批次计数一致
- 单批未混用应用序（留痕 `apply_order` 全等于批次 `apply_order`）
- 执行序连续可重放（`applied_index` 恰为 1..N）
- 事件留痕可重放出该批投影快照
- 快照存活/删除数与批次一致
- 流级：当前投影 = 最近批次快照

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 后续题

`ooo_window` / `event_idempotency` / `tombstone_policy` / `watermark_rewind` / …
