from pathlib import Path

from agent_lens import storage
from agent_lens.storage import (
    UNCLASSIFIED_PROJECT,
    assign_project,
    refresh_session_projects,
    resolve_project,
    write_parsed_session,
)
from tests.test_storage_write import minimal_session


def _make_repo(base: Path, name: str, *, git_file: bool = False) -> Path:
    """造一个真实存在的仓库目录；`.git` 用目录（普通克隆）或文件（worktree）都算。"""
    repo = base / name
    repo.mkdir(parents=True)
    if git_file:
        (repo / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    else:
        (repo / ".git").mkdir()
    return repo


def test_longest_prefix_wins_and_falls_back(lens_db):
    assign_project(lens_db, "/Users/someone/project", "agent-lens")
    assign_project(lens_db, "/Users/someone/project/vendor", "vendor-lib")

    assert resolve_project(lens_db, "/Users/someone/project/app") == "agent-lens"
    assert resolve_project(lens_db, "/Users/someone/project/vendor/lib") == "vendor-lib"
    assert resolve_project(lens_db, "/Users/someone/other") == UNCLASSIFIED_PROJECT
    assert resolve_project(lens_db, None) == UNCLASSIFIED_PROJECT


def test_prefix_matching_ignores_like_wildcards(lens_db):
    assign_project(lens_db, "/Users/someone/pro_ject", "underscore-project")

    assert resolve_project(lens_db, "/Users/someone/proXject/app") == UNCLASSIFIED_PROJECT


def test_refresh_projects_repairs_existing_sessions(lens_db):
    write_parsed_session(lens_db, minimal_session())
    before = lens_db.execute("SELECT project FROM sessions").fetchone()[0]

    assign_project(lens_db, "/Users/someone/project", "agent-lens")
    changed = refresh_session_projects(lens_db)

    assert before == UNCLASSIFIED_PROJECT
    assert changed == 1
    assert lens_db.execute("SELECT project FROM sessions").fetchone()[0] == "agent-lens"


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
    # 目录已改名：旧路径不存在，即使旁边就有同名仓库也不猜（方案 A）。
    _make_repo(tmp_path, "agent-lens")

    assert resolve_project(lens_db, str(tmp_path / "moved-away")) == UNCLASSIFIED_PROJECT


def test_manual_mapping_beats_auto_inference(lens_db, tmp_path):
    repo = _make_repo(tmp_path, "agent-lens")
    (repo / "src").mkdir()
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
    repo = tmp_path / "agent-lens"
    nested = repo / "src"
    nested.mkdir(parents=True)
    write_parsed_session(lens_db, minimal_session(cwd=str(nested)))
    assert lens_db.execute("SELECT project FROM sessions").fetchone()[0] == UNCLASSIFIED_PROJECT

    # 仓库事后才初始化（或目录刚搬进来）：重算要能把历史会话补上归属。
    (repo / ".git").mkdir()
    changed = refresh_session_projects(lens_db)

    assert changed == 1
    assert lens_db.execute("SELECT project FROM sessions").fetchone()[0] == "agent-lens"
