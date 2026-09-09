# C1 证据存储开发说明

状态：C1 原始证据存储与 C2 流式目录写入已接入；目录流式写入使用同目录 staging、只读预验证与单次原子发布。

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

## 回读 API 与外部承诺

```python
from anr_evidence.evidence_store import EvidenceStore

# commitment 必须来自调用方在发布时另行保留的 evidence_refs.json、ESS 或其他可信外部记录。
expected = retained_reference["artifactCommitment"]
with EvidenceStore.open_read_only("/local/context-output/evidence.sqlite3") as store:
    value = store.read_span(
        span_id,
        group_id=group_id,
        source_kind="logcat",
        max_bytes=EvidenceStore.DEFAULT_MAX_REFERENCE_READ_BYTES,
        expected_artifact_commitment=expected,
    )
    original_bytes = value["content"]
```

`read_span` 必须接收调用方外部保留的 `artifactCommitment`。不能在回读时从同一个可变 SQLite 数据库读取 commitment 再把它作为“期望值”：协调改写数据库时，攻击者可以同时替换 bytes 与库内 root，这不构成外部认证。调用方还必须传入期望 group 和 source；回读会检查归属、artifact identity、span identity、边界和 commitment。对任何 chunk-backed artifact，公共外部认证回读还必须提供正整数 `max_bytes`；是否要求上限只由已验证的 chunk 元数据决定，不信任可变的 `artifacts.content` marker。仓库内引用绑定统一使用有限的 `DEFAULT_MAX_REFERENCE_READ_BYTES`，超限时保守拒绝而不是扩大或取消预算。

`read_span_internal_integrity_only(...)` 是明确更弱的本地一致性接口。它只证明当前数据库内部结构彼此一致，不认证数据库是否仍是先前发布/保留的那一代，并且返回值故意不带 `artifactCommitment`。只有不需要来源真实性声明的 legacy/诊断路径才应使用；chunk-backed legacy 回读同样必须显式传入正整数 `max_bytes`，不允许任意无界物化。

快照不随后续输入文件变化而更新，重新捕获产生新的 artifact identity。

## C2 目录流式 staged publication

`stream_directory_event_logs_to_store(input_root, store_path, ...)` 的安全边界分为两个不同信任域：

- `input_root` 始终按对抗性输入处理。遍历只从安全打开的 root FD 出发，逐级使用 `O_DIRECTORY|O_NOFOLLOW` 或 `O_NOFOLLOW` 打开目录和文件，并以 dev/inode 核对 discovery 与实际读取对象。
- C2 输出父目录是**调用方可信的发布域**。实际打开的父目录必须是 `os.geteuid()` 所有的真实目录，且不得设置 group/other write 位；否则在创建 lock/staging 或读取输入前拒绝。能够修改这个输出目录的调用方或同 UID actor，本来就有权限查看、替换敏感的已发布数据库，位于本 API 的 input-TOCTOU 威胁模型之外。C2 不宣称抵抗任意同 UID 恶意输出目录，也不宣称在这类 actor 存在时提供 pathname 或 SQLite generation 的稳定性。

`store_path` 还必须实际位于输入根目录之外。词法 `commonpath` 与 `resolve` 只作提前拒绝，不作为证明；安全打开 input root FD 和 output parent FD 后，代码从 parent FD 开始反复相对打开 `..`（`O_DIRECTORY|O_NOFOLLOW`），按 dev/inode 走到文件系统根。若途中遇到 input root identity，则拒绝。因此即使路径在 resolve 与 parent open 之间经中间 symlink 切换并最终落入输入树，也不能通过检查。

在上述可信输出域合约下，每次写入仍保持“stage/clone/validate，然后单次 atomic replace”，并保证任何发布前失败都不修改固定父目录中的最终输出：

