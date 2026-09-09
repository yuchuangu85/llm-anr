# v1 Definition of Done（DoD）与最终验收

> 文档状态：v1。最后核对：2026-09-09。
> 任务来源：[当前 backlog](TODO.md)。安全边界优先于功能、评测分数和模型输出，见 [安全边界与迁移说明](safety-boundaries.md)。
> 本文件是验收合同与签核格式，不授予真实/私有日志出站、设备访问、外部下载或生产发布权限。

本文件把“局部代码/测试已通过”“最小可靠 Input v1 可接受”和“完整路线图全部验收”严格分开。C2/G003 的 raw directory/ZIP/TAR 默认单路径已取得 `verified-local-slice` 证据；B2、D2、F1、F2、P1、A1、Q1 仍是独立的 P2 扩展。它们**不阻塞最小可靠 Input v1**，但会阻塞相应扩展能力或“完整路线图”签核。任何 release 都只能声明其实际通过的 scope，不能借 core v1 的通过声称跨版本、真实语料、设备或外部工具兼容。

## 1. 两个验收决定与明确支持范围

| 决定 | 可声明的范围 | 必须通过的 gate | 当前状态 |
| --- | --- | --- | --- |
| `core-v1-minimum-reliable-input` | 离线、本地证据处理；当前 Android 17 已核验的 Input 范围；未知/未映射输入 fail-closed；不把候选结论提升为最终根因 | G001/R1/R2、S1 的 default-deny 本地边界、B1 Android 17、C1、V1、DQ1、D1、G010，以及本文件的 candidate/review 证据 | `not-evaluated`：当前工作树不是 clean release candidate，不能签核 |
| `full-roadmap-v1` | core v1 加上 Android 14–17 source-byte profiles、已在本地验收的 default raw-input streaming、完整 lifecycle、framework/Binder/performance/Agent 扩展及跨版本正式评测 | `core-v1-minimum-reliable-input` 加所有其余 P2 gate（B2/D2/F1/F2/P1/A1/Q1） | `blocked`：当前证据明确不足 |

### 1.1 core v1 的硬限制

core v1 acceptance 只有在下面的用户可见支持声明随候选一起发布时才有效：

- 真实/私有日志默认保持本地；`ProviderConfig.outbound_policy` 保持默认 `deny`。`synthetic_only` 仅是 fixture 调用方 attestation，不能成为真实日志出站许可。经独立审批的 `redacted_private` 是受限扩展路径：必须使用当前 verified span 的 reference-preserving view，并绑定 provider/model、purpose、approval、ruleset、expiry 与 view/map commitments；这不等于自动匿名化认证。
- Android timeout ontology 只声明已核验的 Android 17 Input 范围；Android 14、15、16、OEM overlay、未知版本、冲突 provenance 或未映射 kind 必须报告 unsupported/unknown，不能猜测 direct blocker。
- 结论始终保持 `finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`。高置信只有在 V1/DQ1 全部资格满足时才可能出现；当前 auditor 保持 high fail-closed。
- 任何未实现的真实 dumpsys lifecycle、external tool/viewer、external search/expand、production corpus 或 device evidence 都不得出现在 core v1 的能力声明中；C2 的 default raw-input route 只能按其本地验证范围声明，不能外推为跨设备硬资源保证。

### 1.2 完整路线图的硬限制

`full-roadmap-v1` 不能用 core 的局部通过替代以下条件：Android 14–16 immutable source bytes、OEM provenance、D2 真实 lifecycle、F1/F2/P1 的 pack/adapter/device evidence、A1 后续外部能力合同，以及 Q1 的 licensed/deidentified Android 14–17 formal corpus。上述缺失必须保留为 `blocked`。

C2 default directory/ZIP/TAR routing 已有本地 default-path 证据；它不替代跨版本、真实设备或外部工具条件。

## 2. 状态词与不可替代原则

