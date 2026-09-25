# 快照隔离与写冲突检测（从 0 实现）

仓库里只有本说明、`samples/**` 和 `.gitignore`；引擎与页面都从零新写。

## 1. 范围

交付物（仓库根目录）：`snapshotdb/` 内存存储引擎（库，`python -m snapshotdb` 是命令行入口）、
`web/timeline.html` 并发时间线页面（自包含，`file://` 可开）、标准库 `unittest` 测试。

```
python -m snapshotdb trace  <脚本文件> <轨迹文件>   # 跑一个脚本，写逐行轨迹
python -m snapshotdb report <页面文件>              # 全部样例内联进一个 HTML
```

`trace` 只跑给的那一个脚本；`report` 按文件名升序读 `samples/scripts/` 全部脚本。诊断走 stderr。

不做：持久化与恢复；多进程 / 多线程 / 网络；SQL 解析；范围扫描与二级索引；删除与墓碑；死锁检测与等待队列
（冲突当场中止，不排队、不重试）；非法脚本容错；读系统时钟、`sleep`、随机数、依赖线程调度；第三方库、CDN、
构建、联网；页面上的编辑。

## 2. 口径与公式

- **版本与提交号**：版本是 `(键, ver, 值, 写者)`，`ver` 是提交号，从 1 起严格递增；全局 `seq` 初始 0，每次
  成功提交自增一次并当该次的 `ver`，只读提交也占号但不安装版本。
- **快照**：`begin` 记 `snapshot = seq` 且不再变；读只看 `ver ≤ snapshot` 的版本，同一个键重复读结果一致，
  快照不随别人的提交改变。
- **读自己的写**：写先进私有写集（对别人不可见）；读自己写过的键，值取写集里的，版本号写 `-`。
- **写-写冲突**：写集里的键 k 存在 `ver > snapshot` 的版本；**读-写冲突**：读集里的键 k（含读到空、读到自
  己的写）存在 `ver > snapshot` 的版本。
- **判定顺序**：先按 key 升序扫写集判写-写，再按同样顺序扫读集判读-写；先命中的决定 `reason`
  （`write_write` / `read_write`）、触发键与 `with=`（该键上 `ver > snapshot` 中最小那版的写者）。写集为空
  的提交不校验。
- **回收水位**：`w = 活跃事务 snapshot 的最小值`，无活跃事务时 `w = seq`；每个键只留「`ver ≤ w` 中最大的那
  版（基线）」与所有 `ver > w` 的版本——水位以下只留基线、以上一条不落：长事务拖着就堆积，宁可堆积也不能
  收掉别人要读的版本。
- **回收时机**：每个事件处理完立刻回收；真回收了版本才写 `RECLAIM` 行（按键名升序）。
- **时间与调度**：没有线程与 `sleep`，不读系统时钟；`t` 与事件顺序全部来自脚本，同刻事件按脚本里的先后处
  理，提交号也按这个顺序分配。

## 3. 状态机与数据结构

事务从 `active` 起，落到 `committed` / `aborted` / `rolledback` 之一，终结后移出活跃集合：`committed` 分配
提交号并安装写集（只读不安装），`aborted` 与 `rolledback` 丢弃写集、不占提交号。

事务要有 `snapshot`、读集（键的集合）、写集（键 → 值）；引擎要有键 → 版本链（读在链上定位「≤ snapshot 的
最大版本」）、事务表、活跃快照集合（取最小值配 `seq` 定水位）、全局 `seq` 与保留版本总数。

## 4. 输入输出与文件格式

本节文件都是 UTF-8、纯 ASCII、单 `\n`、末行也有换行。

### 4.1 时序脚本 `samples/scripts/<名>.json`

顶层 `name`（等于文件名去 `.json`）与 `events`（按 `t` 非降序）：

- `t` 非负整数；`op` 取 `begin` / `read` / `write` / `commit` / `rollback`；
- `tx` 是唯一事务 id（`[A-Za-z][A-Za-z0-9_]*`，如 `T0`、`R1`），先用 `begin` 声明，只能终结一次；
- `read` / `write` 带 `key`（`[A-Za-z0-9_]+`），`write` 带整数 `value`（可为负）；
- 脚本保证合法：每个事务都有终结、收尾没有活跃事务。

### 4.2 轨迹 `samples/expected/<名>.txt`

一行一条，7 段用 `|` 分隔、空位写 `-`：`<时刻>|<事件>|<事务>|<键>|<版本>|<值>|<明细>`。

- `BEGIN` 明细 `snapshot=<n>`；
- `READ`：版本段是快照里能看见的那一版，读不到写 `0`、值段写 `null`；读自己的写时版本段写 `-`，值段写写
  集里的值；
