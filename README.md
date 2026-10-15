# Mei v2.0.0

Mei 是本地优先的 agent memory 基础设施。memory/markdown/ 是唯一的
runtime canonical source of truth，也是可直接打开的 Obsidian vault root。
Markdown 正文保存 claim、evidence、history；YAML frontmatter 保存 scope、
provenance、status、关系和审计字段。

memory/legacy-json/ 只读用于 migration、audit、rollback、export。
memory/continuity/、trajectories/、memory/episodic/ 下的 JSON 是独立技术
协议，迁移不会触碰它们。

**版本:** v2.0.0 · **运行时 canonical:** Markdown/YAML

## What changed

| 主题 | v2.0.0 行为 |
| --- | --- |
| Canonical memory | 活动 Markdown note + YAML frontmatter；MEMORY.md 是 bounded generated index。 |
| Generated artifacts | registry/memory.yaml 和 MEMORY.md 由活动 note 生成，不是事实来源。 |
| Proposal | memory/proposals/<proposal-id>.md；支持 draft、previewed、approved、applied、stale、rejected、failed。 |
| Write boundary | P1 只读；P2 可直接新建低风险 note；已有 note 修改必须 proposal、preview、current hash、explicit approval、atomic apply、audit。 |
| Relations | 统一 writer 双端更新 reciprocal/typed inverse；drift 只能生成 repair proposal。 |
| Obsidian | memory/markdown/ 直接作为 vault root；旧 obsidian-vault/ 不再读取。 |
| Legacy JSON | 原字节保留在 memory/legacy-json/，不进入默认 retrieval，也不能被新 writer 修改。 |

## Flow

当前 Archify workflow v2 只交付 Light、2048×1320 的 JSON、HTML、SVG。

- [Light SVG](docs/architecture/memory-v2.workflow.svg)
- [Light interactive HTML](docs/architecture/memory-v2.workflow.html)
- [Editable workflow JSON](docs/architecture/memory-v2.workflow.json)

~~~mermaid
flowchart LR
    task[项目任务] --> index[MEMORY.md]
    index --> notes[活动 Markdown]
    notes --> context[选择性上下文]
    notes -. change request .-> proposal[Proposal .md]
    proposal --> preview[Preview + base hash]
    preview --> approval{明确批准}
    approval -->|通过| apply[Atomic apply]
    approval -->|stale / unresolved| reject[Reject]
    apply --> audit[Audit receipt]
    legacy[Legacy JSON] -. read-only scan .-> plan[Plan]
    plan --> migrate[JSON -> Markdown]
    migrate --> verify[Verify]
    verify -->|失败| rollback[Rollback]
    rollback -. remove created Markdown .-> migrate
~~~

读取只打开 index 和相关的活动 note；review_required、proposal、archive、
legacy evidence 不进入默认检索。已有 note 修改先绑定目标 SHA-256，hash 变化
就变为 stale，不覆盖并发修改。明确批准且 approved content hash 匹配后，apply
才会原子写入并记录 audit。

迁移先扫描重复 ID、非法字段、冲突和关系目标，再复制旧 JSON 原字节、写入
Markdown、生成 manifest 并 verify。rollback 只删除本次 manifest 创建的
Markdown，不删除 legacy JSON、continuity、trajectory 或 episodic 数据。

## Start here

需要 Python 3.11+。Archify 是开发文档工具，不是 runtime dependency；没有
Node 或 Obsidian GUI 时，CLI、doctor、search、proposal、relation、migration
仍可完整运行。

~~~powershell
python -m pip install -r requirements.txt
python scripts/init_local.py
python scripts/memory.py init --root memory/markdown
python scripts/memory.py doctor --root memory/markdown
~~~

检查 Git-ignored 的 local/paths.toml，确认 mei_root、memory_root、skills_root
和 obsidian_vault 指向当前机器的实际路径。

## Daily operations

~~~powershell
python scripts/memory.py read --root memory/markdown
python scripts/memory.py read --root memory/markdown --id <memory-id>
python scripts/memory.py search --root memory/markdown "关键词"
python scripts/memory.py doctor --root memory/markdown
~~~

