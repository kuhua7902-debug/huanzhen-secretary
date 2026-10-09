"""产品版本号（单一来源）。

网页「系统状态」、`/health`、自检报告都从这里取，避免各处写死不一致。
发版时只改这一处，并同步 CHANGELOG。
"""

from __future__ import annotations

__version__ = "1.1.0"

# 展示用名称
APP_NAME = "幻帧 AI 智能秘书"