- `WRITE` 值段写新值；
- `COMMIT` 明细 `ver=<n> wrote=<k,…> versions=<n>`，`wrote` 按键名升序，只读写 `-`；
- `ABORT`：键段是触发判定的键，明细 `reason=write_write|read_write with=<事务> versions=<n>`；
- `ROLLBACK` 明细 `reason=user versions=<n>`；
- `RECLAIM`：键段是被回收的键，明细 `watermark=<w> dropped=<ver,…> kept=<ver,…>`，列表按升序；
- `SUMMARY` 明细 `abort= begin= commit= reclaim= rollback= versions=<n>`，末行，`t` 取最后一个事件的 `t`。

`COMMIT`、`ABORT`、`ROLLBACK`、`SUMMARY` 的 `versions=` 都是回收之后的保留版本总数；事件行在前，`RECLAIM`
行紧跟其后。

### 4.3 页面 `web/timeline.html`

`report` 把全部样例内联成单文件 HTML（原生 HTML/Canvas，不引绘图库）：不 `fetch`、不引 CDN、不起服务，双击
即开，跑两遍逐字节相同。一个脚本一节（按文件名升序），每节必须看得到：

1. 横轴是轨迹里的 `t`（刻度取自轨迹）；每个事务一行，行标签是事务 id；
2. 行上标出 `BEGIN`、每次读（`键@版本`，读到空标 `null`）、每次写与终结（`COMMIT` 标 `ver`，`ABORT` 与
   `ROLLBACK` 标 `reason`）；
3. `ABORT` 的 `with=` 指到的事务与它连一条线，线上标触发键与 `reason`；
4. 数字与判定文字都取自轨迹（`RECLAIM` 的水位与回收量、`SUMMARY` 的计数与 `versions`），不重算。

## 5. 性能与验收口径

规模：单脚本事件 ≤ 5×10^4、事务 ≤ 10^4、键 ≤ 10^4；`trace` ≤ 2 秒，`report` ≤ 5 秒；
`trace` 额外峰值内存 ≤ 64 MiB（`tracemalloc` 扣基线）。

1. 环境：Python 3.13、只用标准库、无构建无网络；测试用 `unittest`，用例只读 `samples/`。
2. 正确性：六个脚本的 `trace` 与 `samples/expected/<同名>.txt` 逐字节相同（含结尾换行）。
3. 边界与确定性：六个脚本各盯一条口径、逐行可对（见第 6 节）；同一脚本跑两遍 sha256 相同，`report` 跑两遍
   逐字节相同。
4. 内存：无活跃事务时保留版本数等于有版本的键数；只有长事务还跑着时才堆积，它一结束就收回去。
5. 页面：双击能开，4.3 四项齐全，数字与轨迹一致；规模上限脚本自备。

## 6. 样例说明

`scripts/<名>.json` 与 `expected/<名>.txt` 一一对应；括号里是 `SUMMARY` 的后三项
`reclaim / rollback / versions`：

| 脚本 | 场景 | 关键结果 |
| --- | --- | --- |
| `read_write_conflict` | 读后写冲突：T1 读过 `k1` 后 T2 改了它并提交 | T1 `ABORT reason=read_write with=T2`，它的 `k2` 没落地（1/0/1） |
| `write_write_conflict` | 写后写冲突：T1、T2 同改 `k1`，后半段 T4 又读又写 `k1` | T2 与 T4 都是 `reason=write_write`，T4 两类都命中取写-写（3/0/1） |
| `long_txn_reclaim` | 长事务与回收赛跑：只读事务 T1 从 t=1 挂到 t=10 | 期间 `versions` 涨到 7 且一条没回收，T1 提交后一次回收 1…6（1/0/1） |
| `readonly_txn` | 只读事务：R1 期间 W1 提交了 `k1`、`k2` | R1 三次读 `k1` 都 `1\|1`、读 `k2` 得 `null`，提交不冲突且占水位（1/0/2） |
| `rollback_retry` | 回滚后重试：T1 读了 `k1` 想改 6，W1 先提交成 9 | T1 `ROLLBACK` 丢写集（不占号），T2 重试读到 `2\|9` 提交成 10（2/1/1） |
| `same_time_order` | 同刻次序与读自己的写：T1、T2、T0 的事件都在 t=0 | 提交号按脚本先后定；T1 写后读自己得 `-\|5`，T2 不冲突（2/0/2） |

`samples/notes.md` 是现场记录，不参与判定。

## 7. 待补的文档

隐藏验收用例不随仓库提供；页面配色与布局除 4.3 的必现信息外自定；加载与校验脚本只在评测侧。
