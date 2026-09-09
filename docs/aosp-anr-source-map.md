# Android ANR timeout profiles：来源与开发边界

状态：Android 17 B1 已实现并完成本地验证；B2-14、B2-15、B2-16 是互不替代的独立 profile/验收，OEM overlay 另作来源绑定的 story。三者和任何 OEM story 均未完成，因此不得宣称 G002 完成。

## 固定来源

来源核验发生在本轮开发之前，使用官方 `android-17.0.0_r1` tag 的 peeled commit；不同仓库分别固定，不借用 main。

- [TimeoutRecord.java](https://android.googlesource.com/platform/frameworks/base/+/94b4c163b7dfe5ce3607f7bb8456f9573f7de57d/core/java/com/android/internal/os/TimeoutRecord.java)：`TimeoutRecord.TimeoutKind`，40–67 行。
- [InputDispatcher.cpp](https://android.googlesource.com/platform/frameworks/native/+/ae266dcb706d083868578cfedce381ef44488a07/services/inputflinger/dispatcher/InputDispatcher.cpp)：`InputDispatcher::onAnrLocked` 两个重载，6792–6847 行。

完整文件 SHA-256、commit、path、symbol、官方 URL 存于 `anr_evidence/aosp_contract/android-17.json`。本批不随包分发完整 AOSP 源码。B1 provenance validation 会检查 manifest 的完整 schema、自洽性、固定 revision/path/symbol/hash 值、映射关系，以及 URL 是否为由 repository/revision/path 组成的 canonical `android.googlesource.com` 地址；它不会重新下载这些 URL 对应的上游源码 blob，也不会重新计算 blob SHA-256。因此该验证不能独立证明 manifest 中的 hash 与当前上游字节一致，不能防止所有协同篡改，也不能证明真实设备兼容。

## B2 源码字节获取门禁

Android 14–16 的 profile 不能从 Android 17 manifest、其他 Git project 的 revision、网页片段或人工猜测派生。每个候选 source 必须在同一个 `platform/...` Git project 中按 release tag 解析到 immutable 40 位 commit，随后从该 commit 的 Gitiles `?format=TEXT` 响应取得 Base64，严格解码为原始 bytes 并在本地计算 SHA-256。只有这个记录与实际路径、symbol 一起被人工复核后，才可以写入 profile。

仓库提供 `scripts/fetch_aosp_source_provenance.py` 用于该捕获步骤。它只访问固定的官方根 `https://android.googlesource.com`，拒绝不安全 repository/tag/path/revision，且 tag ref、raw response、JSON 或 Base64 任一环节失败时退出而不输出半成品记录。例如（参数中的 path/symbol 必须先由对应 release 的源码树核验，示例不创建 manifest）：

```bash
python3 scripts/fetch_aosp_source_provenance.py \
  --release-tag <verified-release-tag> \
  --source <name> <platform/repository> <verified/source/path> '<verified symbol>'
```

截至 **2026-09-09**，此开发环境无法连接 `android.googlesource.com:443`，因此尚未取得 Android 14、15、16 的 raw bytes 或 SHA-256；没有新增这些 release 的 manifest，B2 和 G002 继续保持未完成。本轮对 `android-14.0.0_r1`、`android-15.0.0_r1`、`android-16.0.0_r1` 分别运行通用捕获器，均在官方 Gitiles 请求阶段超时且没有输出记录；IPv4/HTTP/1.1 的 5 秒连接探测也超时。工具的 mocked 单测只证明抓取协议、严格解码和 fail-closed 行为，不能替代实际的上游字节获取。

## B2 profile 与 OEM story 的隔离

- **B2-14、B2-15、B2-16 是三个独立验收单元。** 每个 release 必须各自拥有 release tag → 同仓库 immutable commit → Gitiles raw bytes → SHA-256 → path/symbol → manifest/profile 的证据，以及该 release 自己的规则正例、反例和设备/系统镜像兼容证据。一个 release 的 profile、hash、规则或验收结果不能回填、fallback 或签署另一个 release。
- **OEM overlay 不是 AOSP profile 的默认层。** AOSP 输入只按其显式 Android release 的已核验 AOSP profile 解析；没有明确厂商来源时不得猜测厂商、加载 overlay 或扩大 AOSP 结论。
- **每个 OEM overlay 是独立 story。** 启用前必须明确厂商/产品/构建或版本身份，并提供可核验的厂商来源与 source provenance；其规则、revision、hash、path/symbol、冲突处理、正反例和设备证据均与 AOSP profile 分开审计。来源缺失、身份不匹配、与 AOSP 或同厂商 overlay 冲突时必须 fail closed。当前没有任何已注册 OEM story，因此传入 `vendor_overlays` 仍返回 `vendor_overlay_unsupported`，而不是隐式覆盖 Android 17 或未来 AOSP profile。

## F1 · Android 14–17 Framework timeout pack 获取与观察边界

`anr_evidence/framework_timeout_lifecycle.py` 新增独立的 F1 获取/验证链，和 B1 的 Android 17 Input resolver 并列，**不**将其写回或放宽 B1 manifest：

1. `framework_timeout_pack_recipe(14|15|16|17)` 为每个 Android release 分别给出固定 release tag 与 `platform/frameworks/base` peeled commit。它不包含复用的 Android 17 blob hash，初始状态为 `pending_source_acquisition`；
2. `retrieve_framework_timeout_sources()` 复用现有 `aosp_contract.gitiles` 的 canonical tag / immutable-file URL 与 tag JSON revision parser，重新核对 tag→commit，再下载每个固定 commit 的 `?format=TEXT` 原始 bytes；
3. `verify_framework_timeout_pack()` 为每个下载文件计算 SHA-256，并检查所有通用与 kind-route symbol。只有所需 source refs 和 symbols 全部 verified 的 kind 才可用；不支持、缺 blob、错误 URL/revision/hash 或符号缺失均 fail closed；
4. `scripts/fetch_framework_timeout_pack.py --android-release <14|15|16|17> --out <pack.json>` 是显式的实际抓取入口。失败时不会写半成品；输出 pack 不含完整源码 bytes，但包含 release、commit、canonical source URL、SHA-256、symbols、每 kind status/gaps；
5. `build_framework_timeout_lifecycle()` 只接受调用方注入的 verified pack，且要求同一 release、精确 timeout kind、精确 `component=package/class`、以及 record key 才能把 trigger/cancel/recovery 编成组件 lifecycle。所有 edge 均为 `trigger_only`，`triggerIsRootCause=false`。

当前 recipes 的四个 `frameworks/base` commit 分别是 Android 14 `299fe6f5d6fc6f1af7c3411dcf4e5efdf7217368`、Android 15 `4e43ad12e1b211f15152e9c5b16b0fe88e6b93f3`、Android 16 `99b01a65cc4c104933788b3143285ab6bae65827`、Android 17 `94b4c163b7dfe5ce3607f7bb8456f9573f7de57d`。这些是互不共享的 recipe pins，**不是**已捕获的 blob provenance。此环境在 2026-09-09 仍无法连接上游 Gitiles，因此未生成真实 14–17 pack JSON；mocked retrieval tests 只验证协议和 fail-closed 行为，不能替代上游字节、OEM 或设备证据。

Framework pack 正常 kind 覆盖 `BROADCAST_RECEIVER`、`SERVICE_EXEC`、`SERVICE_START`、`CONTENT_PROVIDER`、`APP_REGISTERED`、`SHORT_FGS_TIMEOUT`、`JOB_SERVICE`、`APP_START`；同时把 `CONTENT_PROVIDER_PUBLISH_TIMEOUT` 显式标为 `non_anr_timeout` / `mustNotPromoteToFrameworkAnr=true`。`APP_REGISTERED` 指 app 主动报告 unresponsive，不等价于注册广播接收器 timeout；Job/FGS/broadcast route 的 runtime/compat 条件也会作为 pack 元数据保留。

## 最小支持范围

`TimeoutKind` 1–10 均在 manifest 列举，前两类映射，其余八类为 unsupported，不把枚举存在当作已实现组件状态机。

- `INPUT_DISPATCH_NO_FOCUSED_WINDOW` → `input.no_focused_window`。
- `INPUT_DISPATCH_WINDOW_UNRESPONSIVE` → `input.window_unresponsive`。
- `BROADCAST_RECEIVER`、`SERVICE_START`、`SERVICE_EXEC`、`CONTENT_PROVIDER`、`APP_REGISTERED`、`SHORT_FGS_TIMEOUT`、`JOB_SERVICE`、`APP_START` 暂不解析。

窗口未响应源码使用 waitQueue 中较老事件构造 reason，但源码明确指出它未必就是造成超时的事件。因此本体只记录 observed trigger，`directBlocker` 保持 null，不从 reason 推断根因。

## 接口与兼容

`resolve_timeout(package)` 要求显式整数 `android_release=17`；不从普通日志猜系统版本。可选 `aosp_source_revisions` 必须同时匹配 base/native；可选 `timeout_kind` 与观察矛盾时拒绝解析。OEM overlay 在 B1 中显式不支持；Android 14–16 profiles 和 OEM overlays 留在 B2。

只观察 event_log/logcat 中 `am_anr:` 或 `Reason:` 后的已知 Input reason。多个不同 reason 保守降级，暂不合并不同 ANR；重复 EventLog/AnrManager 包装可能导致保守拒绝，待 D1 按组适配。

Phase 1 输出新增 `classification.timeoutOntology`，保留 `detectedType`、`triggerType`、`supported`、`confidence` 等旧字段。新字段中的 observation refs 指向传入 source view 的 path/lineIndex/content，不冒充原始 artifact byte range。真正不可变引用由 C1 接入。

## 本地验证

- `python3 -m unittest tests.test_aosp_timeout_ontology -q`：14 项 ontology 专项通过。
- `python3 -m unittest tests.test_aosp_timeout_ontology tests.test_cli_phase2 -q`：18 项 ontology + legacy Phase 2 兼容专项通过。
- `python3 -m unittest discover -s tests -q`：运行 418 项，结果 `OK (skipped=1)`。
- `python3 -m compileall -q anr_evidence tests scripts` 与 `git diff --check`：通过。

## 剩余工作与验证边界

- B2 尚未完成：Android 14、15、16 仍需独立 profiles 与验收；OEM overlays 仍需明确厂商来源、冲突处理和 fail-closed 验收。
- B1 provenance validation 只检查 manifest 自洽性和 canonical source URL，不重新下载或重算上游源码 blob；需要独立 source-blob verification 才能证明记录的 SHA-256 对应实际上游字节。
- 尚未执行真实设备/系统镜像兼容性验证；合成 fixture 和本地 unittest 不能证明设备兼容。
- AI context 按组输出、设备 build metadata 自动采集和其余八类 TimeoutKind 的组件状态机不属于 B1 已完成范围。
- 三项保守字段不变；S1 对真实私有日志的默认外发拒绝保持不变。
