"""
幻帧 TTS 语音合成引擎

支持两种后端：
  1. pyttsx3（离线，Windows 默认 SAPI5，中文女声）
  2. edge-tts（在线，微软 Edge 语音，中文质量更高）
  3. none（仅打印，不朗读）

用法:
    engine = TTSEngine(backend="edge")
    engine.speak("好的，已为您打开计算器")
"""

import asyncio
import threading
import time
from typing import Optional, Literal

from core.logger import setup_logger

logger = setup_logger("keji.tts")

# 可用的后端类型
TTSBackend = Literal["pyttsx3", "edge", "none"]


class TTSEngine:
    """语音合成引擎"""

    def __init__(
        self,
        backend: TTSBackend = "pyttsx3",
        rate: int = 180,          # 语速（pyttsx3 用，默认 180 words/min）
        volume: float = 1.0,       # 音量 0.0~1.0
        edge_voice: str = "zh-CN-XiaoxiaoNeural",  # 中文女声（edge-tts）
    ):
        self.backend = backend
        self.rate = rate
        self.volume = volume
        self.edge_voice = edge_voice
        self._engine = None        # pyttsx3 engine
        self._speaking = False

        if backend == "pyttsx3":
            self._init_pyttsx3()
        elif backend == "edge":
            # edge-tts 无需预先初始化，每次 speak 时调用
            pass
        elif backend == "none":
            logger.info("TTS 已禁用（backend=none）")
        else:
            logger.warning("未知 TTS 后端 '%s'，回退到 pyttsx3", backend)
            self.backend = "pyttsx3"
            self._init_pyttsx3()

    def _init_pyttsx3(self):
        """初始化 pyttsx3 离线引擎"""
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.rate)
            self._engine.setProperty("volume", self.volume)

            # 尝试设置中文语音
            voices = self._engine.getProperty("voices")
            for voice in voices:
                # 优先找中文女声
                if "chinese" in voice.name.lower() and "female" in voice.name.lower():
                    self._engine.setProperty("voice", voice.id)
                    logger.info("TTS 语音: %s", voice.name)
                    break
            else:
                # 退而求其次，找任何中文语音
                for voice in voices:
                    if "chinese" in voice.name.lower() or "zh" in voice.id.lower():
                        self._engine.setProperty("voice", voice.id)
                        logger.info("TTS 语音: %s", voice.name)
                        break

            logger.info("TTS (pyttsx3) 初始化成功，语速=%d，音量=%.1f", self.rate, self.volume)
        except ImportError:
            logger.error("pyttsx3 未安装，请执行: pip install pyttsx3")
            self.backend = "none"
        except Exception as e:
            logger.warning("pyttsx3 初始化失败: %s，回退到 none 模式", e)
            self.backend = "none"

    def speak(self, text: str, block: bool = False) -> bool:
        """
        朗读文本

        参数:
            text: 要朗读的文本
            block: 是否阻塞等待朗读完成（默认 False，后台线程朗读）
        返回:
            是否成功启动朗读
        """
        if not text or not text.strip():
            return False

        # 清理文本（去掉过长内容，保留前 2000 字，约 8 分钟朗读）
        clean_text = text.strip()[:2000]

        if self.backend == "pyttsx3":
            return self._speak_pyttsx3(clean_text, block)
        elif self.backend == "edge":
            return self._speak_edge(clean_text, block)
        else:
            logger.debug("TTS 跳过（backend=none）: %s", clean_text[:50])
            return False

    def _speak_pyttsx3(self, text: str, block: bool) -> bool:
        """使用 pyttsx3 离线朗读"""
        if not self._engine:
            logger.warning("pyttsx3 引擎未初始化")
            return False

        if block:
            try:
                self._speaking = True
                self._engine.say(text)
                self._engine.runAndWait()
                self._speaking = False
                return True
            except Exception as e:
                logger.error("pyttsx3 朗读失败: %s", e)
                self._speaking = False
                return False
        else:
            def _run():
                try:
                    self._speaking = True
                    self._engine.say(text)
                    self._engine.runAndWait()
                except Exception as e:
                    logger.error("pyttsx3 后台朗读失败: %s", e)
                finally:
                    self._speaking = False

            t = threading.Thread(target=_run, daemon=True, name="tts-worker")
            t.start()
            return True

    def _speak_edge(self, text: str, block: bool) -> bool:
        """使用 edge-tts 在线朗读（微软 Edge 语音，中文质量更高）"""
        if block:
            try:
                asyncio.run(self._edge_tts_task(text))
                return True
            except RuntimeError:
                # 已经在事件循环中，用 nest_asyncio
                return self._speak_edge_sync(text)
            except Exception as e:
                logger.error("edge-tts 朗读失败: %s，回退 pyttsx3", e)
                if self._engine:
                    return self._speak_pyttsx3(text, block)
                return False
        else:
            t = threading.Thread(
                target=self._speak_edge_sync, args=(text,), daemon=True, name="tts-edge"
            )
            t.start()
            return True

    def _speak_edge_sync(self, text: str) -> bool:
        """在独立线程中运行 edge-tts 异步任务"""
        try:
            # 创建新的事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._edge_tts_task(text))
                return True
            finally:
                loop.close()
        except Exception as e:
            logger.error("edge-tts 朗读线程失败: %s", e)
            return False

    async def _edge_tts_task(self, text: str):
        """edge-tts 异步任务"""
        import tempfile
        import os

        try:
            import edge_tts
        except ImportError:
            logger.error("edge-tts 未安装，请执行: pip install edge-tts")
            return

        self._speaking = True
        tmp_path = None
        try:
            # 生成临时音频文件
            fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)

            communicate = edge_tts.Communicate(text, self.edge_voice, rate="+0%")
            await communicate.save(tmp_path)

            # 播放音频
            await self._play_audio(tmp_path)

        except Exception as e:
            logger.error("edge-tts 任务异常: %s", e)
        finally:
            self._speaking = False
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    async def _play_audio(self, filepath: str):
        """播放音频文件（跨平台）

        修复说明：
        PowerShell 的 MediaPlayer.Open() + Play() 后，NaturalDuration 不一定会立即就绪。
        若 .HasTimeSpan 为 false，TimeSpan.Ticks 为 0，导致 while 循环立即退出并 Close，
        音频播放被中断。修复方案：
          1. 先循环等待 NaturalDuration 就绪（最多等 5 秒）
          2. 就绪后按实际时长等待 + 2 秒缓冲
        """
        import subprocess
        import sys

        try:
            if sys.platform == "win32":
                # Windows: 使用 PowerShell + MediaPlayer 播放 MP3
                ps_cmd = (
                    f"Add-Type -AssemblyName PresentationCore; "
                    f"$player = New-Object System.Windows.Media.MediaPlayer; "
                    f"$player.Open('{filepath}'); "
                    f"$player.Play(); "
                    # 步骤1：等待 NaturalDuration 就绪（每 100ms 检查，最多 5 秒）
                    f"$loaded = $false; "
                    f"for ($i = 0; $i -lt 50; $i++) {{ "
                    f"    Start-Sleep -Milliseconds 100; "
                    f"    if ($player.NaturalDuration.HasTimeSpan) {{ "
                    f"        $loaded = $true; break "
                    f"    }} "
                    f"}}; "
                    # 步骤2：按实际时长等待 + 2s 缓冲（上限 120s）
                    f"if ($loaded) {{ "
                    f"    $durationMs = $player.NaturalDuration.TimeSpan.TotalMilliseconds; "
                    f"    if ($durationMs -gt 0) {{ "
                    f"        $waitMs = [Math]::Min($durationMs + 2000, 120000); "
                    f"        Start-Sleep -Milliseconds $waitMs "
                    f"    }} "
                    f"}}; "
                    f"$player.Close()"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    capture_output=True, timeout=130  # 比内部最大 120s 多 10s 容错
                )
            else:
                # Linux/Mac: 使用系统播放器
                subprocess.run(["afplay" if sys.platform == "darwin" else "aplay", filepath],
                             capture_output=True, timeout=130)
        except subprocess.TimeoutExpired:
            logger.warning("音频播放超时（>130秒），已跳过")
        except Exception as e:
            logger.error("音频播放失败: %s", e)

    def stop(self):
        """停止朗读"""
        if self._engine and self.backend == "pyttsx3":
            try:
                self._engine.stop()
            except Exception:
                pass
        self._speaking = False

    @property
    def is_speaking(self) -> bool:
        """是否正在朗读"""
        return self._speaking


def create_tts_engine_from_config(config_path: Optional[str] = None) -> TTSEngine:
    """从 config.yaml 创建 TTSEngine"""
    import os
    import yaml
    from nanobot.config.loader import _resolve_env_vars

    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config = _resolve_env_vars(config)

    voice_config = config.get("voice", {})
    tts_config = voice_config.get("tts", {})

    return TTSEngine(
        backend=tts_config.get("backend", "pyttsx3"),
        rate=tts_config.get("rate", 180),
        volume=tts_config.get("volume", 1.0),
        edge_voice=tts_config.get("edge_voice", "zh-CN-XiaoxiaoNeural"),
    )