| 状态 | 含义 | 能否用于验收 |
| --- | --- | --- |
| `verified-local-slice` | 受限代码路径和所列命令在指定 revision/环境已通过 | 仅满足对应 scope/gate 的局部证据 |
| `implemented-pending-evidence` | 代码或文档存在，但没有本次 candidate 的可复查 evidence | 否 |
| `blocked` | 缺少必要输入、版本、权限、语料、设备或工具，或 gate 主动 fail-closed | 否；保留 blocker 与恢复条件 |
| `not-applicable` | 明确在所声明 scope 外，且有批准者、理由、影响 | 不能跳过该 scope 所要求的安全、引用或质量门禁 |
| `accepted` | 所声明 scope 的所有必要 gate 都有本次 candidate 的完整证据包，且无未批准 blocker | 是 |

以下替代一律无效：

- `trigger`、压力线索、router 选择、模型分数或 specialist prose 代替 direct blocker、因果或根因；
- 过滤视图行号代替原始 immutable byte/span 引用；
- Android 17 manifest 自洽性、synthetic fixture 或单一平台测试代替**所宣称跨版本范围**的上游 bytes、OEM 或设备兼容性；
- 成功 build、metadata-only WinScope ZIP 校验或固定 Perfetto query 代替所宣称的 capture/viewer/device 兼容性；
- 一次 formal case 通过、未完整标注输出或 legacy hint 通过代替完整路线图所需的 frozen、licensed/deidentified、分版本 coverage 正式评测；
- 历史测试总数、其他 dirty worktree 日志或未绑定 revision 的截图代替当前 release-candidate evidence。

## 3. Release candidate 与证据包

每次签核必须在一个指定、可复现的 release candidate 上产生证据包；含未说明修改的工作树不能被签为 `accepted`。

### 3.1 Candidate 身份

记录并归档以下输出；它们必须指向同一个 commit：

```bash
git rev-parse HEAD
git status --short --branch
git diff --check
python3 --version
```

- candidate 必须是可识别 commit；工作树应为 clean。验收输出若需持久化，应纳入候选提交或写入候选外、可 hash 的 evidence directory。
- manifest 必须记录 OS、CPU 架构、Python、执行日期、命令、退出码、输入 SHA-256、输出 artifact SHA-256 与外部工具的精确版本/hash。
- AOSP/Gitiles、Perfetto、WinScope 或 device evidence 必须给出 immutable identity；URL、tag 名称、文件名或“最新版”都不充分。

### 3.2 最小证据包

```text
acceptance/<candidate-id>/
  manifest.json                 # commit、环境、命令、退出码、artifact hashes
  unit-test.txt                 # unittest 原始输出
  compileall.txt
  diff-check.txt
  hint-eval.txt
  formal-eval.json              # 如运行 formal eval，保存完整 JSON
  benchmarks/                   # 实际运行的 C2/digest/replay 输出
  provenance/                   # source-byte/pack/adapter identity
  device/                       # 许可范围内 device/capture evidence
  review.md                     # gate matrix、blocker、签核和风险
```

证据包不是原始 bugreport 的替代品。真实或私有日志不能因为收集验收记录而绕过 default deny、脱敏或数据最小化。

## 4. 每个 candidate 的基础验证

以下命令为 core 与 full-roadmap candidate 的共同最低回归集；原始输出与退出码必须进入证据包：

```bash
python3 -m unittest discover -s tests -q
python3 -m compileall -q anr_evidence scripts tests
git diff --check
python3 scripts/run_eval.py tests/fixtures/eval
```

判定规则：

