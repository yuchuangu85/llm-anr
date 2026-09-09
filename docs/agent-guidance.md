# Agent 指引生成合同

状态：**P2 已实现；由一致性回归和 `--check` 保持可验证。**

本项目将跨工具重复的 ANR 工作流收敛到一个可审计的源片段，而不是在多个根目录指导文件中手工复制。它不使用 symlink，以保证 Windows checkout 与各客户端读取路径的兼容性。

## Canonical sources 与输出

| Source | 责任 | Output |
| --- | --- | --- |
| `agent_guidance/project.md` | 所有工具共用的项目工作流、命令、ANR 专项和安全结论 | 所有受管指导文件 |
| `agent_guidance/tools/codex.md` | Codex 专属说明 | `AGENTS.md` |
| `agent_guidance/tools/claude.md` | Claude Code 专属说明 | `CLAUDE.md` |
| `agent_guidance/tools/gemini.md` | Gemini 专属说明 | `GEMINI.md` |
| `agent_guidance/tools/codebuddy.md` | CodeBuddy 专属说明 | `CODEBUDDY.md` |
| `agent_guidance/tools/hermes.md` | Hermes 专属说明 | `HERMES.md` |
| `agent_guidance/tools/cursor.md` | Cursor 专属说明 | `.cursor/rules/project.mdc` |

`AGENTS.md`、`GEMINI.md`、`CODEBUDDY.md`、`HERMES.md` 与 Cursor rule 都是 generator 物化的 regular files。`CLAUDE.md` 则保留 Claude Code 的项目内原生 include：

```md
@agent_guidance/project.md
@agent_guidance/tools/claude.md
```

不要在 `AGENTS.md` 中写入未受支持的 in-file include。对 Codex，共享内容必须在生成的 `AGENTS.md` 内物化；不能用 symlink、fallback filename 或相邻文档代替同目录的项目指引。

## 所有权与 OMX 安全边界

generator 只拥有每个输出中的以下区块：

```md
<!-- ANR:PROJECT-GUIDANCE:START -->
…
<!-- ANR:PROJECT-GUIDANCE:END -->
```

- 该区块以外的文本不由 generator 覆盖。
- 所有 `<!-- OMX:…:START --> … <!-- OMX:…:END -->` runtime/team/guidance 区块都被当作不透明内容：先验证嵌套和闭合，再逐字保留。
- 若旧文件已有任何 OMX marker、但没有上述 `ANR:PROJECT-GUIDANCE` 受管区块，generator 拒绝初始化而不写文件；这避免把 runtime overlay 当作项目模板覆盖。
- 不闭合、未匹配或错误嵌套的 OMX marker 会 fail closed。
- 输出文件或其既有父路径为 symlink 时会被拒绝；`.claude/settings.local.json` 不会被读取、合并或重写。

## 使用与 CI

```bash
# 从 canonical fragments 生成或刷新输出。
python3 scripts/generate_agent_guidance.py

# 只验证所有受管输出与 fragments 一致；发现 drift 返回非零且不写文件。
python3 scripts/generate_agent_guidance.py --check

# 定向验证 generator、OMX 保留与 native include wrapper。
python3 -m unittest tests.test_agent_guidance_generation -v
```

修改项目级流程时，先改 `agent_guidance/project.md`；修改某一客户端专属规则时，只改相应的 `agent_guidance/tools/*.md`。随后运行生成器、`--check` 和定向测试。不要直接编辑受管区块。
