#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
幻帧语音助手 - 系统托盘版

开机常驻后台，系统托盘图标显示状态。
喊 "Alexa" 唤醒 → 录音 → STT → Agent 处理 → TTS 语音播报回复。

用法:
    # 默认（从 config.yaml 读取配置）
    py -3.12 voice_tray.py

    # OpenWakeWord + alexa + groq + edge TTS
    py -3.12 voice_tray.py --backend openwakeword --wake-word alexa --keyboard --tts edge

    # 纯键盘模式（无唤醒词）
    py -3.12 voice_tray.py --keyboard --no-wake

    # VAD 模式（说话即激活）
    py -3.12 voice_tray.py --backend vad --keyboard

特性:
    - 系统托盘图标，右键菜单（显示状态/退出）
    - 唤醒时托盘图标变色提示
    - 录音/思考/播报 状态实时显示
    - Agent 回复自动 TTS 语音播报
    - 键盘热键 Ctrl+Alt+V 兜底
"""

import os
import sys
import time
import tempfile
import threading
import argparse

# 确保项目根在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 单实例锁：防止多个语音助手同时运行 ──
PID_FILE = os.path.join(tempfile.gettempdir(), "huanzhen_voice_tray.pid")


def _is_process_running(pid: int) -> bool:
    """用 Win32 API 判断指定 PID 的进程是否仍在运行。

    旧实现的三个缺陷（本次修复重点）：
    1. 先 CloseHandle(handle) 再 GetExitCodeProcess(handle, ...)：句柄已经关闭，
       读到的退出码无效（恒为 0），永远不等于 259(STILL_ACTIVE)，
       于是单实例保护从未生效，可以重复启动多个语音助手。
       → 现在必须在关闭句柄之前读取退出码，且句柄用 finally 保证只关闭一次。
    2. 未声明 argtypes/restype：64 位 Windows 上 OpenProcess 返回的 HANDLE 会被
       ctypes 当作 32 位 int 截断，句柄失效。
       → 现在显式声明 wintypes.HANDLE / DWORD / BOOL 签名。
    3. 使用 PROCESS_QUERY_INFORMATION(0x0400)：对"属于其他用户/更高权限"的进程会
       直接失败（拒绝访问），从而误判为"没在运行"。
       → 改用 PROCESS_QUERY_LIMITED_INFORMATION(0x1000)，权限要求更低更可靠。

    非 Windows 或 ctypes 不可用时返回 False（即不做单实例拦截，保持原有宽松行为）。
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False

    # WinDLL 仅 Windows 存在；不存在说明当前平台不支持该检查
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        return False

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    try:
        kernel32 = win_dll("kernel32", use_last_error=True)
        # 显式声明签名：防止 64 位句柄被截断成 32 位，也防止参数被错误装箱
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            # 打不开：进程不存在，或无权打开（后者极少见，按"未运行"处理）
            return False
        try:
            # ⭐ 必须在 CloseHandle 之前读退出码（旧代码顺序反了，导致判断永远为假）
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            # 保证句柄恰好关闭一次，不泄漏内核对象
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _check_single_instance():
    """确保只有一个幻帧语音助手实例在运行"""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r", encoding="utf-8") as f:
                old_pid = int(f.read().strip())
        except Exception:
            old_pid = 0  # PID 文件无效，继续启动

        if old_pid and old_pid != os.getpid() and _is_process_running(old_pid):
            print(f"❌ 幻帧语音助手已在运行中 (PID: {old_pid})")
            print("   请先退出旧实例再启动")
            sys.exit(1)

    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def _cleanup_pid_file():
    """退出时清理 PID 文件"""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError:
        pass


import atexit
atexit.register(_cleanup_pid_file)

from PIL import Image, ImageDraw
import pystray

from core.voice_listener import VoiceListener
from core.logger import setup_logger

logger = setup_logger("huanzhen.tray")


# ---------------------------------------------------------------------------
# 托盘图标生成
# ---------------------------------------------------------------------------