read/search 只面向活动、可检索 Markdown，不读取 legacy JSON、proposals 或技术
协议 JSON。

P2/P3 可创建经过人工检查的低风险 preferences、project、feedback note：

~~~powershell
python scripts/memory.py write --root memory/markdown --path preferences/<memory-id>.md --content-file <reviewed-note.md> --reviewed-personal --capability P2
~~~

已有 note 修改统一走 proposal；CLI 和 Obsidian 卡片调用同一个 apply handler：

~~~powershell
python scripts/memory.py propose --root memory/markdown --target-type personal_markdown --target <relative-note-path> --content-file <proposed-note.md> --scope <scope> --source "human review" --reason "具体原因"
python scripts/review_proposals.py preview --proposal-id <proposal-id>
python scripts/review_proposals.py status --proposal-id <proposal-id>
python scripts/memory.py apply <proposal-id> --root memory/markdown --confirm --capability P3
~~~

Obsidian checkbox 只表达 approval intent，不能单独授权。apply 会再次检查
target base hash 和 approved content hash，成功后重建 index 并写 audit。

### Relations

~~~powershell
python scripts/memory.py relations add <memory-a> <memory-b> --type related --capability P3
python scripts/memory.py relations add <new-id> <old-id> --type supersedes --capability P3
python scripts/memory.py relations remove <memory-a> <memory-b> --type related --capability P3
python scripts/memory.py relations check --root memory/markdown
python scripts/memory.py relations repair --root memory/markdown --proposal
~~~

check 只读检查 dangling target、missing reciprocal、duplicate edge 和非法 type。
允许的 typed inverse 是 supersedes/superseded_by、depends_on/dependency_of、
supports/supported_by、contradicts/contradicted_by。

### Migration

~~~powershell
python scripts/migrate_memory.py plan --root memory/markdown
python scripts/migrate_memory.py migrate --root memory/markdown
python scripts/migrate_memory.py verify --root memory/markdown
python scripts/migrate_memory.py rollback --root memory/markdown
~~~

迁移范围是 memory/candidates/、memory/canonical/、memory/archive/；不处理
memory/continuity/、trajectories/、memory/episodic/ 或 eval fixtures。
manifest 在 memory/migration/manifest.yaml，报告在 memory/migration/reports/。
migrate 可重复执行且不覆盖人工修改；正式生产迁移保留 maintainer 人工验收。

## Configuration and Obsidian

机器配置只放在 Git-ignored 的 local/paths.toml，公共模板是
local/paths.example.toml。关键字段：

| 字段 | 用途 |
| --- | --- |
| paths.mei_root | 当前 Mei checkout。 |
| paths.memory_root | runtime canonical root，默认 memory/markdown。 |
| paths.skills_root | 用户 Codex skills 目录。 |
| paths.obsidian_vault | 直接指向 memory/markdown。 |
| host.memory_injection | host memory 注入开关。 |

~~~powershell
python scripts/sync_obsidian.py check --memory-root memory/markdown
python scripts/sync_obsidian.py sync --memory-root memory/markdown --source-generation manual
python scripts/check_obsidian.py --memory-root memory/markdown
python scripts/sync_obsidian.py uri --memory-root memory/markdown --note views/<memory-id>.md
~~~

.obsidian/ 只提供可选模板和 vault 设置。views/、proposal cards、drift 报告是
投影/审计数据，不是第二份 canonical note。

## Storage and recovery

| 路径 | 角色 | 默认 retrieval |
| --- | --- | --- |
| memory/markdown/MEMORY.md | bounded generated index | 入口 |
| memory/markdown/{profile,preferences,domain,procedural,project,feedback} | canonical notes | active 且已验证 |
| memory/markdown/proposals/ | 未批准 proposal | 否 |
| memory/legacy-json/ | byte-preserved evidence | 否，只读 |
| memory/continuity/、trajectories/、memory/episodic/ | 独立技术协议 | 不迁移 |
| registry/memory.yaml | generated registry | 需从 canonical 重建 |

