# P3.4 项目归属自动化（按工作目录推断）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地设计文档 9.2 节的第 2 级归属——从会话 `cwd` 向上查找 `.git`，把仓库根作为项目边界，
让零配置下 `/api/projects` 也能返回真实项目，而不是把全部会话都丢进「未归类」。

**Architecture:** 只改 `storage.resolve_project()` 的判定链与调用它的 `refresh_session_projects()`，
**不改 schema**：`sessions.project` 已经是 TEXT 列，自动推断出来的项目名只是不再走
`projects` / `project_paths` 两张手动映射表（设置页会以「有会话数、无前缀」的形态展示它们）。
判定链变成：手动映射（最长前缀）> 自动推断（`cwd` 向上找 `.git`）> 未归类。

**Tech Stack:** Python 3.12、SQLite、FastAPI、pytest、Vue 3 + Element Plus、uv、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-09-24-agent-lens-design.md`（9.2 项目归属）

**Depends on:** P1.2 的 `resolve_project` / `refresh_session_projects`、P1.3 采集器（写入时定归属）。

## Global Constraints

- 事实源只读：推断只做 `os.stat` 级别的存在性检查，**绝不读写** `~/.codex/sessions` 下的任何文件。
- 正文不入库、UTC aware、标识符英文 / 注释中文。
- **零 schema 变更**：不新增表、不新增列、不改 SCHEMA_VERSION。
- 手动映射优先级不变：任何有映射命中的 `cwd` 都不进入自动推断。
- 推断必须可解释：同一份 `cwd` 在写入时与重算时得到同一个答案（纯函数，不依赖调用顺序）。

## 已完成的先期验证（2026-09-30，真实数据）

真实库（`agent-lens-web-1` 容器里 `/data/agent-lens.db`）当前 12 个会话、`project_paths` 0 行、
`projects` 表 0 行，所以 12 个会话全部是「未归类」。7 个不同 `cwd`：

| `cwd` | 会话数 | 磁盘现状 | 本计划落地后的归属 |
|---|---|---|---|
| `/Users/wzy/agent-lens` | 4 | 已不存在（目录搬到 `~/projects/agent-lens`） | 未归类（陈旧路径，留给手动映射） |
| `/Users/wzy/godot_projects` | 2 | 已不存在（搬到 `~/projects/godot_projects`） | 未归类（同上） |
| `/Users/wzy` | 2 | 存在，但不是仓库 | 未归类（主目录不作为仓库根候选） |
| `/Users/wzy/projects/godot_projects` | 1 | 存在，元目录，本身不是仓库 | 未归类 |
| `/Users/wzy/projects/godot_projects/DSR_function_demo` | 1 | 存在，git 根 | `DSR_function_demo` |
| `/Users/wzy/projects/godot_projects/eros` | 1 | 存在，git 根 | `eros` |
| `/Users/wzy/projects/agent-lens` | 1 | 存在，git 根 | `agent-lens` |

**验收预期**：12 个会话里 3 个变成真实项目（`agent-lens` / `eros` / `DSR_function_demo`），
其余 9 个留在「未归类」。
`/Users/wzy/agent-lens` 这类陈旧路径按本轮决定（方案 A）不做别名兜底，由设置页手动映射解决。

## 关键裁决（本轮控制器做出，落 ledger）

1. **只认磁盘上真实存在的路径**：`cwd` 不存在（目录已改名/删除）时直接返回未归类，不做 basename 兜底。
   理由：basename 兜底会引入「同名目录谁赢」的新语义，属于设计文档没承诺的规则。
2. **向上查找到文件系统根为止，但用户主目录不作为仓库根候选**。
   理由：dotfiles 仓库（`~/.git`）一旦被当成仓库根，会吞掉主目录下所有没有自己仓库的会话
   （`~/projects` 这种元目录首当其冲），那比「未归类」更糟。
3. **项目名 = 仓库根目录名**。同名仓库会合并成一个项目名——这与手动映射的命名口径一致，
   代价写进文档而不是偷偷加去重后缀。
4. **容器可见性沿用已有挂载**：`docker-compose.yml` 已把 `${PROJECTS_ROOT:-$HOME/projects}` 以
   「宿主路径 == 容器路径」只读挂进 collector 与 web（本轮之前的未提交改动，随本计划一起提交）。
   挂在白名单之外的目录在容器里不可见，那些 `cwd` 自然落到「未归类」——README 已写明。

---

## Task 1: 存储层自动推断 + 重算

**Files:**
- Modify: `src/agent_lens/storage.py`
- Test: `tests/test_storage_projects.py`

**Interfaces:**

- Produces:
  - `storage._git_root(cwd: Path, *, home: Path | None = None) -> Path | None`
  - `storage._home_dir() -> Path`（可被测试 monkeypatch）
  - `storage.resolve_project(conn, cwd, *, fallback=UNCLASSIFIED_PROJECT) -> str`
    行为扩展：手动映射未命中时，若 `cwd` 存在且能向上找到 `.git`，返回仓库根目录名。
  - `storage.refresh_session_projects(conn, *, now=None) -> int` 行为不变，内部按 `cwd` 去重，
    避免同一目录被反复走文件系统。

- Consumes: 既有 `project_paths` 表、`UNCLASSIFIED_PROJECT`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_storage_projects.py` 追加（`tmp_path` 造真实目录，`repo/.git` 用目录或文件都算）：