def _create_icon_image(size: int = 64, color: str = "#4A90D9",
                       text: str = "🎤") -> Image.Image:
    """生成托盘图标"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 绘制圆形背景
    margin = 2
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
        outline="#FFFFFF",
        width=2,
    )
    return img


# 不同状态的图标颜色
ICON_COLORS = {
    "idle": "#4A90D9",         # 蓝色 - 监听中
    "listening": "#50C878",    # 绿色 - 听到唤醒
    "recording": "#FF6B6B",    # 红色 - 录音中
    "thinking": "#FFD700",     # 金色 - 思考中
    "speaking": "#9B59B6",     # 紫色 - 播报中
    "error": "#E74C3C",        # 深红 - 错误
}


class VoiceTrayApp:
    """系统托盘应用"""

    def __init__(self, listener: VoiceListener, enable_tts: bool = True):
        self.listener = listener
        self.enable_tts = enable_tts
        self._tray: pystray.Icon = None
        self._status = "idle"
        self._tts_engine = None

        # 尝试初始化 TTS
        if enable_tts:
            try:
                from core.tts_engine import create_tts_engine_from_config
                self._tts_engine = create_tts_engine_from_config()
                logger.info("TTS 引擎就绪: %s", self._tts_engine.backend)
            except Exception as e:
                logger.warning("TTS 初始化失败: %s，仅文字回复", e)
                self._tts_engine = None

        # 注册回调
        self._register_callbacks()

    def _register_callbacks(self):
        """注册 VoiceListener 回调"""
        def on_wake():
            self._set_status("listening")
            logger.info("🎤 检测到唤醒词！")

        def on_recording_start():
            self._set_status("recording")
            logger.info("🔴 录音中...")

        def on_recording_end(text):
            self._set_status("thinking")
            logger.info("📝 你说: %s", text)

        def on_response(response):
            self._set_status("speaking")

            # TTS 语音播报：播放前暂停唤醒检测，防止 TTS 语音误触发唤醒词
            if self._tts_engine:
                try:
                    # 估算 TTS 播放时长（中文约 4 字/秒 + 1.5s 安全余量 + 0.5s 冷却）
                    estimated_duration = max(len(response) / 4 + 3.0, 4.0)
                    self.listener.suppress_wake(estimated_duration)
                    self._tts_engine.speak(response, block=False)
                except Exception as e:
                    logger.error("TTS 播报失败: %s", e)
                    self.listener.resume_wake()
            else:
                # 无 TTS 时，仅短暂抑制（1.5s 冷却避免立即误触发）
                self.listener.suppress_wake(1.5)

            # 恢复监听状态
            def _reset():
                time.sleep(0.5)
                self._set_status("idle")

            threading.Thread(target=_reset, daemon=True).start()

        def on_error(error):
            self._set_status("error")
            logger.error("❌ %s", error)
            # 3 秒后恢复
            threading.Timer(3.0, lambda: self._set_status("idle")).start()

        self.listener.on_wake_word = on_wake
        self.listener.on_recording_start = on_recording_start
        self.listener.on_recording_end = on_recording_end
        self.listener.on_response = on_response
        self.listener.on_error = on_error

    def _set_status(self, status: str):
        """更新托盘状态和图标"""
        self._status = status
        if self._tray:
            color = ICON_COLORS.get(status, "#4A90D9")
            icon = _create_icon_image(color=color)
            self._tray.icon = icon

            status_text = {
                "idle": "👂 监听中",
                "listening": "🎤 检测到唤醒！",
                "recording": "🔴 录音中...",
                "thinking": "🤔 思考中...",
                "speaking": "🔊 播报中...",
                "error": "❌ 错误",
            }
            self._tray.title = f"幻帧语音助手 - {status_text.get(status, status)}"

    def _create_menu(self):
        """创建右键菜单"""
        def show_status(icon, item):
            status = self._status
            status_text = {
                "idle": "👂 监听中 - 说 'Alexa' 唤醒",
                "listening": "🎤 检测到唤醒词",
                "recording": "🔴 正在录音",
                "thinking": "🤔 Agent 思考中",
                "speaking": "🔊 播报回复中",
                "error": "❌ 发生错误",
            }
            msg = status_text.get(status, f"状态: {status}")
            icon.notify(msg, "幻帧语音助手")

        def exit_app(icon, item):
            logger.info("用户请求退出")
            self.listener.stop()
            if self._tts_engine:
                self._tts_engine.stop()
            icon.stop()

        return pystray.Menu(
            pystray.MenuItem("📊 当前状态", show_status, enabled=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "🎤 手动激活 (Ctrl+Alt+V)", 
                lambda *_: None,  # 键盘兜底已经处理
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ 退出幻帧助手", exit_app),
        )

    def run(self):
        """启动托盘应用"""
        # 初始化托盘图标
        icon = _create_icon_image(color=ICON_COLORS["idle"])
        menu = self._create_menu()

        self._tray = pystray.Icon(
            "huanzhen_voice",
            icon,
            "幻帧语音助手 - 监听中",
            menu,
        )

        # 启动语音监听（后台线程）
        if not self.listener.start():
            logger.error("语音监听启动失败！")
            self._tray.notify("语音监听启动失败，请检查配置", "幻帧语音助手")
            return

        logger.info("幻帧语音助手托盘应用已启动")
        logger.info("唤醒词: %s (后端: %s)", self.listener.wake_word, self.listener.wake_word_backend)
        if self._tts_engine:
            logger.info("TTS: %s", self._tts_engine.backend)

        # 显示启动通知
        backend_names = {
            "openwakeword": "OpenWakeWord",
            "vad": "VAD 语音检测",
            "porcupine": "Porcupine",
        }
        backend_label = backend_names.get(self.listener.wake_word_backend, self.listener.wake_word_backend)
        self._tray.notify(
            f"幻帧语音助手已启动\n"
            f"唤醒词: {self.listener.wake_word} ({backend_label})\n"
            f"键盘兜底: Ctrl+Alt+V",
            "幻帧语音助手",
        )

        # 运行托盘（阻塞直到退出）
        self._tray.run()

    def stop(self):
        """停止应用"""
        if self._tray:
            self._tray.stop()
        self.listener.resume_wake()
        self.listener.stop()
        if self._tts_engine:
            self._tts_engine.stop()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_banner():
    return r"""
  ╔══════════════════════════════════════════╗
  ║      幻帧语音助手 - 系统托盘版            ║
  ║   开机常驻 · 喊话唤醒 · 语音播报           ║
  ╚══════════════════════════════════════════╝
