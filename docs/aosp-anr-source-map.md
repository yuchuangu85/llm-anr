# Android 17 ANR profile：来源与开发边界

状态：B1 首批已实现，尚未测试或验证；不得按此文档宣称 B1/G002 验收完成。

## 固定来源

来源核验发生在本轮开发之前，使用官方 `android-17.0.0_r1` tag 的 peeled commit；不同仓库分别固定，不借用 main。

- [TimeoutRecord.java](https://android.googlesource.com/platform/frameworks/base/+/94b4c163b7dfe5ce3607f7bb8456f9573f7de57d/core/java/com/android/internal/os/TimeoutRecord.java)：`TimeoutRecord.TimeoutKind`，40–67 行。
- [InputDispatcher.cpp](https://android.googlesource.com/platform/frameworks/native/+/ae266dcb706d083868578cfedce381ef44488a07/services/inputflinger/dispatcher/InputDispatcher.cpp)：`InputDispatcher::onAnrLocked` 两个重载，6792–6847 行。

完整文件 SHA-256、commit、path、symbol、官方 URL 存于 `anr_evidence/aosp_contract/android-17.json`。本批不随包分发完整 AOSP 源码；本地 manifest 检查只检查基本版本和 commit 形状，不等于离线重算上游文件 hash，也不能防止所有 manifest 篡改。完整来源一致性验收仍待实施。

## 最小支持范围

`TimeoutKind` 1–10 均在 manifest 列举，前两类映射，其余八类为 unsupported，不把枚举存在当作已实现组件状态机。

- `INPUT_DISPATCH_NO_FOCUSED_WINDOW` → `input.no_focused_window`。
- `INPUT_DISPATCH_WINDOW_UNRESPONSIVE` → `input.window_unresponsive`。
- `BROADCAST_RECEIVER`、`SERVICE_START`、`SERVICE_EXEC`、`CONTENT_PROVIDER`、`APP_REGISTERED`、`SHORT_FGS_TIMEOUT`、`JOB_SERVICE`、`APP_START` 暂不解析。

窗口未响应源码使用 waitQueue 中较老事件构造 reason，但源码明确指出它未必就是造成超时的事件。因此本体只记录 observed trigger，`directBlocker` 保持 null，不从 reason 推断根因。

## 接口与兼容

`resolve_timeout(package)` 要求显式整数 `android_release=17`；不从普通日志猜系统版本。可选 `aosp_source_revisions` 必须同时匹配 base/native；可选 `timeout_kind` 与观察矛盾时拒绝解析。OEM overlay 尚不支持。

只观察 event_log/logcat 中 `am_anr:` 或 `Reason:` 后的已知 Input reason。多个不同 reason 保守降级，暂不合并不同 ANR；重复 EventLog/AnrManager 包装可能导致保守拒绝，待 D1 按组适配。

Phase 1 输出新增 `classification.timeoutOntology`，保留 `detectedType`、`triggerType`、`supported`、`confidence` 等旧字段。新字段中的 observation refs 指向传入 source view 的 path/lineIndex/content，不冒充原始 artifact byte range。真正不可变引用由 C1 接入。

## 剩余工作

- 回归、歧义/版本/畸形字段负例、旧 API/产物兼容和 wheel 资源验证尚未执行。
- manifest 完整结构/映射/来源 hash 一致性和离线来源验收尚未完成。
- AI context 按组输出、设备 build metadata 自动采集、其他 TimeoutKind 与多版本/OEM 能力不在本批范围。
- 三项保守字段不变；S1 对真实私有日志的默认外发拒绝保持不变。