1. unittest、compileall、diff check、hint eval 必须零失败；skip 必须记录原因，不能用 skip 满足不可跳过 gate。
2. P2 扩展发生变更时，先执行相应专项回归，再执行本节全量命令；适用时补 benchmark、replay、真实样本或设备证据。该顺序与 [TODO 的通用完成条件](TODO.md)一致。
3. 如 candidate 声称 `full-roadmap-v1` 或跨版本 formal qualification，必须运行：

   ```bash
   python3 scripts/run_eval.py --formal <frozen-formal-corpus> --json
   ```

   仅当退出码为 `0` 且 JSON 的 `qualified=true`、`qualificationStatus="qualified"` 才通过。还必须确认 `labelingScope.fullyLabeledOutputDomains` 覆盖 v2 的六个输出域、每个 `outputDimensionMetrics.*.completeFailCases=0`、没有未标注/意外输出、每个 `ruleCoverage` 的正例与反例均通过，及 v3 `corpusGovernance` 的 evaluation-only 样本量/类别 support、事故与设备 split leakage、missing/unsupported manual labels 均满足冻结合同。退出码 `2` / `blocked` 是正确的 fail-closed 运行结果，但不是 full-roadmap pass；它本身不否定不声称该能力的 core v1。详见 [formal-eval-v2 合同](formal-eval-v2.md) 与 [formal-eval-v3 admission 合同](formal-eval-v3-real-corpus-admission.md)。

4. G003/C2 default raw-input extension 的原始 benchmark 必须用已检查入库的 default-route runner 重新产生，例如：

   ```bash
   python3 scripts/benchmark_c2_default_routing.py --size-mib 100 --groups 2 --cold-runs 1 --warm-runs 1 --input-kind directory
   python3 scripts/benchmark_c2_default_routing.py --size-mib 1024 --groups 2 --cold-runs 1 --warm-runs 1 --input-kind directory
   ```

   保存 input hash、group count、cold/warm wall time、peak RSS、store/output/working-tree bytes，并确认 `inputRouting.mode=store_backed_default`、完整 filtered baseline 与 compact-view hard cap。默认 directory/ZIP/TAR 小 fixture 的端到端 baseline 回归与上述大目录基准共同构成当前 C2 local slice；仅运行显式 store-backed harness 仍不能证明 default routing。

## 5. Gate 矩阵

各 story 的细节合同仍以 [TODO](TODO.md) 和第 6 节索引为准。`当前快照`描述 2026-09-09 的已知范围，不是候选签核。