```python
def _make_repo(base: Path, name: str, *, git_file: bool = False) -> Path:
    repo = base / name
    repo.mkdir(parents=True)
    if git_file:
        (repo / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    else:
        (repo / ".git").mkdir()
    return repo


def test_cwd_inside_a_repo_infers_repo_root_name(lens_db, tmp_path):
    repo = _make_repo(tmp_path, "agent-lens")
    nested = repo / "src" / "agent_lens"
    nested.mkdir(parents=True)

    assert resolve_project(lens_db, str(repo)) == "agent-lens"
    assert resolve_project(lens_db, str(nested)) == "agent-lens"


def test_git_file_also_marks_a_repo_root(lens_db, tmp_path):
    repo = _make_repo(tmp_path, "worktree-clone", git_file=True)
    assert resolve_project(lens_db, str(repo)) == "worktree-clone"


def test_meta_directory_without_repo_stays_unclassified(lens_db, tmp_path):
    meta = tmp_path / "godot_projects"
    child = meta / "placeholder"
    child.mkdir(parents=True)
    assert resolve_project(lens_db, str(meta)) == UNCLASSIFIED_PROJECT
    assert resolve_project(lens_db, str(child)) == UNCLASSIFIED_PROJECT


def test_stale_cwd_is_not_rescued_by_basename(lens_db, tmp_path):
    # 目录已改名：旧路径不存在，即使同名的仓库就在旁边也不猜。
    _make_repo(tmp_path, "agent-lens")
    assert resolve_project(lens_db, str(tmp_path / "moved-away")) == UNCLASSIFIED_PROJECT


def test_manual_mapping_beats_auto_inference(lens_db, tmp_path):
    repo = _make_repo(tmp_path, "agent-lens")
    assign_project(lens_db, str(repo / "vendor"), "vendor-lib")

    assert resolve_project(lens_db, str(repo / "vendor" / "lib")) == "vendor-lib"
    assert resolve_project(lens_db, str(repo / "src")) == "agent-lens"


def test_home_directory_is_never_a_repo_root(lens_db, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".git").mkdir(parents=True)
    stray = home / "projects" / "godot_projects"
    stray.mkdir(parents=True)
    monkeypatch.setattr(storage, "_home_dir", lambda: home)

    assert resolve_project(lens_db, str(stray)) == UNCLASSIFIED_PROJECT
    assert resolve_project(lens_db, str(home)) == UNCLASSIFIED_PROJECT


def test_refresh_recomputes_inferred_projects(lens_db, tmp_path):
    repo = _make_repo(tmp_path, "agent-lens")
    write_parsed_session(lens_db, minimal_session(cwd=str(repo / "src")))

    assert lens_db.execute("SELECT project FROM sessions").fetchone()[0] == "agent-lens"
```

> `minimal_session` 目前不接受 `cwd` 参数（`tests/test_storage_write.py`）。执行者给它加一个
> `cwd: str | None = None` 的可选参数（默认值保持现状），**不要**改动既有调用点。

- [ ] **Step 2: 跑测试确认失败**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_storage_projects.py -v`
Expected: 新增用例 FAIL（`AttributeError: ... has no attribute '_home_dir'` 或返回「未归类」而非仓库名）。

- [ ] **Step 3: 写最小实现**

```python
def _home_dir() -> Path:
    return Path.home()


def _git_root(cwd: Path, *, home: Path | None = None) -> Path | None:
    """从 cwd 向上找 `.git`，返回仓库根；没有仓库返回 None。

    边界：只认磁盘上真实存在的目录（陈旧 cwd 一律 None）；走到文件系统根为止；
    用户主目录不作为仓库根候选——dotfiles 仓库不该吞掉主目录下的元目录。
    `.git` 是目录（普通克隆）或文件（worktree / submodule）都算。
    """
    if not cwd.is_dir():
        return None
    stop = home or _home_dir()
    current = cwd
    while True:
        if current != stop and (current / ".git").exists():
            return current
        parent = current.parent
        if current == stop or parent == current:
            return None
        current = parent
