import logging
import json
import sys
import os
from datetime import datetime
from typing import Optional


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logger(
    name: str = "huanzhen",
    level: str = "INFO",
    log_file: Optional[str] = None,
    fmt: str = "json",
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = True  # 让 MemoryLogHandler（挂在 root）能捕获日志

    if fmt == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        from logging.handlers import RotatingFileHandler

        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # 轮转：单个文件上限 10MB、保留 5 份。
        # 长期运行的部署（服务常驻数月）如果不轮转，agent.log 会无限增长，
        # 最终拖慢启动、占满磁盘——这是「跑久了就出问题」的常见根因。
        try:
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
        except Exception:
            # 极端情况下（路径不可用等）退回普通 FileHandler，不影响服务启动
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