| Gate | 适用验收范围 | DoD 与必需证据 | 当前快照 |
| --- | --- | --- | --- |
| G001 / R2 / R1 | core | 组内 identity/scope/time/fusion 负例和真实 Agent grouping regression；完整 selected-trace inventory；压力线索不升级根因 | `verified-local-slice`，历史提交 `a7a1347`；候选仍需重跑 |
| S1 | core（离线 default-deny）/扩展（受限私有出站） | core 必须验证 default deny、synthetic-only 边界、预算与结构拒绝。私有扩展还必须验证 verified-span redacted view、local-only replacement map、exact admission **及外部 verifier**、单请求 permit、fixed endpoint/no redirect、每 retry 复验、零 HTTP 负例、re-probe fail closed，以及 mixed ZIP/TAR/loose accepted-input accounting | local code/mock regression 已验证；真实受许可 corpus、可审计独立 verifier、隐私/DLP 审查和完整对抗语料仍 `blocked`。shared budget 不宣称 RSS/wall/CPU/ZIP-central-directory 保证，不得列入 core scope |
| G002 / B1 | core（Android 17） | Android 17 manifest/path/symbol、Input reason 正反例、unknown/矛盾/无版本均降级；不从 trigger 推断 blocker | `verified-local-slice` |
| G002 / B2 | full-roadmap | Android 14–16 各自 tag → peeled commit → Gitiles blob → SHA-256 → symbol；OEM 来源/冲突和设备 evidence | `blocked`：14–16 raw bytes/SHA-256 与 OEM evidence 缺失 |
| G003 / C1 | core | store/ESS/ref integrity、winner provenance、rollback、四槽位与 baseline 保留 | `verified-local-slice`；候选仍需重跑 |
| G003 / C2 | full-roadmap | default directory/ZIP/TAR routing 的端到端 baseline、资源与磁盘证据 | `verified-local-slice`：`tests/test_c2_default_routing.py` 覆盖真实 temporary directory/ZIP/TAR 与完整 filtered baseline；100 MiB/1 GiB default-directory cold/warm 记录见下方 artifacts。仅为当前机器实测，不是跨设备硬 RSS/壁钟保证 |
| V1 / DQ1 | core | immutable anchor/direct blocker、PID/time/scope/quality/provenance 的 acceptance/negative regression；DQ1 source-local `dumpLifecycle` 仅由 D1/F2/未来 dump adapter 复用，证据不足保持 low/medium/unknown | `verified-local-slice`；current high 仍 fail-closed，source-local lifecycle 不证明 freshness/staleness/process lifetime |
| G004 / D1 | core | Input → WMS/AMS/Recents 的 target/blame/lifecycle fixture；wrong PID、late dump、pending-focus、embedded PID、重复 suppression、recovery、Recents cancel/finish 与 missing-event=`unknown` 负例 | `verified-local-slice`；synthetic/local rule evidence only，不证明真实设备或 OEM 格式兼容 |
| G004 / D2 | full-roadmap | 真实 dumpsys input/window、token/display transfer、queue/dump lifecycle 与 Recents 状态机 evidence | `implemented-pending-evidence`：首个 strict raw snapshot adapter 已由 `tests/test_dumpsys_input_window.py` 覆盖 direct parser 与 raw directory/ZIP/TAR default route，且只投影到 DQ1 / existing inputLifecycle；capture timestamp、qualified blame、真实设备/OEM profile、完整 queue/dump lifecycle 与 Recents 状态机仍 `blocked` |
| G005 / F1 | full-roadmap | Android 14–17 verified timeout packs、kind/cancel/recovery/错误 provenance/OEM/device evidence | recipe/verifier 与受限 lifecycle 已有；实际 packs/device `blocked` |
| G006 / F2 | full-roadmap | exact transaction/caller-peer、Watchdog 顺序、lock-owner、生产 dump adapter 和设备 regression | 首个观察链与 DQ1 shared `dumpLifecycle` projection 已验证；F2 不另建 stale/wrong-PID 门；其余 `blocked` |
| G007 / P1 | full-roadmap | capability manifest、hash/version rejection、trace/capture manifest、clock mapping、viewer/device compatibility | fixed query/metadata-only verifier 已验证；viewer/capture/device `blocked` |
| G008 / A1 | full-roadmap | router/re-probe audit、budget/no-result/反证停止 regression；未来新 role/tool 必须先有安全与证据合同 | local bounded router/re-probe 已验证；external search/expand、telemetry、SOP 未完成 |
| G009 / E1 / Q1 | full-roadmap | frozen v3 corpus manifest + case SHA-256；v2 六个 output-domain label scope 与独立 metrics；每条 formal rule 的通过/不触发正反例；许可证/脱敏/人工标注 record commitments、incident/device split leakage=0、分割样本量与每类 minimum support；14–17 coverage；`licensed_deidentified`；immutable refs、ontology provenance、resource/zero-model-call budgets；`acceptanceMetrics` 中 supported citation integrity=100%、high-confidence violations=0、adversarial trigger false positives=0。无样本只能 N/A，required measurement 则 blocked；macro-F1/top-3 须有独立 metric execution | 仅 2 个 synthetic Android 17 case；三项确定性 acceptance metric 当前通过，但无外部核验的真实语料、14–16 support 或 macro-F1/top-3 engine，formal gate `blocked` |
| G010 / DOC | core 与 full-roadmap | 本文、README 导航、TODO 状态、专项合同、evidence bundle、签核模板与独立 reviewer 记录一致 | 本文已落地；后续 story 改动必须同步矩阵 |

## 6. 专项合同与证据入口

