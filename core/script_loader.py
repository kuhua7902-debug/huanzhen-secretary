"""按路径加载 `scripts/` 下的脚本模块。

## 为什么需要

`scripts/` 不是 Python 包（没有 `__init__.py`），直接 `import backup` 会污染
`sys.path` 且有重名风险（比如撞上第三方库的 `backup` 模块）。

而我们的目标是**「命令行能做的，网页也能做」**——网页端需要复用
`scripts/backup.py`（数据备份）、`scripts/doctor.py`（环境自检）里的能力，
因此这里用 importlib 按**文件路径**加载并缓存，作为服务端与脚本之间的唯一桥梁。

加载失败一律返回 `None`：调用方把它当作「该能力不可用」，而不是让整个请求 500。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any

_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
_CACHE: dict[str, Any] = {}


def scripts_dir() -> str:
    return _SCRIPTS_DIR


def load_script(filename: str) -> Any | None:
    """加载 `scripts/<filename>` 模块；不存在或加载失败返回 None。"""
    if filename in _CACHE:
        return _CACHE[filename]
    path = os.path.join(_SCRIPTS_DIR, filename)
    if not os.path.isfile(path):
        return None
    try:
        module_name = "huanzhen_script_" + os.path.splitext(os.path.basename(filename))[0]
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        _CACHE[filename] = module
        return module
    except Exception:
        return None
