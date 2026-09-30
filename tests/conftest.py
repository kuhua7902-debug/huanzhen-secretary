"""幻帧测试套件共享夹具。

设计原则（很重要，改动前请先读）：
1. **绝不允许测试触碰真实用户数据**：``data/keji.db``、``data/workspace``、
   ``sessions/`` 都是真人数据。任何落盘操作都必须走 ``tmp_path``，
   任何工作区/数据库路径都必须被 monkeypatch 重定向。
2. **绝不发起真实网络请求**：被测代码里凡是会连 Ollama(11434) / 外部 API 的
   路径，测试一律绕开或 skip；需要重依赖（chromadb / easyocr / pywin32）
   时用 ``pytest.importorskip``。
3. **绝不读取 .env 里的真实密钥**：JWT 测试用 ``monkeypatch.setenv`` 注入
   固定密钥，密码测试用临时明文，不读取任何现有配置值。
4. **导入副作用**：``core.gui_tools`` / ``core.tools`` / ``core.new_tools``
   会在导入时注册全局工具、设置 DPI 感知、拉起 chromadb 客户端。
   因此这类导入只放在测试函数内部（或惰性 fixture 里），不做模块级导入。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── 保证仓库根目录可导入（pytest.ini 的 pythonpath=. 之外的兜底）──
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _isolated_request_context():
    """每个用例前后清空请求上下文。

    风险：``core.security.context`` 是 ContextVar 单例，``resolve_current_user()``
    依赖它。若某个用例（或产品代码）设置了 user_id/role 而没清理，
    后续用例会以"管理员/只读账号"的身份运行 —— 权限测试会静默失效。
    """
    from core.security.context import clear_request_context

    clear_request_context()
    yield
    clear_request_context()


@pytest.fixture
def temp_workspace(tmp_path, monkeypatch):
    """把团队工作区根目录重定向到 tmp_path，并建好 shared/users 骨架。

    风险：``core.workspace`` 的根目录是模块级常量 ``_PROJECT_ROOT``，
    指向真实仓库（真实 ``data/workspace`` 里有真人文件/用户目录）。
    重定向后才能安全地测 user_dir / assert_access / can_write 的读写语义。
    """
    import core.workspace as ws

    monkeypatch.setattr(ws, "_PROJECT_ROOT", tmp_path, raising=True)
    ws.ensure_layout()
    return ws


@pytest.fixture
def workspace_files(temp_workspace):
    """在临时工作区里铺好：自己的文件 / 别人的文件 / 共享文件 / 工作区外的文件。"""
    ws = temp_workspace
    ws.ensure_user_dir("m1")
    ws.ensure_user_dir("m2")

    mine_dir = ws.user_dir("m1")
    other_dir = ws.user_dir("m2")
    mine = mine_dir / "mine.txt"
    other = other_dir / "other.txt"
    shared = ws.shared_dir() / "shared.txt"
    for p, text in ((mine, "mine"), (other, "other"), (shared, "shared")):
        p.write_text(text, encoding="utf-8")

    # tmp_path 是 workspace_root() 的上两级（tmp_path/data/workspace），天然在工作区之外
    outside = ws.workspace_root().parent.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    return {
        "ws": ws,
        "root": ws.workspace_root(),
        "shared_dir": ws.shared_dir(),
        "users_root": ws.users_root(),
        "mine_dir": mine_dir,
        "other_dir": other_dir,
        "mine": mine,
        "other": other,
        "shared": shared,
        "outside": outside,
    }


@pytest.fixture
def tmp_db(tmp_path):
    """临时 SQLite 库（绝不复用 ``data/keji.db``）。"""
    from core.database.db import Database

    return Database(str(tmp_path / "keji_test.db"))


@pytest.fixture
def sandbox_config():
    """hermetic 的 path_policy 配置。

    - ``workspace.enabled = False``：避免 ``get_allowed_roots()`` 去 ``ensure_layout()``
      在真实仓库里建目录（那是唯一会写出 tmp_path 的地方）。
    - 允许目录都是 tmp_path 下的相对路径，由被测代码自行 mkdir。
    """
    return {
        "security": {"filesystem_sandbox": True},
        "workspace": {"enabled": False},
        "mcp": {"filesystem_allowed_dirs": ["knowledge"], "include_knowledge": True,
                "include_data": True},
    }
