"""数据备份：把 SQLite 主库与关键配置打包为带时间戳的 zip。

## 为什么需要

此前项目**没有任何备份机制**：`data/huanzhen.db` 里是全部用户、对话、知识库索引
元数据与审计记录，一旦误删、磁盘故障或迁移出错，就全没了。对「交给别人部署、
长期使用」的场景，这是最不可接受的风险。

## 关键实现点

- **必须用 SQLite 的备份 API，而不是直接复制文件**。数据库在服务运行时处于
  活动状态（可能有未落盘的事务、WAL），直接 `copy` 得到的文件可能损坏。
  `sqlite3.Connection.backup()` 会生成一致快照。
- **打包而不是散放**：一次备份 = 一个 zip，便于拷走、按时间归档。
- **自动轮转**：默认只保留最近 7 份，避免备份把磁盘占满。
- **不含 `.env`**：其中含 API 密钥，避免"备份被随手分享导致密钥外泄"。
  恢复时需要你另行保管 `.env`。

## 用法

    venv\\Scripts\\python.exe scripts\\backup.py               # 手动备份一次
    venv\\Scripts\\python.exe scripts\\backup.py --keep 14     # 保留 14 份
    venv\\Scripts\\python.exe scripts\\backup.py --list        # 列出已有备份

服务启动时也会**每天最多自动备份一次**（见 `maybe_auto_backup`）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BACKUP_DIRNAME = os.path.join("data", "backups")
DEFAULT_KEEP = 7
AUTO_MARKER = ".last_auto_backup"


# ────────────────────────────────────────────────────────────
# 路径解析（不依赖 core.*，保证环境异常时也能备份）
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


def db_path() -> str:
    cfg = _load_config_dict()
    rel = str((cfg.get("database") or {}).get("path") or "data/huanzhen.db")
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)


def backup_dir() -> str:
    return os.path.join(ROOT, BACKUP_DIRNAME)


def _display_path(path: str) -> str:
    """尽量转成相对路径用于展示；跨盘符时直接给绝对路径。

    注意：os.path.relpath 在「目标与项目不同盘符」时会抛 ValueError
    （例如 database.path 配到了 E:\\），必须兜底，否则备份会整体失败。
    """
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


# ────────────────────────────────────────────────────────────
# 备份 / 轮转
# ────────────────────────────────────────────────────────────

def _snapshot_sqlite(src: str, dst: str) -> None:
    """用 SQLite 备份 API 生成一致快照（服务运行中也可安全执行）。"""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=10)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _extra_files() -> list[tuple[str, str]]:
    """返回 [(磁盘路径, zip 内路径)]，仅包含可安全备份的非机密文件。"""
    items: list[tuple[str, str]] = []
    cfg = os.path.join(ROOT, "config.yaml")
    if os.path.isfile(cfg):
        items.append((cfg, "config.yaml"))
    for name in ("jwt_secret", "api_key"):
        p = os.path.join(ROOT, "data", "security", name)
        if os.path.isfile(p):
            items.append((p, f"security/{name}"))
    return items


def create_backup(keep: int = DEFAULT_KEEP, out_dir: str | None = None) -> str:
    """创建一次备份，返回生成的 zip 路径。没有数据库时抛 FileNotFoundError。"""
    src_db = db_path()
    if not os.path.isfile(src_db):
        raise FileNotFoundError(f"未找到数据库：{src_db}（服务首次启动后才有）")

    target_dir = out_dir or backup_dir()
    os.makedirs(target_dir, exist_ok=True)

    # 时间戳精确到毫秒：只用秒的话，同一秒内的多次备份会互相覆盖，
    # 导致"备份了却没留下"（实测连发 4 次只剩 1 个文件）。
    now = _dt.datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S") + f"{now.microsecond // 1000:03d}"
    zip_path = os.path.join(target_dir, f"huanzhen-backup-{stamp}.zip")
    seq = 1
    while os.path.exists(zip_path):          # 极端情况下同一毫秒内连发
        zip_path = os.path.join(target_dir, f"huanzhen-backup-{stamp}-{seq}.zip")
        seq += 1

    tmp_dir = tempfile.mkdtemp(prefix="hz_backup_")
    try:
        tmp_db = os.path.join(tmp_dir, "huanzhen.db")
        _snapshot_sqlite(src_db, tmp_db)

        manifest = [
            "幻帧 AI 智能秘书 · 数据备份",
            f"备份时间: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"来源: {ROOT}",
            f"数据库: {_display_path(src_db)}",
            "",
            "包含内容:",
            "  - huanzhen.db      全部用户 / 对话 / 文档元数据 / 审计",
            "  - config.yaml      配置（不含密钥）",
            "  - security/*       本地 API Key 与 JWT 密钥",
            "",
            "恢复方法:",
            "  1. 停止服务（双击 停止服务.bat）",
            "  2. 解压本 zip，把 huanzhen.db 覆盖到项目的 data/ 目录",
            "  3. 如需要，把 config.yaml 与 security/* 一并覆盖",
            "  4. 重新启动（双击 启动幻帧.bat）",
            "",
            "注意：本备份【不包含 .env】（其中含 API 密钥），请自行单独保管。",
        ]

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, "huanzhen.db")
            for disk_path, arc_name in _extra_files():
                zf.write(disk_path, arc_name)
            zf.writestr("MANIFEST.txt", "\n".join(manifest) + "\n")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    prune_backups(keep=keep, out_dir=target_dir)
    return zip_path


def list_backups(out_dir: str | None = None) -> list[str]:
    target_dir = out_dir or backup_dir()
    if not os.path.isdir(target_dir):
        return []
    names = [n for n in os.listdir(target_dir)
             if n.startswith("huanzhen-backup-") and n.endswith(".zip")]
    return sorted((os.path.join(target_dir, n) for n in names), reverse=True)


def prune_backups(keep: int = DEFAULT_KEEP, out_dir: str | None = None) -> list[str]:
    """只保留最近 keep 份，返回被删除的路径列表。"""
    keep = max(1, int(keep))
    existing = list_backups(out_dir)
    removed = []
    for path in existing[keep:]:
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed


# ────────────────────────────────────────────────────────────
# 服务启动时的自动备份（每天最多一次）
# ────────────────────────────────────────────────────────────

def maybe_auto_backup(keep: int = DEFAULT_KEEP) -> str | None:
    """每天最多自动备份一次；返回备份路径，跳过或失败返回 None。

    不会抛异常：备份失败绝不能影响服务启动。
    """
    try:
        target_dir = backup_dir()
        os.makedirs(target_dir, exist_ok=True)
        marker = os.path.join(target_dir, AUTO_MARKER)
        today = _dt.date.today().isoformat()
        if os.path.isfile(marker):
            try:
                with open(marker, "r", encoding="utf-8") as f:
                    if f.read().strip() == today:
                        return None
            except OSError:
                pass
        if not os.path.isfile(db_path()):
            return None                      # 首次启动还没建库
        path = create_backup(keep=keep)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(today)
        return path
    except Exception:
        return None


# ────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="幻帧数据备份")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help=f"保留最近多少份备份（默认 {DEFAULT_KEEP}）")
    parser.add_argument("--list", action="store_true", help="列出已有备份后退出")
    parser.add_argument("--out", default="", help="自定义备份目录（默认 data/backups）")
    args = parser.parse_args()

    out_dir = args.out or None

    if args.list:
        items = list_backups(out_dir)
        if not items:
            print("（暂无备份）")
            return 0
        print(f"共 {len(items)} 份备份：")
        for p in items:
            size_mb = os.path.getsize(p) / 1048576
            print(f"  {os.path.basename(p)}  {size_mb:.2f} MB")
        return 0

    print("正在备份…")
    try:
        path = create_backup(keep=args.keep, out_dir=out_dir)
    except FileNotFoundError as e:
        print(f"[跳过] {e}")
        return 2
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}")
        return 1

    size_mb = os.path.getsize(path) / 1048576
    print(f"✅ 备份完成：{path}  ({size_mb:.2f} MB)")
    print(f"   当前保留最近 {max(1, args.keep)} 份")
    print("   注意：备份不含 .env（含 API 密钥），请自行单独保管。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