```

`resolve_project` 在手动映射查空之后、返回 `fallback` 之前插入：

```python
    root = _git_root(Path(cwd))
    return root.name if root else fallback
```

`refresh_session_projects` 按 `cwd` 记忆化，避免 12 行会话把同一目录走 12 遍：

```python
    cache: dict[str | None, str] = {}
    for row in rows:
        if row["cwd"] not in cache:
            cache[row["cwd"]] = resolve_project(conn, row["cwd"])
        project = cache[row["cwd"]]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_storage_projects.py tests/test_storage_write.py tests/test_api_endpoints.py -v`
Expected: 全绿。既有用例（`/Users/someone/...` 这类不存在的路径）不受影响。

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/storage.py tests/test_storage_projects.py tests/test_storage_write.py
git commit -m "feat: infer project from the git root above a session cwd"
```

---

## Task 2: 设置页文案与文档对齐

**Files:**
- Modify: `web/src/views/SettingsView.vue`
- Modify: `docs/superpowers/specs/2026-09-24-agent-lens-design.md`（9.2 进度注记）
- Modify: `docs/superpowers/plans/README.md`（P3.4 状态）
- Modify: `docs/stories.md`（「诚实的边界」里那条已不成立）

**Interfaces:** 无代码接口变化，只有文案与文档状态。

- [ ] **Step 1: 设置页文案**

`:empty-text` 从「还没有映射，全部会话会归到「未归类」」改为
「还没有手动映射；有 `.git` 的工作目录会自动归属，其余归入「未归类」」；
「项目映射」标题右侧的说明补一句三档优先级（手动映射 > 自动推断 > 未归类）。

- [ ] **Step 2: 文档**

- 设计文档 9.2 的实现进度注记改成「三级均已落地（第 2 级 2026-09-30 落地）」，并写明
  陈旧路径与主目录边界两条规则；
- `plans/README.md` 的 P3.4 行标记为已完成，指向本计划；
- `docs/stories.md` 的「诚实的边界」把「项目归属目前只有手动路径映射」改成
  「陈旧 `cwd`（目录已改名）与元目录仍会落到未归类，需要手动映射」。

- [ ] **Step 3: 构建**

```bash
cd web && npm run build
```
Expected: 构建成功。

- [ ] **Step 4: 提交**

```bash
git add web/src docs README.md docker-compose.yml
git commit -m "docs: record the second-level project attribution rules"
```

> `README.md` / `docker-compose.yml` 里「把 `~/projects` 同路径只读挂进容器」的未提交改动
> 属于本计划的第 4 条裁决，随这一步一起提交。

---

## Task 3: 真实库重算与验收

**Files:** 无（只跑命令、验数据）

- [ ] **Step 1: 重建镜像并重启**

```bash
docker compose up -d --build
```

- [ ] **Step 2: 重算归属**

```bash
curl -sS -X POST http://127.0.0.1:8000/api/settings/projects/refresh
```
Expected: `{"sessions_reassigned": 2}`（只有两个 `~/projects/...` 下的 git 仓库命中）。

- [ ] **Step 3: 验收 API 与页面**

```bash
curl -sS http://127.0.0.1:8000/api/projects
BASE=http://127.0.0.1:8000 node scripts/nav-smoke.mjs
```
Expected: `/api/projects` 返回 ≥ 2 个项目（`agent-lens`、`DSR_function_demo`）且会话总数仍为 12；
导航探针全过、0 browser error。

**验收结果（2026-09-30，实际执行）**：

- `POST /api/settings/projects/refresh` → `{"sessions_reassigned": 3}`（比先期预期的 2 多一个
  `eros`——先期那张表把 `~/projects/godot_projects` 这个元目录写了两遍，漏了它的子仓库 `eros`，
  已更正）；再调用一次返回 0，说明重算幂等。
- `/api/projects`：`agent-lens` 1 个会话、`eros` 1 个、`DSR_function_demo` 1 个、「未归类」9 个，
  合计仍是 12 个会话——零配置下不再全空，且没有丢会话。
- `scripts/nav-smoke.mjs`：8 步导航全过，0 browser error。

---

## Self-Review（控制器已核对）

- **Spec 覆盖**：9.2 的三级优先级 → Task 1 把第 2 级插进 `resolve_project` 的判定链中间；
  9.2 末尾的实现进度注记 → Task 2 更新。
- **零 schema 变更**：自动推断只写 `sessions.project`，不碰 `projects` / `project_paths`。
- **已记录的风险**：同名仓库合并为一个项目名；白名单外的目录在容器内不可见 → 两者都写进文档，
  不做「看起来更聪明」的兜底。
