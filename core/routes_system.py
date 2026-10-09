"""系统状态与数据备份 API（管理页「系统状态」）。

## 为什么需要

前几轮把「环境自检」和「数据备份」做成了命令行脚本（`诊断.bat` / `备份.bat`）。
但那意味着：**管理员必须能登录到那台机器、会开命令行**才能用。

把这两件事搬到网页后，管理员在浏览器里就能：
- 看清运行状态（版本 / Python / PID / 运行时长 / 磁盘）
- 看清数据规模（用户 / 对话 / 消息 / 审计条数）
- 一键跑环境自检（复用 `scripts/doctor.py`）
- 查看并立即创建数据备份（复用 `scripts/backup.py`）

命令行的 `诊断.bat` / `备份.bat` 仍然保留——那是在"服务都起不来"时的救命入口，
两者是互补关系，不是替代关系。

## 权限

全部端点要求 **admin**：诊断会暴露文件系统路径与配置细节，备份会写盘。
"""

from __future__ import annotations

import datetime
import os
import shutil
import sys
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from core.script_loader import load_script
from core.security.deps import require_admin
from core.security.users import CurrentUser
from core.version import APP_NAME, __version__

router = APIRouter(prefix="/api/system", tags=["system"])

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ────────────────────────────────────────────────────────────
# 采集函数（全部做了兜底：任何一项失败都不能让整个页面挂掉）
# ────────────────────────────────────────────────────────────

def _runtime() -> dict:
    info = {
        "app": APP_NAME,
        "version": __version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "pid": os.getpid(),
        "platform": f"{sys.platform}",
        "uptime_sec": 0,
    }
    try:
        import psutil  # noqa: PLC0415

        info["uptime_sec"] = int(time.time() - psutil.Process(os.getpid()).create_time())
    except Exception:
        pass
    return info


def _disk() -> dict:
    try:
        usage = shutil.disk_usage(ROOT)
        return {
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "total_gb": round(usage.total / (1024 ** 3), 1),
        }
    except Exception:
        return {}


def _db_stats() -> dict:
    mod = load_script("backup.py")
    path = mod.db_path() if mod else os.path.join(ROOT, "data", "huanzhen.db")
    out: dict = {"path": path, "exists": os.path.isfile(path), "size_mb": 0.0}
    if not out["exists"]:
        return out
    try:
        out["size_mb"] = round(os.path.getsize(path) / 1048576, 2)
    except OSError:
        return out

    try:
        import sqlite3  # noqa: PLC0415

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            for key, table in (
                ("users", "users"),
                ("conversations", "conversations"),
                ("messages", "messages"),
                ("audit_events", "audit_events"),
            ):
                try:
                    out[key] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except sqlite3.DatabaseError:
                    out[key] = None
        finally:
            conn.close()
    except Exception:
        pass
    return out


def _tools() -> dict:
    builtin = 0
    try:
        from core.tools import _tool_registry  # noqa: PLC0415

        builtin = len(_tool_registry)
    except Exception:
        pass
    return {"builtin": builtin, "mcp": None}


async def _mcp_tool_count() -> int | None:
    """MCP 工具数量（adapter 未就绪时返回 None，不阻塞页面）。"""
    try:
        from nanobot.adapter import get_adapter  # noqa: PLC0415

        adapter = await get_adapter()
        names = list(getattr(adapter.tools, "_tools", {}) or {})
        return sum(1 for n in names if str(n).startswith("mcp_"))
    except Exception:
        return None


def _checks() -> list[dict]:
    """复用 scripts/doctor.py 的检查项（慢，仅在 deep=1 时调用）。"""
    mod = load_script("doctor.py")
    if mod is None:
        return [{
            "level": "warn", "group": "系统",
            "title": "自检脚本不可用",
            "detail": "未找到 scripts/doctor.py",
            "fix": "确认项目文件完整。",
        }]
    try:
        report = mod.run_all(fix=False)
        return [
            {"level": r.level, "title": r.title, "detail": r.detail,
             "fix": r.fix, "group": r.group}
            for r in report.results
        ]
    except Exception as e:
        return [{
            "level": "warn", "group": "系统",
            "title": "自检执行失败", "detail": f"{type(e).__name__}: {e}", "fix": "",
        }]