1. 输出父目录以 `O_DIRECTORY|O_NOFOLLOW` 打开并固定 dev/inode；所有 sibling lock、唯一 staging、最终 DB 与 SQLite sidecar 名称都只相对该 parent FD 操作。新 lock 使用 `O_EXCL|O_NOFOLLOW` 与 `0600` 创建；已有 lock 在 `flock` 前必须由 namespace metadata 与所持 `O_RDONLY|O_NOFOLLOW` FD 同时证明为 effective UID 所有的 regular file，且不得设置 group/other write 位。staging 使用 `O_EXCL|O_NOFOLLOW` 创建，且原始可读写 stage FD 一直保留到成功发布或失败擦除完成。
2. 最终数据库若已存在，先以 `O_RDONLY|O_NOFOLLOW` 相对 parent FD 打开并保留 source FD/fstat；namespace metadata 与 held FD 在 backup 前后都必须证明该对象是 effective UID 所有的 regular file，且不得设置 group/other write 位，并继续匹配同一 dev/inode。对已经确认不存在 sidecar 的 predecessor，SQLite 使用 `mode=ro&immutable=1` URI 打开，再通过标准库 `sqlite3.Connection.backup` 克隆到 staging，避免 clean persistent-WAL 数据库仅因只读 clone 而在 final 名称旁创建 `-wal`/`-shm`。最终路径从不以可写方式打开，也不会自动删除 clone 期间出现的 sidecar。这个检查防止非所有者路径技巧和意外 namespace 变化，但不声称抵抗有输出域权限的同 UID actor。
3. SQLite 必须使用 pathname 时，Linux 使用 `/proc/self/fd/<parent_fd>/...` descriptor namespace。macOS stdlib SQLite 没有 fd-stable open；实现使用 `F_GETPATH` 得到路径并立即核对 parent dev/inode。该 pathname 只在“output parent 受调用方信任”的合约下有效，不能被描述为针对恶意同 UID 输出目录的 fd-stable SQLite 保证。
4. schema 初始化/legacy migration、流式 ingestion、writer 关闭、rollback-journal/`DELETE` 模式检查、只读 schema 验证和 consumer 准备都在 staging 上完成；发布前重新核对 parent FD、stage 名称与所持 stage FD 的 dev/inode，并拒绝 live staging/final sidecar。最后一次输入 root identity 校验之后，唯一发布动作是 `os.replace(stage_name, final_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)`。
5. replace 之前的异常先关闭 consumer/SQLite，然后对原始 stage FD 执行 `ftruncate(fd, 0)` 与 `fsync(fd)`，再相对 parent FD 删除已知 stage 名称及 `-journal`/`-wal`/`-shm`。因此即使原 stage 名称已消失、被改名或原 inode 另有 hard link，所持 inode 中已捕获的 bytes 仍先被清零。擦除、fsync、unlink 或 descriptor close 的实际错误都会作为 `EvidenceStoreError` 显式上报并保留原始失败为 cause；只有在成功擦除后，已知名称 unlink 返回 `FileNotFoundError` 才可作为非致命名称缺失处理，名称消失本身不被当成清理成功证明。
6. `os.replace` 调用若抛出任意 `BaseException`，不能据此假设 rename 未发生。异常处理会把所持 stage FD 的 dev/inode 与 parent-FD-relative final/stage 名称重新对账：只有 final 已映射到该 inode 且 stage 名称消失时才标记为已发布，并绝不 truncate/unlink 该 final；若调用方因原异常收不到 consumer，则关闭已准备的只读 consumer 以避免泄漏。publication 与 consumer-delivery 是两个独立状态。清理失败仍以 `EvidenceStoreError` 上报并把原始 replace 异常保留为 cause；无法可靠对账时宁可显式失败并保留所持 inode，也不冒险擦除可能已经发布的 final。
7. replace 明确失败且身份对账证明 final 未映射到 stage inode 时，不改变固定 parent FD 命名空间中的既有最终数据库。发布成功后不再执行可能失败的 root/path 校验或重新打开 consumer；consumer 已持有经验证 staging 的只读连接，并在 POSIX rename 后继续读取同一已发布 inode。

协作 writer 通过持久 sibling lock 串行化 clone/modify/replace，避免两个遵守协议的 writer 丢失更新。最终数据库若存在 live `-journal`、`-wal` 或 `-shm` 会直接拒绝，不删除或修复这些文件；staging 在发布时也不得有 sidecar。最终 DB、lock、staging 及其 sidecar 的 inode 都属于排除集合，其位于输入树中的 hard-link alias 不会被选为 EventLog。

目录 fd 捕获的 origin 是身份快照，不是实时 pathname 声明：

```json
{
  "type": "directory_fd_snapshot",
  "scheme": "anr-directory-fd-snapshot-v1",
  "lexicalRoot": "/lexical/root/at-open",
  "relativePath": "events.txt",
  "rootDevice": "16777234",
  "rootInode": "123",
  "fileDevice": "16777234",
  "fileInode": "456"
}
```

所有 identity 数值均以字符串保存，并参与 artifact identity。`lexicalRoot` 只记录打开时的词法根名；它不声称当前该 pathname 仍指向捕获 bytes。若输入根在最终 `os.replace` 包装器内部才被替换，发布可以成功，但 origin 仍绑定原始 root/file dev+inode，consumer 也读取原始捕获 bytes；不得在 replace 后再因 live pathname 已变化而抛错。

## 尚未完成

- wheel/发布产物级验收仍未执行；本地单元回归不等同正式发布验收。
- smart-discovery 原始快照优化（当前 capture 模式绕开），更多 loader 边界和混合来源兼容验收。
- Agent/V1 引用消费与结果引用/因果校验的完整验收。当前主循环已支持 store+sidecar 的 fail-closed 绑定，但字段引用不等于因果资格。
- 非目录捕获路径仍可能使用 base64/完整文本视图；C2 目录流式接口不代表所有 loader 都已获得相同资源上限。

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

本增量不宣称完成 V1 的语义矛盾、身份/时间/blocker 资格或 DQ1/D1；C2 staged publication 只覆盖显式目录流式接口，不代表其他 loader 已迁移。