| 范围 | 权威入口 | 验收用途 |
| --- | --- | --- |
| Backlog/状态 | [docs/TODO.md](TODO.md) | 当前完成边界、P0/P1/P2 与未完成项的唯一 backlog 入口 |
| 安全/远程模型 | [docs/safety-boundaries.md](safety-boundaries.md) | default deny、合成路径、HTTP/结构预算、保守字段 |
| Evidence store / C1/C2 / DQ1 | [docs/evidence-store.md](evidence-store.md) | source coordinates、ESS/ref、store readback、预算和 dump-quality 边界 |
| AOSP timeout provenance | [docs/aosp-anr-source-map.md](aosp-anr-source-map.md) | B1/B2/F1 source bytes、symbols、unsupported 与 Gitiles fail-closed 边界 |
| Agent router/re-probe | [docs/agent-router-contract.md](agent-router-contract.md) | 已有 specialist 的 capability routing、本地 tag/window probe 与停止预算 |
| Performance adapters | [docs/performance-adapter-contract.md](performance-adapter-contract.md) | tool capability/hash/version、fixed query 与 WinScope metadata 边界 |
| C2 default-route benchmark artifacts | [100 MiB](benchmarks/c2-default-routing-100MiB-2026-09-09.json)、[1 GiB](benchmarks/c2-default-routing-1GiB-2026-09-09.json) | raw default entrypoint 的 current-machine directory cold/warm 记录：default routing、完整 filtered baseline、compact cap、RSS 和磁盘检查均通过 |
| Historical explicit-store harness | [docs/benchmarks/c2-store-context-2026-09-09.json](benchmarks/c2-store-context-2026-09-09.json) | 显式 store-backed export 的历史原始记录；不作为 default-routing evidence |
| Formal qualification | [formal-eval-v2 合同](formal-eval-v2.md)、[formal-eval-v3 admission 合同](formal-eval-v3-real-corpus-admission.md)、`anr_evidence/formal_eval.py`、`scripts/run_eval.py`、frozen corpus manifest | 以 JSON aggregate 的 `qualified`、gate failures、labeling scope、逐域 metrics、规则正反例、`corpusGovernance`（split/support/missing/unsupported）与 `acceptanceMetrics`（citation/high-confidence/adversarial/N/A）为准，不看 pass case 数量或旧 hint 总数 |

## 7. 当前验收快照（不是 release approval）

本快照只记录 2026-09-09 在当前 dirty 开发工作树执行的结果；没有绑定 clean release candidate commit，不能签为 `accepted`。

| 检查 | 本次结果 | 解释 |
| --- | --- | --- |
| `python3 -m unittest discover -s tests -q` | 653 passed，2 skipped | 当前本地工作树回归；包含 formal-eval-v3 corpus admission、DQ1 shared lifecycle 与 D1/D2 deterministic acceptance 回归；skip 不自动获批 |
| `python3 -m compileall -q anr_evidence scripts tests` | 通过 | Python 编译检查 |
| `git diff --check` | 通过 | 仅检查文本空白，不证明行为或 candidate identity |
| `python3 scripts/run_eval.py tests/fixtures/eval` | 13/13 passed | hint regression 通过；不等于 formal qualification、六域输出正确率或端到端根因准确率 |
| `python3 scripts/run_eval.py --formal tests/fixtures/formal_eval` | 2/2 v3 case passed，退出码 2，`qualification=blocked` | v3 governance 为 evaluation=2、四类 support 各 1、无 split leakage/手工缺失/unsupported；Android 14/15/16 coverage 为 0，source class 为 `synthetic`，正确地未获得 full-roadmap 资格 |
| C2 default-route artifacts | 100 MiB、1 GiB directory 各 cold/warm；1 GiB RSS 60,293,120 / 60,342,272 bytes | 当前机器的 default raw-input route 已验证；不证明跨设备硬 RSS/壁钟墙、ZIP central-directory 成员数解析上限或真实设备兼容 |

当前决定：