"""


def main():
    parser = argparse.ArgumentParser(
        description="幻帧语音助手 - 系统托盘版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 默认 OpenWakeWord + alexa + edge TTS
  py -3.12 voice_tray.py

  # VAD 模式 + pyttsx3 离线 TTS
  py -3.12 voice_tray.py --backend vad --tts pyttsx3 --keyboard

  # 纯键盘模式 + 禁用 TTS
  py -3.12 voice_tray.py --keyboard --no-wake --no-tts
""",
    )
    parser.add_argument("--wake-word", default="alexa",
                       help="唤醒词 (默认: alexa)")
    parser.add_argument("--backend", default="openwakeword",
                       choices=["vad", "porcupine", "openwakeword"],
                       help="唤醒后端 (默认: openwakeword)")
    parser.add_argument("--access-key", default=None,
                       help="Picovoice AccessKey")
    parser.add_argument("--keyboard", action="store_true",
                       help="启用键盘兜底 (Ctrl+Alt+V)")
    parser.add_argument("--no-wake", action="store_true",
                       help="纯键盘模式（不初始化唤醒后端）")
    parser.add_argument("--stt", default="groq", choices=["groq", "openai"],
                       help="STT 服务商 (默认: groq)")
    parser.add_argument("--language", default="zh",
                       help="语言代码 (默认: zh)")
    parser.add_argument("--device", type=int, default=None,
                       help="音频设备索引")
    parser.add_argument("--vad-threshold", type=float, default=0.02,
                       help="VAD 能量阈值 (默认: 0.02)")
    parser.add_argument("--oww-threshold", type=float, default=0.7,
                       help="OpenWakeWord 检测阈值 0.0~1.0，越大越严格 (默认: 0.7)")
    parser.add_argument("--tts", default="edge",
                       choices=["edge", "pyttsx3", "none"],
                       help="TTS 后端 (默认: edge)")
    parser.add_argument("--no-tts", action="store_true",
                       help="禁用 TTS 语音播报")

    args = parser.parse_args()

    # ⭐ 单实例检查
    _check_single_instance()

    # 检查必要环境变量
    if args.stt == "groq" and not os.environ.get("GROQ_API_KEY"):
        print("⚠️  GROQ_API_KEY 未设置")
        print("   请设置环境变量或在 启动幻帧语音助手.bat 中配置")
        print()

    # 确定唤醒后端
    if args.no_wake:
        actual_backend = "porcupine"  # 随便填，init 会失败但键盘兜底接管
    else:
        actual_backend = args.backend

    # 创建 VoiceListener
    listener = VoiceListener(
        wake_word=args.wake_word,
        wake_word_backend=actual_backend,
        access_key=args.access_key,
        stt_provider=args.stt,
        language=args.language,
        keyboard_fallback=args.keyboard,
        device_index=args.device,
    )

    # VAD 阈值
    if actual_backend == "vad" and hasattr(listener._detector, '_impl'):
        vad = listener._detector._impl
        if hasattr(vad, 'energy_threshold'):
            vad.energy_threshold = args.vad_threshold

    # 创建 OpenWakeWord 阈值
    if actual_backend == "openwakeword" and hasattr(listener._detector, '_impl'):
        oww = listener._detector._impl
        if hasattr(oww, 'threshold'):
            oww.threshold = getattr(args, 'oww_threshold', 0.5)

    # 启动托盘
    enable_tts = not args.no_tts
    app = VoiceTrayApp(listener, enable_tts=enable_tts)

    # 如果命令行指定了 TTS 后端，覆盖 config
    if enable_tts and args.tts and app._tts_engine:
        app._tts_engine.backend = args.tts
        if args.tts == "pyttsx3" and not app._tts_engine._engine:
            app._tts_engine._init_pyttsx3()

    # 打印信息
    print(build_banner())
    backend_names = {"openwakeword": "OpenWakeWord", "vad": "VAD", "porcupine": "Porcupine"}
    print(f"  唤醒后端: {backend_names.get(actual_backend, actual_backend)}")
    print(f"  唤醒词:   {args.wake_word}")
    print(f"  STT:      {args.stt}")
    print(f"  TTS:      {'禁用' if not enable_tts else args.tts}")
    print(f"  键盘兜底: {'是' if args.keyboard else '否'}")
    print()
    print("  👂 系统托盘已启动 - 说 'Alexa' 唤醒我")
    print("  📌 右键托盘图标可查看状态或退出")
    print()

    try:
        app.run()
    except KeyboardInterrupt:
        print("\n  正在退出...")
        app.stop()
        print("  再见！\n")


if __name__ == "__main__":
    main()