def _backup_items() -> list[dict]:
    mod = load_script("backup.py")
    if mod is None:
        return []
    items: list[dict] = []
    for path in mod.list_backups():
        try:
            stat = os.stat(path)
        except OSError:
            continue
        items.append({
            "name": os.path.basename(path),
            "size_mb": round(stat.st_size / 1048576, 2),
            "created_at": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return items


# ────────────────────────────────────────────────────────────
# 端点
# ────────────────────────────────────────────────────────────

@router.get("/diagnostics")
async def diagnostics(
    _admin: CurrentUser = Depends(require_admin),
    deep: int = Query(0, ge=0, le=1, description="1=运行完整环境自检（较慢）"),
):
    """系统状态汇总。deep=1 时附带完整环境自检结果。"""
    tools = _tools()
    tools["mcp"] = await _mcp_tool_count()

    payload = {
        "runtime": _runtime(),
        "database": _db_stats(),
        "tools": tools,
        "disk": _disk(),
        "backups": _backup_items(),
        "checks": _checks() if deep else [],
    }
    return payload


def _retention_report(dry_run: bool) -> dict:
    mod = load_script("retention.py")
    if mod is None:
        raise HTTPException(500, "清理脚本不可用（未找到 scripts/retention.py）")

    # 网页触发时服务正在运行：拿审计写入锁，避免归档期间新记录被覆盖丢失
    exclusive = None
    try:
        from core.security.audit import get_audit_logger  # noqa: PLC0415

        exclusive = get_audit_logger().exclusive
    except Exception:
        exclusive = None

    try:
        return mod.apply_retention(dry_run=dry_run, exclusive=exclusive)
    except Exception as e:
        raise HTTPException(500, f"清理失败：{type(e).__name__}: {e}")


@router.get("/retention/preview")
def retention_preview(_admin: CurrentUser = Depends(require_admin)):
    """预演：只统计会被归档/清理多少，不改动任何文件。"""
    return _retention_report(dry_run=True)


@router.post("/retention")
def run_retention(_admin: CurrentUser = Depends(require_admin)):
    """执行保留策略：归档旧审计、归档旧会话、清理过期归档与数据库审计。"""
    report = _retention_report(dry_run=False)
    audit = report.get("audit", {})
    return {
        "status": "ok",
        "message": (
            f"归档审计 {audit.get('archived_lines', 0)} 行、"
            f"清理归档 {len(report.get('archives', {}).get('removed', []))} 个、"
            f"归档会话 {report.get('sessions', {}).get('archived', 0)} 个、"
            f"删除数据库审计 {report.get('database', {}).get('deleted', 0)} 行"
        ),
        "report": report,
    }


@router.get("/backups")
def list_backups(_admin: CurrentUser = Depends(require_admin)):
    items = _backup_items()
    mod = load_script("backup.py")
    return {
        "backups": items,
        "dir": getattr(mod, "backup_dir", lambda: "")() if mod else "",
    }


@router.post("/backup")
def create_backup_now(_admin: CurrentUser = Depends(require_admin)):
    """立即创建一次数据备份（等价于双击 备份.bat）。"""
    mod = load_script("backup.py")
    if mod is None:
        raise HTTPException(500, "备份脚本不可用（未找到 scripts/backup.py）")
    try:
        path = mod.create_backup()
    except FileNotFoundError as e:
        raise HTTPException(400, f"无法备份：{e}")
    except Exception as e:
        raise HTTPException(500, f"备份失败：{type(e).__name__}: {e}")

    try:
        size_mb = round(os.path.getsize(path) / 1048576, 2)
    except OSError:
        size_mb = 0.0
    return {
        "status": "ok",
        "message": "备份完成",
        "name": os.path.basename(path),
        "size_mb": size_mb,
        "backups": _backup_items(),
    }