- `core-v1-minimum-reliable-input = not-evaluated`：局部工程证据存在，但必须从 clean candidate 重新采集；candidate 必须保持 offline、Android 17 已核验范围和上述非声明项。
- `full-roadmap-v1 = blocked`：B2、D2、F1、F2、P1、A1、Q1 的扩展证据仍不完整；formal gate 的 blocked 输出必须保留，不能用 core 测试覆盖。
- C2/G003 只有明确列出的 `verified-local-slice` 已验证，不能被外推为跨设备、真实设备或全路线图签核。

## 8. 最终验收顺序

1. 冻结 candidate commit，创建第 3 节 evidence bundle，记录 candidate identity。
2. 选择要签核的 scope：`core-v1-minimum-reliable-input` 或 `full-roadmap-v1`。先写入 support/exclusion list，后运行验证；不得在看到结果后扩大 scope。
3. 运行第 4 节共同回归。失败即停止，修复后从 step 1 重新采集。
4. 按第 5 节只检查该 scope 所必需的 gate；full-roadmap 还必须覆盖全部扩展 gate 和 formal qualification。
5. 对每个 `blocked`/`not-applicable` 写明恢复条件。不得以 waiver 静默跳过安全、引用、已声明的 Android release、语料 provenance 或设备兼容性。
6. 独立 reviewer 复核 manifest 的 command/exit-code/hash、gate matrix、scope 声明和结论；reviewer 不以模型生成摘要代替原始输出。
7. 仅当选定 scope 的全部 required gate 为 `accepted`，才写入同名 acceptance；否则保留 `blocked` 或 `not-evaluated`。

## 9. 最终签核模板

```yaml
schemaVersion: agent-anr-v1-final-acceptance-v1
candidate:
  gitCommit: "<40-char SHA>"
  workingTreeClean: true
  executedAt: "<ISO-8601>"
  environment:
    os: "<value>"
    architecture: "<value>"
    python: "<value>"
scope:
  requested: "core-v1-minimum-reliable-input" # or full-roadmap-v1
  supportedAndroidReleases: [17]
  remoteRealPrivateLogsEnabled: false
  exclusions:
    - "Android 14-16 and OEM timeout ontology"
    - "real-log remote analysis"
    - "P2 capabilities not accepted for this scope"
verification:
  unitTests: { command: "...", exitCode: 0, artifact: "unit-test.txt", sha256: "..." }
  compileall: { command: "...", exitCode: 0, artifact: "compileall.txt", sha256: "..." }
  diffCheck: { command: "...", exitCode: 0, artifact: "diff-check.txt", sha256: "..." }
  hintEval: { command: "...", exitCode: 0, artifact: "hint-eval.txt", sha256: "..." }
  formalEval: { command: "... --json", exitCode: 2, qualified: false, artifact: "formal-eval.json", sha256: "..." }
gates:
  G001_R1_R2: { status: accepted, evidence: ["..."] }
  S1_core_default_deny: { status: accepted, evidence: ["..."] }
  B1_android17: { status: accepted, evidence: ["..."] }
  C1: { status: accepted, evidence: ["..."] }
  V1_DQ1: { status: accepted, evidence: ["..."] }
  D1: { status: accepted, evidence: ["..."] }
  G010_DOC: { status: accepted, evidence: ["docs/v1-dod-and-final-acceptance.md"] }
  # Add B2/D2/F1/F2/P1/A1/Q1 only when requested scope is full-roadmap-v1.
blockers: []
decision:
  coreV1MinimumReliableInput: "accepted | blocked | not-evaluated"
  fullRoadmapV1: "accepted | blocked | not-evaluated"
review:
  reviewer: "<human or independent reviewer identity>"
  reviewedAt: "<ISO-8601>"
  statement: "I independently inspected the declared scope, commands, exit codes, hashes, gate evidence, and blockers."
```

无法诚实填写的字段必须保留 `blocked` 或 `not-evaluated`，不能通过删除 gate、删除 exclusion 或把局部测试升格为 `accepted` 来完成签核。
