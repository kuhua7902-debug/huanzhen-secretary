"""数据保留策略：审计日志 / 旧会话 / 旧备份的归档与清理。

## 为什么需要

幻帧是常驻服务，三类数据会随时间无限增长：

| 数据 | 增长源 | 长期后果 |
|---|---|---|
| `logs/audit.jsonl` | 每次工具调用与文件访问都追加一行 | 文件达到几十上百 MB，检索变慢 |
| `data/huanzhen.db` 的 `audit_events` 表 | 同上（同时写库） | 库膨胀，备份越来越慢 |
| `sessions/*.jsonl` | 每个会话一个文件，长期累积 | 目录臃肿，历史列表变慢 |

本脚本提供**归档优先、删除兜底**的保留策略：

- 审计日志：超过 `audit_days` 的记录**按月份压缩归档**到 `logs/archive/audit-YYYY-MM.jsonl.gz`，
  归档文件超过 `archive_days` 才真正删除
- 数据库审计：超过 `db_audit_days` 的行直接删除（明细在 jsonl 归档里仍可追溯）
- 旧会话：超过 `sessions_days` 未更新的会话文件压缩归档到 `sessions/archive/`
- **无法判定时间的记录一律保留**——宁可占空间，不可丢审计

服务启动时**每月自动执行一次**；也可从网页「设置 → 系统状态 → 立即清理」触发。

> ⚠️ 命令行执行时若服务正在运行，归档期间新写入的审计行存在极小概率丢失。
> 网页触发会用审计写入锁保证安全；命令行建议在服务停止时使用。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULTS = {
    "audit_days": 90,        # audit.jsonl 保留天数（更早的按月归档）
    "archive_days": 365,     # 归档文件保留天数
    "sessions_days": 180,    # 会话文件保留天数（更早的压缩归档）
    "db_audit_days": 180,    # 数据库 audit_events 保留天数
}

RETENTION_MARKER = ".last_retention"


# ────────────────────────────────────────────────────────────
# 路径解析（不依赖 core.*，服务起不来时也能清理）
# ────────────────────────────────────────────────────────────

def _load_config_dict() -> dict:
    try:
        import yaml  # noqa: PLC0415

        path = os.path.join(ROOT, "config.yaml")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _resolve(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)


def audit_log_path() -> str:
    rel = str((_load_config_dict().get("audit") or {}).get("file") or "logs/audit.jsonl")
    return _resolve(rel)


def db_path() -> str:
    rel = str((_load_config_dict().get("database") or {}).get("path") or "data/huanzhen.db")
    return _resolve(rel)


def sessions_dir() -> str:
    return os.path.join(ROOT, "sessions")


# ────────────────────────────────────────────────────────────
# 审计日志：按月归档
# ────────────────────────────────────────────────────────────

def _line_ts(line: str) -> float | None:
    """从一行审计 JSON 里取出时间戳；无法判定返回 None。"""
    try:
        obj = json.loads(line)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    for key in ("ts", "timestamp", "time", "created_at"):
        value = obj.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
    return None


def archive_audit_log(
    path: str,
    audit_days: int,
    dry_run: bool = False,
    exclusive=None,
) -> dict:
    """把超过 audit_days 的审计记录按月份归档，返回统计信息。"""
    result = {"path": path, "archived_lines": 0, "kept_lines": 0, "undated_lines": 0, "months": []}
    if not os.path.isfile(path):
        return result

    cutoff = time.time() - max(0, int(audit_days)) * 86400
    archive_dir = os.path.join(os.path.dirname(path) or ".", "archive")

    def _work() -> dict:
        old: dict[str, list[str]] = {}
        kept: list[str] = []
        undated: list[str] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.rstrip("\n")
                    if not line.strip():
                        continue
                    ts = _line_ts(line)
                    if ts is None:
                        undated.append(line)          # 判定不了时间 → 一律保留
                    elif ts < cutoff:
                        month = time.strftime("%Y-%m", time.localtime(ts))
                        old.setdefault(month, []).append(line)
                    else:
                        kept.append(line)
        except OSError:
            return result

        archived = sum(len(v) for v in old.values())
        result.update(
            archived_lines=archived,
            kept_lines=len(kept),
            undated_lines=len(undated),
            months=sorted(old),
        )
        if dry_run or archived == 0:
            return result

        os.makedirs(archive_dir, exist_ok=True)
        for month, lines in old.items():
            target = os.path.join(archive_dir, f"audit-{month}.jsonl.gz")
            with gzip.open(target, "at", encoding="utf-8") as gz:   # 追加为新 member
                for line in lines:
                    gz.write(line + "\n")

        # 原子替换：先写临时文件再 rename，避免中途失败导致日志损坏
        tmp = path + ".rotating"
        with open(tmp, "w", encoding="utf-8") as f:
            for line in kept:
                f.write(line + "\n")
            for line in undated:
                f.write(line + "\n")
        os.replace(tmp, path)
        return result

    if exclusive is not None:
        with exclusive():
            return _work()
    return _work()


# ────────────────────────────────────────────────────────────
# 归档文件清理
# ────────────────────────────────────────────────────────────

def prune_archives(archive_dir: str, archive_days: int, dry_run: bool = False) -> dict:
    result = {"dir": archive_dir, "removed": [], "freed_mb": 0.0}
    if not os.path.isdir(archive_dir):
        return result
    cutoff = time.time() - max(1, int(archive_days)) * 86400
    for name in sorted(os.listdir(archive_dir)):
        if not name.endswith(".gz"):
            continue
        path = os.path.join(archive_dir, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if stat.st_mtime >= cutoff:
            continue
        result["removed"].append(name)
        result["freed_mb"] += stat.st_size / 1048576
        if not dry_run:
            try:
                os.remove(path)
            except OSError:
                pass
    result["freed_mb"] = round(result["freed_mb"], 3)
    return result


# ────────────────────────────────────────────────────────────
# 旧会话归档
# ────────────────────────────────────────────────────────────

def archive_sessions(sessions_days: int, dry_run: bool = False) -> dict:
    src_dir = sessions_dir()
    archive_dir = os.path.join(src_dir, "archive")
    result = {"dir": src_dir, "archived": 0, "freed_mb": 0.0}
    if not os.path.isdir(src_dir):
        return result

    cutoff = time.time() - max(1, int(sessions_days)) * 86400
    for name in sorted(os.listdir(src_dir)):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(src_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if stat.st_mtime >= cutoff:
            continue

        result["archived"] += 1
        result["freed_mb"] += stat.st_size / 1048576
        if dry_run:
            continue

        os.makedirs(archive_dir, exist_ok=True)
        target = os.path.join(archive_dir, name + ".gz")
        try:
            with open(path, "rb") as fin, gzip.open(target, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            os.remove(path)
        except OSError:
            pass

    result["freed_mb"] = round(result["freed_mb"], 3)
    return result


# ────────────────────────────────────────────────────────────
# 数据库审计清理
# ────────────────────────────────────────────────────────────

def prune_db_audit(db_audit_days: int, dry_run: bool = False) -> dict:
    path = db_path()
    result = {"path": path, "deleted": 0, "skipped": ""}
    if not os.path.isfile(path):
        result["skipped"] = "数据库不存在"
        return result
    cutoff = time.time() - max(1, int(db_audit_days)) * 86400
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE created_at < ?", (cutoff,)
            ).fetchone()[0]
            result["deleted"] = int(count)
            if not dry_run and count:
                conn.execute("DELETE FROM audit_events WHERE created_at < ?", (cutoff,))
                conn.commit()
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        result["skipped"] = f"数据库错误：{e}"
    except Exception as e:
        result["skipped"] = f"{type(e).__name__}: {e}"
    return result


# ────────────────────────────────────────────────────────────
# 编排
# ────────────────────────────────────────────────────────────

def apply_retention(
    dry_run: bool = False,
    audit_days: int | None = None,
    archive_days: int | None = None,
    sessions_days: int | None = None,
    db_audit_days: int | None = None,
    exclusive=None,
) -> dict:
    """执行全部保留策略，返回分项报告。"""
    audit_days = DEFAULTS["audit_days"] if audit_days is None else audit_days
    archive_days = DEFAULTS["archive_days"] if archive_days is None else archive_days
    sessions_days = DEFAULTS["sessions_days"] if sessions_days is None else sessions_days
    db_audit_days = DEFAULTS["db_audit_days"] if db_audit_days is None else db_audit_days

    log_path = audit_log_path()
    audit = archive_audit_log(log_path, audit_days, dry_run=dry_run, exclusive=exclusive)
    archives = prune_archives(os.path.join(os.path.dirname(log_path) or ".", "archive"),
                              archive_days, dry_run=dry_run)
    sessions = archive_sessions(sessions_days, dry_run=dry_run)
    db = prune_db_audit(db_audit_days, dry_run=dry_run)

    return {
        "dry_run": dry_run,
        "policy": {
            "audit_days": audit_days,
            "archive_days": archive_days,
            "sessions_days": sessions_days,
            "db_audit_days": db_audit_days,
        },
        "audit": audit,
        "archives": archives,
        "sessions": sessions,
        "database": db,
    }


def maybe_auto_retention(exclusive=None) -> dict | None:
    """每月最多自动执行一次；跳过或失败返回 None。"""
    try:
        marker_dir = os.path.join(ROOT, "logs")
        os.makedirs(marker_dir, exist_ok=True)
        marker = os.path.join(marker_dir, RETENTION_MARKER)
        this_month = _dt.date.today().strftime("%Y-%m")
        if os.path.isfile(marker):
            try:
                with open(marker, "r", encoding="utf-8") as f:
                    if f.read().strip() == this_month:
                        return None
            except OSError:
                pass
        report = apply_retention(exclusive=exclusive)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(this_month)
        return report
    except Exception:
        return None


# ────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────

def _summary(report: dict) -> str:
    audit = report["audit"]
    return (
        f"审计归档 {audit['archived_lines']} 行（{len(audit['months'])} 个月份）"
        f"，保留 {audit['kept_lines']} 行，未判定时间保留 {audit['undated_lines']} 行；"
        f"清理归档 {len(report['archives']['removed'])} 个；"
        f"归档会话 {report['sessions']['archived']} 个；"
        f"删除数据库审计 {report['database']['deleted']} 行"
    )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="幻帧数据保留策略（审计/会话/备份归档清理）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不实际改动")
    parser.add_argument("--audit-days", type=int, default=None, help=f"审计日志保留天数（默认 {DEFAULTS['audit_days']}）")
    parser.add_argument("--archive-days", type=int, default=None, help=f"归档文件保留天数（默认 {DEFAULTS['archive_days']}）")
    parser.add_argument("--sessions-days", type=int, default=None, help=f"会话保留天数（默认 {DEFAULTS['sessions_days']}）")
    parser.add_argument("--db-audit-days", type=int, default=None, help=f"数据库审计保留天数（默认 {DEFAULTS['db_audit_days']}）")
    args = parser.parse_args()

    mode = "预演（不会改动任何文件）" if args.dry_run else "执行"
    print(f"数据保留策略 · {mode}")
    report = apply_retention(
        dry_run=args.dry_run,
        audit_days=args.audit_days,
        archive_days=args.archive_days,
        sessions_days=args.sessions_days,
        db_audit_days=args.db_audit_days,
    )
    print("  " + _summary(report))
    if report["database"].get("skipped"):
        print(f"  （数据库：{report['database']['skipped']}）")
    print("  提示：服务运行时执行本脚本，归档期间新写入的审计行有极小概率丢失；")
    print("        网页「设置 → 系统状态 → 立即清理」会加写入锁，更安全。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
