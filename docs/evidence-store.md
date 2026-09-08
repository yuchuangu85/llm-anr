# C1 证据存储开发说明

状态：基础存储、目录/归档/混合输入捕获、上下文/ESS/Agent 的可选接入，以及可恢复的完整工作区发布交换均已开发；本轮新增代码尚未测试或验收。

## 目录输入接入

```python
from anr_evidence import load_package_from_directory, AiContextOptions, build_ai_context_artifacts

package = load_package_from_directory("/local/bugreport", capture_originals=True)
index = build_ai_context_artifacts(
    package,
    AiContextOptions(out_dir="/local/context-output", build_evidence_store=True),
)
```

两个选项默认关闭，避免向所有旧调用方引入原始数据副本和额外存储成本。启用捕获时目录输入跳过 smart-discovery，直接读取文件；原始 bytes 以 base64 携带，legacy text view 仍使用通用换行。输出目录应位于输入目录之外，避免下一轮扫描重新读取生成物。

包中的 `source.originalArtifacts` 是 loader 提供的原始快照及来源信息，不是由过滤字符串反编码出来的“原文件”。`load_package_from_archive` 和 `load_package_from_path` 同样提供 `capture_originals=True`。归档在原有预算限定的那次成员读取中捕获 bytes，不重新解压；origin 区分 container/member，并记录 memberOffset 以区分同名成员。混合目录传递已选中来源的快照；capture 模式绕开 smart-discovery，普通默认路径不变。旧 fixture 没有原始快照时，不会伪造 immutable 引用。

## 导出产物

- `evidence.sqlite3`：本地原始字节快照、SHA-256、artifact/group/span；临时 SQLite 文件只在 staging 工作区内生成。
- `<group-id>/evidence_refs.json`：每条展示行的 resolved/unresolved 状态，以及已解析行的 span ID、artifact ID、byte range。
- `index.json`：相应 artifact path、保留的 Markdown 分析槽位和完整过滤 logcat。

生成器会先在输出目录同级的 staging 工作区构建完整一代产物，之后通过可恢复的目录交换发布。若交换进程中断，下次生成会恢复唯一的前代备份；若发现多个候选备份则拒绝猜测。每次生成还会删除不属于当前 `index.json` group 的 `evidence_refs.json`/`evidence.jsonl`，并在关闭对应选项时删除过期 store/ESS 生成物；`anr_analysis.md` 保留供人工分析续写。

**数据库含未过滤、未脱敏的原始文件，可能包含不属于某个 ANR 时间窗口的敏感信息。禁止外发。** group-span API 的归属检查不是数据库文件级访问控制。该存储不解除 S1 的真实日志外发限制。

只有唯一、逐字匹配的原始行建立 span；重复内容、跨 shard 多个相同出现位置、转换后的 trace 展示行和缺少原始快照等情况保留 unresolved。异常 UTF-8 的原始 bytes 仍存储，但不为替换字符后的展示内容伪造精确引用。byte range 包含原行换行字节，保留 CRLF/多字节内容/末尾无换行情况的原始表示；上述边界尚待测试验收。

## 回读 API

```python
from anr_evidence.evidence_store import EvidenceStore

with EvidenceStore("/local/context-output/evidence.sqlite3") as store:
    value = store.read_span(span_id, group_id=group_id, source_kind="logcat")
    original_bytes = value["content"]
```

调用方必须传入期望 group 和 source；回读会检查归属、artifact hash、span 身份及边界。快照不随后续输入文件变化而更新，重新捕获产生新的 artifact ID。

## 尚未完成

- 所有本轮新增代码的测试/验证、升级及 wheel 验收。
- smart-discovery 原始快照优化（当前 capture 模式绕开），更多 loader 边界和混合来源兼容验收。
- Agent/V1 引用消费与结果引用/因果校验的完整验收。当前主循环已支持 store+sidecar 的 fail-closed 绑定，但字段引用不等于因果资格。
- 真实文件系统级单步原子目录替换：当前是完整 staging + 可恢复的两次目录交换，不把短暂不可见窗口表述为单一原子系统调用。
- 大输入流式处理：base64、文本视图、SQLite blob 和逐行哈希回读会有额外 CPU/内存开销，不证明 C2 的资源目标。

## ESS 引用开发接入

在 `AiContextOptions` 同时设置 `build_evidence_store=True, build_ess=True`，writer 会为每组增加 `evidence.jsonl` 并在 index 记录路径。

`EvidenceSlice.evidence_ref` 为新增可选字段；没有引用的旧 JSONL 字段保持原状。`attach_store_references` 不信任已有 JSONL 引用，而是使用侧车候选回读数据库，检查 group/source 和解码后的完整行内容，仅保留唯一且匹配的 span。失败时保留原 slice 并清空引用；AnrManager 展示来源映射回原 logcat artifact。

字段绑定不等于因果资格：时间、进程、直接阻塞点和结论矛盾门禁仍需 V1/DQ1/D1。真实 Agent 主循环在调用方同时提供 `evidence_store` 与逐组 sidecar 引用时会重建 ESS、回读绑定，并在完整性失败时于任何 HTTP 请求前停止。

## Agent / V1 首批引用消费

`run_ai_agent_analysis(..., evidence_store=store, evidence_references=refs_by_group)`
显式启用引用模式；两个参数必须同时提供，store 的打开/关闭由调用方负责。
`refs_by_group` 是逐组 sidecar 的 `{groupId: references}` 映射，不从模型提供的路径打开数据库。
主循环重建 ESS 后绑定引用，向 specialist 展示 span ID，向 manager 提供本轮保留片段的引用目录。
目录只包含 group/source/span ID/展示内容，不发送原始 blob、文件路径或完整数据库。

引用模式下，chain 和 conclusion 均须包含 `groupId` 与非空 `evidenceRefs: ["span-..."]`。
接受边界要求结构及保守字段合法、引用属于当前组且实际提供于本轮、原始字节回读一致。
模型响应期间发生的存储变化也会被拒绝；re-probe 重新绑定、截断，只允许引用本轮保留的证据。
当前尚无完整的确定性因果资格/矛盾判定，引用模式一律拒绝 `high`，不把有效引用等同根因证明。

`AiAgentResult.validation_status` 区分 `not_accepted`、`structure_only`（旧路径）与
`references_verified_causality_unverified`（引用模式）。旧路径保持原 schema，不因此获得引用已验证标记。
真实日志外发仍默认拒绝；仅合成测试可以显式启用已有 `synthetic_only` 边界。

JSONL 中的 `evidence_ref` 不受信任：`read_ess_jsonl(path)` 默认清空引用；提供
`store=store, references=refs_by_group` 才执行原始回读并恢复可用引用。
原始快照保留所有检测为同一 source kind 的物理输入（包括旧文本视图去重/筛选掉的来源），
因此跨 shard 的重复行不会在文本去重后错误获得“唯一”引用；这可能保守地增加 unresolved。

本增量不宣称完成 V1 的语义矛盾、身份/时间/blocker 资格或 DQ1/D1，也未达到 C2 大输入资源目标。
