#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
幻帧语音监听器 - Phase 1 启动脚本

三种唤醒后端：
  1. VAD 语音活动检测（默认，零配置）
     python voice_demo.py

  2. OpenWakeWord 开源离线唤醒词
     python voice_demo.py --backend openwakeword --wake-word alexa

  3. Porcupine 唤醒词（需注册 Picovoice）
     python voice_demo.py --backend porcupine --wake-word computer

纯键盘模式（无需麦克风持续监听）：
  python voice_demo.py --keyboard

快速开始（VAD 模式，零配置）：
  1. 激活虚拟环境: venv/Scripts/activate
  2. 确保 pyaudio 已安装: pip install pyaudio
  3. 设置 Groq Key: set GROQ_API_KEY=gsk_你的key
  4. 启动: py -3.12 voice_demo.py
  5. 对着麦克风说话即可激活！
"""

import os
import sys
import time
import argparse

# 确保项目根在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.voice_listener import VoiceListener, AudioRecorder, WakeWordDetector


def build_banner() -> str:
    return r"""
  ╔══════════════════════════════════════════╗
  ║        幻帧语音助手 - Phase 1             ║
  ║   语音输入 → Agent 处理 → 文字回复         ║
  ╚══════════════════════════════════════════╝
"""


class TerminalUI:
    """终端界面辅助类 - 显示监听状态"""

    @staticmethod
    def show_status(status: str):
        """显示当前状态"""
        symbols = {
            "listening": "👂 等待语音...",
            "wake": "🎤 检测到语音活动！准备录音...",
            "recording": "🔴 正在录音...（请说话）",
            "transcribing": "📝 语音识别中...",
            "thinking": "🤔 Agent 思考中...",
            "responding": "🤖 幻帧回复:",
            "done": "✅ 处理完成",
            "error": "❌ 出错",
        }
        print(f"\n{symbols.get(status, status)}")


def main():
    parser = argparse.ArgumentParser(
        description="幻帧语音监听器 - Phase 1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # VAD 模式（默认，零配置，说话即激活）
  py -3.12 voice_demo.py

  # VAD 模式 + 键盘兜底
  py -3.12 voice_demo.py --keyboard

  # OpenWakeWord 唤醒词模式（alexa 检测最准确）
  py -3.12 voice_demo.py --backend openwakeword --wake-word alexa

  # Porcupine 唤醒词模式（需 PICOVOICE_ACCESS_KEY）
  py -3.12 voice_demo.py --backend porcupine --wake-word computer

  # 纯键盘模式（零依赖唤醒后端）
  py -3.12 voice_demo.py --keyboard --no-wake

  # 列出音频设备
  py -3.12 voice_demo.py --list-devices
"""
    )
    parser.add_argument("--wake-word", default="voice_activity",
                       help="唤醒词（VAD: 任意; Porcupine: computer/jarvis/...; OWW: alexa/hey_jarvis/...）")
    parser.add_argument("--backend", default="vad",
                       choices=["vad", "porcupine", "openwakeword"],
                       help="唤醒后端 (默认: vad)")
    parser.add_argument("--access-key", default=None,
                       help="Picovoice AccessKey（仅 porcupine 后端需要）")
    parser.add_argument("--keyboard", action="store_true",
                       help="启用键盘快捷键兜底模式 (Ctrl+Alt+V)")
    parser.add_argument("--no-wake", action="store_true",
                       help="纯键盘模式（不初始化唤醒后端，仅靠 Ctrl+Alt+V 触发）")
    parser.add_argument("--stt", default="groq", choices=["groq", "openai"],
                       help="STT 服务商 (默认: groq)")
    parser.add_argument("--language", default="zh",
                       help="语言代码 (默认: zh)")
    parser.add_argument("--list-devices", action="store_true",
                       help="列出所有可用音频输入设备和唤醒后端")
    parser.add_argument("--device", type=int, default=None,
                       help="指定音频设备索引")
    parser.add_argument("--vad-threshold", type=float, default=0.02,
                       help="VAD 能量阈值 0.0~1.0 (默认: 0.02，越小越灵敏)")

    args = parser.parse_args()

    # 列出设备
    if args.list_devices:
        AudioRecorder.list_devices()
        print("\n" + "─" * 50)
        print("唤醒后端一览：")
        print("─" * 50)
        for backend in ["vad", "porcupine", "openwakeword"]:
            kws = WakeWordDetector.get_builtin_keywords(backend)
            label = "(零配置，说话即激活)" if backend == "vad" else f"内置: {', '.join(kws)}"
            print(f"  [{backend}] {label}")
        print()
        sys.exit(0)

    print(build_banner())

    # 确定唤醒后端
    if args.no_wake:
        backend_label = "无（纯键盘模式）"
        actual_backend = "porcupine"  # 随便填，init 会失败但键盘兜底接管
    else:
        actual_backend = args.backend
        backend_names = {"vad": "VAD 语音活动检测", "porcupine": "Porcupine", "openwakeword": "OpenWakeWord"}
        backend_label = backend_names.get(actual_backend, actual_backend)

    print(f"  唤醒后端: {backend_label}")
    print(f"  唤醒词:   {args.wake_word}")
    print(f"  STT 服务: {args.stt}")
    print(f"  键盘兜底: {'是' if args.keyboard else '否'}")
    print(f"  语言:     {args.language}")
    if actual_backend == "vad":
        print(f"  VAD 阈值: {args.vad_threshold} (越小越灵敏)")
    print()

    # 检查必要的 API Key
    if args.stt == "groq":
        stt_key = os.environ.get("GROQ_API_KEY", "")
        if not stt_key:
            print("⚠️  GROQ_API_KEY 未设置，语音转文字不可用！")
            print("   获取免费 Groq API Key: https://console.groq.com/keys")
            print("   设置: set GROQ_API_KEY=你的key")
            print()
    elif args.stt == "openai":
        stt_key = os.environ.get("OPENAI_API_KEY", "")
        if not stt_key:
            print("⚠️  OPENAI_API_KEY 未设置，语音转文字不可用！")
            print()

    # Porcupine 后端提示
    if actual_backend == "porcupine":
        access_key = args.access_key or os.environ.get("PICOVOICE_ACCESS_KEY")
        if not access_key:
            print("⚠️  未设置 PICOVOICE_ACCESS_KEY，Porcupine 唤醒不可用。")
            print("   建议使用 VAD 模式（默认）或 OpenWakeWord。")
            print()
            print("   VAD 模式: py -3.12 voice_demo.py")
            print("   OWW 模式: py -3.12 voice_demo.py --backend openwakeword")
            print()

    # 创建监听器
    listener = VoiceListener(
        wake_word=args.wake_word,
        wake_word_backend=actual_backend,
        access_key=args.access_key,
        stt_provider=args.stt,
        language=args.language,
        keyboard_fallback=args.keyboard,
        device_index=args.device,
    )

    # VAD 阈值调整
    if actual_backend == "vad" and hasattr(listener._detector, '_impl'):
        vad = listener._detector._impl
        if hasattr(vad, 'energy_threshold'):
            vad.energy_threshold = args.vad_threshold

    # 注册回调
    ui = TerminalUI()

    def on_wake():
        ui.show_status("wake")

    def on_recording_start():
        ui.show_status("recording")

    def on_recording_end(text):
        print(f"\n  📝 你说: {text}")

    def on_response(response):
        print(f"\n  🤖 幻帧: {response}")
        print(f"\n  {'─' * 46}")
        ui.show_status("listening")

    def on_error(error):
        print(f"\n  ❌ 错误: {error}")

    listener.on_wake_word = on_wake
    listener.on_recording_start = on_recording_start
    listener.on_recording_end = on_recording_end
    listener.on_response = on_response
    listener.on_error = on_error

    ui.show_status("listening")
    if args.keyboard:
        print(f"  提示: 按 Ctrl+Alt+V 开始说话，或对着麦克风说话激活，Ctrl+C 退出")
    else:
        print(f"  提示: 对着麦克风说话即可激活，Ctrl+C 退出")
    print()

    if listener.start():
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\n  正在退出...")
            listener.stop()
            print("  再见！\n")
            sys.exit(0)
    else:
        print("\n  ❌ 启动失败")
        print("  请检查:")
        print("  - pyaudio 已安装: pip install pyaudio")
        if actual_backend == "porcupine":
            print("  - pvporcupine 已安装: pip install pvporcupine")
            print("  - PICOVOICE_ACCESS_KEY 已设置")
        if actual_backend == "openwakeword":
            print("  - openwakeword 已安装: pip install openwakeword")
            print("  - tflite-runtime 已安装 (Windows 可能不可用)")
        print("  - GROQ_API_KEY 已设置")
        print()
        print("  推荐使用 VAD 模式（零配置）:")
        print("  py -3.12 voice_demo.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