不要直接编辑 generated index；先修 canonical note，再运行：

~~~powershell
python scripts/rebuild_registry.py
python scripts/memory.py doctor --root memory/markdown
python scripts/validate_repository.py
~~~

## Testing and verification

~~~powershell
python -m unittest discover -s tests -v
python scripts/validate_repository.py
python scripts/check_public_git.py
git diff --check
~~~

覆盖 YAML duplicate-key rejection、v0.2 schema、Markdown 读写/检索、migration
idempotency/verify/rollback、legacy exclusion、proposal stale/approval/apply、
P1/P2/P3、双向关系、direct-root Obsidian，以及 continuity/trajectory/episodic
不受迁移影响。

Archify 校验、交付、浏览器证据：

~~~powershell
Push-Location $env:USERPROFILE\.agents\skills\engineering\archify
node bin/archify.mjs validate workflow "S:\Agent\Mei\docs\architecture\memory-v2.workflow.json" --quality standard --json
node bin/archify.mjs deliver workflow "S:\Agent\Mei\docs\architecture\memory-v2.workflow.json" "S:\Agent\Mei\docs\architecture\memory-v2.workflow.html" --quality standard --json
Pop-Location
node scripts/light_architecture.mjs
node scripts/light_architecture.mjs --check
Push-Location $env:USERPROFILE\.agents\skills\engineering\archify
node bin/archify.mjs visual-check "S:\Agent\Mei\docs\architecture\memory-v2.workflow.html" --json
Pop-Location
~~~

validate/deliver 检查 SVG、路由、交叉、关系走廊、边界和 composition；
visual-check 另外检查真实 Chrome containment、viewer chrome 和截图。Chrome
DevTools 启动失败时保留 receipt 并报告环境未完成，不能把 delivery receipt
当成浏览器或人工视觉验收。

### Light-only artifact

最终只保留 Light 文件，不保留 dark artifact 或其他尺寸：

~~~text
docs/architecture/memory-v2.workflow.json
docs/architecture/memory-v2.workflow.html
docs/architecture/memory-v2.workflow.svg
~~~

HTML/SVG 应带 data-frag-archify-light="true" marker、Light theme、单一
2048×1320 viewBox，并隐藏主题切换入口。Archify 按 MIT license 分发，但不是
Mei runtime dependency。

## Capability boundaries

P1/P2/P3 是能力契约，不等于文件系统管理员权限：

| 能力 | P1 | P2 | P3 |
| --- | ---: | ---: | ---: |
| 读取 active Markdown、搜索、关系遍历 | ✓ | ✓ | ✓ |
| 生成 proposal/draft | ✓ | ✓ | ✓ |
| 新建低风险 note | - | ✓ | ✓ |
| 修改已有 note | - | - | explicit approval |
| profile/domain/procedural、关系、归档/覆盖/删除 | - | proposal | approval 后 |
| schema/policy/permission | - | - | - |

普通项目使用 mei-project：Mei 只读，最多写一个合规的 trajectories/inbox 轨迹。
migration、registry rebuild 和 canonical 修改使用明确范围的 mei-maintainer；
session 名称和 README 都不是 ACL 证明。

## Repository layout

~~~text
memory/markdown/       canonical Markdown/YAML + direct-root Obsidian vault
memory/legacy-json/    byte-preserved legacy evidence
memory/continuity/     continuity technical protocol
memory/migration/      manifest and reports
registry/              generated indexes and public examples
schemas/               note, proposal, trajectory and continuity schemas
scripts/               unified CLI, migration, relation, sync and validation
policies/              memory, promotion, permission and project policies
docs/architecture/     current Light flowchart artifacts
tests/                 offline regression and integration tests
~~~

具体 memory、continuity、trajectory、logs、host paths 和本机 Obsidian 状态默认不
进入公共 Git。不要批量删除历史记录；归档或 rollback 必须由 manifest、hash 和
audit receipt 限定范围。
