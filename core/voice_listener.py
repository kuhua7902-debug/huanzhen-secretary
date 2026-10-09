"""
幻帧语音监听器 - Phase 1: 语音输入 → Agent → 文字回复

架构：
  WakeWordDetector → AudioRecorder(pyaudio) → STT(Groq/OpenAI Whisper) → CoreAgent

支持的唤醒后端（按推荐顺序）：
  1. VAD 语音活动检测（默认，零配置）：检测到说话声即激活，纯 pyaudio 实现
  2. OpenWakeWord（开源离线）：支持 alexa/hey_jarvis/hey_mycroft 等内置唤醒词
  3. Porcupine（需注册）：支持内置关键词 + 自定义 .ppn 模型

支持的激活方式：
  1. 语音检测（VAD）：直接说话即可激活
  2. 键盘快捷键（Ctrl+Alt+V）：作为兜底方案

使用示例：
  python voice_demo.py                          # VAD 模式（默认，零配置）
  python voice_demo.py --backend porcupine      # Porcupine 唤醒词模式
  python voice_demo.py --backend openwakeword   # OpenWakeWord 模式
  python voice_demo.py --keyboard               # 纯键盘模式
"""

import os
import sys
import json
import time
import wave
import threading
import tempfile
from pathlib import Path
from typing import Optional, Callable

import httpx
import numpy as np

from core.logger import setup_logger

logger = setup_logger("huanzhen.voice")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 默认采样率（Porcupine 要求 16000）
SAMPLE_RATE = 16000
# 每帧样本数（Porcupine 要求 512）
FRAME_LENGTH = 512
# WAV 格式参数
WAV_CHANNELS = 1
WAV_SAMPLE_WIDTH = 2  # 16-bit


# ---------------------------------------------------------------------------
# GUI 紧急停止（语音"停止"指令的刹车）
# ---------------------------------------------------------------------------

def _trigger_gui_abort(reason: str) -> bool:
    """触发进程级 GUI 紧急停止标志（core.gui_abort.trigger_abort）。

    为什么需要：`agent.cancel()` 只设置 CoreAgent 的 cancel_event，
    正在执行的 pyautogui.click / typewrite / drag 等 GUI 操作不会中途响应它，
    会继续把动作做完。core.gui_abort 提供的全局标志才是 GUI 工具在每次
    操作前都会检查的刹车，配合 cancel_event 形成多层制动。

    防御式延迟导入：gui_abort 缺失/异常时语音模块仍能正常加载运行，
    只记一条 debug 日志，不影响其他停止逻辑。

    返回：True 表示成功触发，False 表示 gui_abort 不可用或触发失败。
    """
    try:
        from core.gui_abort import trigger_abort
    except ImportError as e:
        logger.debug("core.gui_abort 不可用，跳过 GUI 紧急停止: %s", e)
        return False
    try:
        trigger_abort(reason=reason)
        return True
    except Exception as e:
        logger.debug("触发 GUI 紧急停止失败: %s", e)
        return False


# ---------------------------------------------------------------------------
# 音频录制器
# ---------------------------------------------------------------------------

class AudioRecorder:
    """从麦克风录制音频，静音检测自动结束"""

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        silence_threshold: float = 0.03,
        silence_duration: float = 1.5,
        max_duration: float = 15.0,
        device_index: Optional[int] = None,
    ):
        self.sample_rate = sample_rate
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.max_duration = max_duration
        self.device_index = device_index

    def record(self) -> Optional[bytes]:
        """录制音频直到静音，返回 WAV 字节数据"""
        try:
            import pyaudio
        except ImportError:
            logger.error("pyaudio 未安装，请执行: pip install pyaudio")
            return None

        p = pyaudio.PyAudio()
        stream = None

        try:
            stream = p.open(
                format=pyaudio.paInt16,
                channels=WAV_CHANNELS,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=FRAME_LENGTH,
            )

            logger.info("开始录音...（静音 %.1f 秒后自动停止）", self.silence_duration)

            frames = []
            silent_frames = 0
            max_frames = int(self.max_duration * self.sample_rate / FRAME_LENGTH)
            silence_frames_needed = int(self.silence_duration * self.sample_rate / FRAME_LENGTH)

            for _ in range(max_frames):
                data = stream.read(FRAME_LENGTH, exception_on_overflow=False)
                frames.append(data)

                # 计算音量
                audio_data = np.frombuffer(data, dtype=np.int16)
                volume = np.abs(audio_data).mean() / 32768.0

                if volume < self.silence_threshold:
                    silent_frames += 1
                    if silent_frames >= silence_frames_needed:
                        logger.info("检测到静音，录音结束（共 %.1f 秒）",
                                   len(frames) * FRAME_LENGTH / self.sample_rate)
                        break
                else:
                    silent_frames = 0

            if not frames:
                logger.warning("未录制到音频")
                return None

            # 转换为 WAV 字节
            wav_bytes = self._frames_to_wav(frames)
            logger.info("录音完成：%d 帧，%.1f 秒",
                       len(frames), len(frames) * FRAME_LENGTH / self.sample_rate)
            return wav_bytes

        except Exception as e:
            logger.error("录音失败: %s", e)
            return None
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            p.terminate()

    def _frames_to_wav(self, frames: list) -> bytes:
        """将音频帧转换为 WAV 格式字节"""
        import io
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wf:
            wf.setnchannels(WAV_CHANNELS)
            wf.setsampwidth(WAV_SAMPLE_WIDTH)
            wf.setframerate(self.sample_rate)
            wf.writeframes(b''.join(frames))
        return buffer.getvalue()

    @staticmethod
    def list_devices():
        """列出所有音频输入设备"""
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            print("\n可用音频输入设备：")
            count = p.get_device_count()
            for i in range(count):
                try:
                    info = p.get_device_info_by_index(i)
                except AttributeError:
                    # 旧版 PyAudio 兼容
                    info = p.get_device_info_general(i)
                if info.get("maxInputChannels", 0) > 0:
                    print(f"  [{i}] {info['name']}")
            p.terminate()
        except ImportError:
            print("pyaudio 未安装")


# ---------------------------------------------------------------------------
# 语音转文字（STT）
# ---------------------------------------------------------------------------

class SpeechToText:
    """语音转文字，支持 Groq Whisper 和 OpenAI Whisper"""

    def __init__(self, provider: str = "groq", api_key: Optional[str] = None,
                 api_base: Optional[str] = None, language: str = "zh"):
        self.provider = provider
        self.api_key = api_key
        self.api_base = api_base
        self.language = language

        if provider == "groq":
            self.api_url = api_base or "https://api.groq.com/openai/v1/audio/transcriptions"
            self.model = "whisper-large-v3"
            self._key = api_key or os.environ.get("GROQ_API_KEY", "")
            self._headers = {"Authorization": f"Bearer {self._key}"}
        else:
            self.api_url = api_base or "https://api.openai.com/v1/audio/transcriptions"
            self.model = "whisper-1"
            self._key = api_key or os.environ.get("OPENAI_API_KEY", "")
            self._headers = {"Authorization": f"Bearer {self._key}"}

    def transcribe(self, audio_bytes: bytes) -> str:
        """将音频字节转为文本"""
        if not self._key:
            logger.error("STT API key 未配置（%s）", "GROQ_API_KEY" if self.provider == "groq" else "OPENAI_API_KEY")
            return ""

        # 写入临时文件
        tmp_path = None
        try:
            # 使用临时文件
            fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            with os.fdopen(fd, 'wb') as f:
                f.write(audio_bytes)

            with open(tmp_path, "rb") as f:
                files = {
                    "file": (os.path.basename(tmp_path), f, "audio/wav"),
                    "model": (None, self.model),
                }
                if self.language:
                    files["language"] = (None, self.language)

                with httpx.Client(timeout=60) as client:
                    response = client.post(self.api_url, headers=self._headers, files=files)
                    response.raise_for_status()
                    text = response.json().get("text", "")
                    logger.info("STT 转录结果: %s", text[:100])
                    return text.strip()

        except Exception as e:
            logger.error("STT 转录失败: %s", e)
            return ""
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# 唤醒词检测器（多后端工厂模式）
# ---------------------------------------------------------------------------

class _BaseDetector:
    """唤醒词检测器基类"""

    def init(self) -> bool:
        raise NotImplementedError

    def listen(self, timeout: float = 0.1) -> bool:
        raise NotImplementedError

    def cleanup(self):
        raise NotImplementedError

    @property
    def is_streaming(self) -> bool:
        """是否持续占用麦克风流（Porcupine/OWW 返回 True，VAD 按需打开）"""
        return True


# ---- VAD 后端（默认，零依赖） ----

class _VADDetector(_BaseDetector):
    """
    语音活动检测 (Voice Activity Detection) 唤醒后端

    原理：持续监听麦克风能量级别，当能量超过阈值持续一定帧数后触发。
    零额外依赖，仅需 pyaudio + numpy。

    行为：
      - 用户开始说话 → 检测到语音活动 → 触发唤醒 → 进入录音模式
      - 录音结束后有冷却时间，避免立即再次触发
    """

    # 内置唤醒词名称（用于 UI 显示，实际不被检测）
    BUILTIN_KEYWORDS = ["voice_activity"]

    def __init__(
        self,
        keyword: str = "voice_activity",
        energy_threshold: float = 0.02,
        trigger_frames: int = 8,
        cooldown_seconds: float = 2.0,
        device_index: Optional[int] = None,
        **kwargs,
    ):
        self.keyword = keyword
        self.energy_threshold = energy_threshold
        self.trigger_frames = trigger_frames          # 连续超过阈值多少帧后触发
        self.cooldown_seconds = cooldown_seconds       # 触发后冷却时间
        self.device_index = device_index

        self._audio = None
        self._stream = None
        self._last_trigger_time = 0.0
        self._consecutive_triggers = 0

    @property
    def is_streaming(self) -> bool:
        """VAD 需要持续监听麦克风"""
        return True

    def init(self) -> bool:
        """初始化麦克风流"""
        try:
            import pyaudio
        except ImportError:
            logger.error("pyaudio 未安装，请执行: pip install pyaudio")
            return False

        try:
            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=FRAME_LENGTH,
            )
            logger.info("VAD 语音检测器初始化成功（阈值=%.3f, 连续%d帧触发）",
                       self.energy_threshold, self.trigger_frames)
            return True
        except Exception as e:
            logger.error("VAD 检测器初始化失败: %s", e)
            self.cleanup()
            return False

    def listen(self, timeout: float = 0.1) -> bool:
        """监听一帧，检测是否有语音活动"""
        if not self._stream:
            return False

        try:
            pcm = self._stream.read(FRAME_LENGTH, exception_on_overflow=False)
            audio_data = np.frombuffer(pcm, dtype=np.int16)
            energy = np.abs(audio_data).mean() / 32768.0

            if energy >= self.energy_threshold:
                self._consecutive_triggers += 1
                if self._consecutive_triggers >= self.trigger_frames:
                    # 检查冷却时间
                    now = time.time()
                    if now - self._last_trigger_time >= self.cooldown_seconds:
                        self._last_trigger_time = now
                        self._consecutive_triggers = 0
                        logger.info("VAD 检测到语音活动（能量=%.4f）", energy)
                        return True
                    else:
                        # 冷却中，重置计数器
                        self._consecutive_triggers = 0
            else:
                self._consecutive_triggers = 0

            return False
        except Exception as e:
            logger.debug("VAD 检测异常: %s", e)
            return False

    def cleanup(self):
        """释放麦克风资源"""
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._audio:
            try:
                self._audio.terminate()
            except Exception:
                pass
            self._audio = None


# ---- Porcupine 后端（需注册 Picovoice） ----

class _PorcupineDetector(_BaseDetector):
    """基于 Picovoice Porcupine 的唤醒词检测器"""

    # Porcupine 内置关键词
    BUILTIN_KEYWORDS = [
        "computer", "jarvis", "alexa", "hey google", "hey siri",
        "porcupine", "blueberry", "bumblebee", "grasshopper",
        "picovoice", "terminator", "americano", "grapefruit",
    ]

    def __init__(
        self,
        keyword: str = "computer",
        access_key: Optional[str] = None,
        sensitivities: Optional[list] = None,
        device_index: Optional[int] = None,
        **kwargs,
    ):
        self.keyword = keyword
        self.access_key = access_key or os.environ.get("PICOVOICE_ACCESS_KEY", "")
        self.sensitivities = sensitivities or [0.5]
        self.device_index = device_index
        self._porcupine = None
        self._audio = None
        self._stream = None

    @property
    def is_custom_keyword(self) -> bool:
        """是否是自定义唤醒词（.ppn 文件路径）"""
        return self.keyword not in self.BUILTIN_KEYWORDS and (
            self.keyword.endswith(".ppn") or os.path.exists(self.keyword)
        )

    def init(self) -> bool:
        """初始化 Porcupine"""
        if not self.access_key:
            logger.warning(
                "PICOVOICE_ACCESS_KEY 未设置！\n"
                "请到 https://console.picovoice.ai/ 注册获取 AccessKey，\n"
                "或切换到 VAD 后端（--backend vad）。"
            )
            return False

        try:
            import pvporcupine
        except ImportError:
            logger.error("pvporcupine 未安装，请执行: pip install pvporcupine")
            return False

        try:
            import pyaudio
            self._audio = pyaudio.PyAudio()

            if self.is_custom_keyword:
                keyword_path = self.keyword
                logger.info("使用自定义唤醒词模型: %s", keyword_path)
                self._porcupine = pvporcupine.create(
                    access_key=self.access_key,
                    keyword_paths=[keyword_path],
                    sensitivities=self.sensitivities,
                )
            else:
                logger.info("使用内置唤醒词: %s", self.keyword)
                self._porcupine = pvporcupine.create(
                    access_key=self.access_key,
                    keywords=[self.keyword],
                    sensitivities=self.sensitivities,
                )

            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self._porcupine.sample_rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self._porcupine.frame_length,
            )
            logger.info("Porcupine 唤醒词检测器初始化成功")
            return True

        except Exception as e:
            logger.error("Porcupine 检测器初始化失败: %s", e)
            self.cleanup()
            return False

    def listen(self, timeout: float = 0.1) -> bool:
        """监听一帧音频，检测唤醒词"""
        if not self._porcupine or not self._stream:
            return False

        try:
            pcm = self._stream.read(
                self._porcupine.frame_length, exception_on_overflow=False
            )
            pcm_array = np.frombuffer(pcm, dtype=np.int16)
            result = self._porcupine.process(pcm_array)
            return result >= 0
        except Exception as e:
            logger.debug("Porcupine 检测异常: %s", e)
            return False

    def cleanup(self):
        """释放资源"""
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._audio:
            try:
                self._audio.terminate()
            except Exception:
                pass
            self._audio = None
        if self._porcupine:
            try:
                self._porcupine.delete()
            except Exception:
                pass
            self._porcupine = None


# ---- OpenWakeWord 后端（开源离线，需 tflite-runtime 或 ONNX） ----

class _OpenWakeWordDetector(_BaseDetector):
    """基于 OpenWakeWord 的唤醒词检测器（开源离线）"""

    BUILTIN_KEYWORDS = [
        "alexa", "hey_jarvis", "hey_mycroft", "hey_rhasspy", "timer", "weather"
    ]

    def __init__(
        self,
        keyword: str = "alexa",
        threshold: float = 0.5,
        device_index: Optional[int] = None,
        **kwargs,
    ):
        self.keyword = keyword
        self.threshold = threshold
        self.device_index = device_index
        self._oww_model = None
        self._audio = None
        self._stream = None

    def init(self) -> bool:
        """初始化 OpenWakeWord"""
        try:
            import openwakeword
        except ImportError:
            logger.error("openwakeword 未安装，请执行: pip install openwakeword")
            return False

        # 检查唤醒词是否可用
        if self.keyword not in self.BUILTIN_KEYWORDS:
            logger.warning(
                "唤醒词 '%s' 不是 OpenWakeWord 内置模型。\n"
                "可选: %s\n"
                "将自动使用 'alexa' 作为替代。",
                self.keyword, ", ".join(self.BUILTIN_KEYWORDS)
            )
            self.keyword = "alexa"

        # 主路径：tflite 推理框架
        if self._init_with_framework("tflite"):
            logger.info("OpenWakeWord 检测器初始化成功（唤醒词: %s）", self.keyword)
            return True

        # 回退路径：ONNX 推理框架（必须同样完成"模型 + 音频流"的完整初始化）
        logger.info("尝试使用 ONNX 推理框架...")
        if self._init_with_framework("onnx"):
            logger.info("OpenWakeWord (ONNX) 初始化成功（唤醒词: %s）", self.keyword)
            return True

        # 两条路径都不可用 → 必须返回 False，让调用方走键盘兜底，
        # 绝不能返回 True（旧实现 ONNX 分支只重建了模型没打开音频流却返回 True，
        # listen() 因 self._stream 为空而永远返回 False，唤醒检测静默死亡）。
        logger.error(
            "OpenWakeWord 检测器初始化失败（tflite 与 ONNX 推理框架均不可用），"
            "唤醒词检测不可用！请改用键盘兜底（--keyboard，快捷键 Ctrl+Alt+V）"
            "或切换唤醒后端（--backend vad）。"
        )
        self.cleanup()
        return False

    def _init_with_framework(self, framework: str) -> bool:
        """用指定推理框架初始化：创建唤醒模型 + 打开麦克风音频流。

        只有「模型」与「音频流」都真正就绪才返回 True。
        失败时清理半初始化资源（残留的 PyAudio 句柄会导致下次打开设备失败）。

        参数：
            framework: "tflite" 或 "onnx"
        """
        try:
            from openwakeword.model import Model

            self._oww_model = Model(
                wakeword_models=[self.keyword],
                inference_framework=framework,
            )

            import pyaudio
            self._audio = pyaudio.PyAudio()
            # OpenWakeWord 默认每帧 1280 样本 (80ms @ 16kHz)
            self._frame_length = 1280
            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self._frame_length,
            )

            # 双保险：模型和音频流缺一不可，否则 listen() 必然失效
            if not self._oww_model or not self._stream:
                logger.error("OpenWakeWord（%s）初始化不完整：模型或音频流缺失", framework)
                self.cleanup()
                return False

            return True

        except Exception as e:
            logger.error("OpenWakeWord（%s）初始化失败: %s", framework, e)
            self.cleanup()
            return False

    def listen(self, timeout: float = 0.1) -> bool:
        """监听一帧，检测唤醒词"""
        if not self._oww_model or not self._stream:
            return False

        try:
            pcm = self._stream.read(
                self._frame_length, exception_on_overflow=False
            )
            audio_data = np.frombuffer(pcm, dtype=np.int16)
            prediction = self._oww_model.predict(audio_data)
            # prediction 格式: {model_name: score}
            score = prediction.get(self.keyword, 0)
            return score >= self.threshold
        except Exception as e:
            logger.debug("OpenWakeWord 检测异常: %s", e)
            return False

    def cleanup(self):
        """释放资源"""
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._audio:
            try:
                self._audio.terminate()
            except Exception:
                pass
            self._audio = None
        self._oww_model = None


# ---- 工厂类 ----

class WakeWordDetector:
    """
    唤醒词检测器工厂

    根据 backend 参数创建对应的后端：
      - "vad" (默认):   语音活动检测，零配置，检测到说话声即触发
      - "porcupine":    Picovoice Porcupine，需注册获取 AccessKey
      - "openwakeword": OpenWakeWord 开源离线引擎

    用法:
        detector = WakeWordDetector(backend="vad")
        if detector.init():
            while True:
                if detector.listen():
                    print("唤醒!")
    """

    BACKENDS = {
        "vad": _VADDetector,
        "porcupine": _PorcupineDetector,
        "openwakeword": _OpenWakeWordDetector,
    }

    @classmethod
    def get_builtin_keywords(cls, backend: str = "vad") -> list:
        """获取指定后端的可用唤醒词列表"""
        detector_cls = cls.BACKENDS.get(backend)
        if detector_cls and hasattr(detector_cls, "BUILTIN_KEYWORDS"):
            return detector_cls.BUILTIN_KEYWORDS
        return []

    @classmethod
    def default_backend(cls) -> str:
        """自动选择最佳可用后端"""
        # VAD 总是可用（只需 pyaudio）
        return "vad"

    def __init__(self, backend: str = "vad", **kwargs):
        """
        参数:
            backend: 后端名称 "vad" / "porcupine" / "openwakeword"
            **kwargs: 传递给具体后端的参数
                VAD: keyword, energy_threshold, trigger_frames, cooldown_seconds, device_index
                Porcupine: keyword, access_key, sensitivities, device_index
                OpenWakeWord: keyword, threshold, device_index
        """
        detector_cls = self.BACKENDS.get(backend)
        if detector_cls is None:
            logger.warning("未知后端 '%s'，回退到 VAD", backend)
            detector_cls = _VADDetector

        self.backend = backend
        self._impl = detector_cls(**kwargs)

    @property
    def keyword(self) -> str:
        return getattr(self._impl, "keyword", "voice_activity")

    @property
    def is_streaming(self) -> bool:
        return self._impl.is_streaming

    def init(self) -> bool:
        return self._impl.init()

    def listen(self, timeout: float = 0.1) -> bool:
        return self._impl.listen(timeout)

    def cleanup(self):
        self._impl.cleanup()


# ---------------------------------------------------------------------------
# 语音监听器（主控制器）
# ---------------------------------------------------------------------------

class VoiceListener:
    """
    幻帧语音监听器 - Phase 1

    后台常驻，监听唤醒词 → 录音 → STT → Agent 处理

    用法:
        listener = VoiceListener(wake_word="computer")
        listener.on_response = lambda text: print(f"幻帧: {text}")
        listener.start()
        # ... 等待 ...
        listener.stop()
    """

    def __init__(
        self,
        wake_word: str = "computer",
        wake_word_backend: str = "vad",
        access_key: Optional[str] = None,
        stt_provider: str = "groq",
        stt_api_key: Optional[str] = None,
        language: str = "zh",
        silence_duration: float = 1.5,
        max_record_seconds: float = 15.0,
        device_index: Optional[int] = None,
        keyboard_fallback: bool = False,
    ):
        """
        参数:
            wake_word: 唤醒词/检测标识
                       - VAD 后端: 任意字符串（仅用于日志显示）
                       - Porcupine: 内置关键词（如 "computer", "jarvis"）或 .ppn 路径
                       - OpenWakeWord: "alexa", "hey_jarvis", "hey_mycroft" 等
            wake_word_backend: 唤醒后端 "vad"(默认) / "porcupine" / "openwakeword"
            access_key: Picovoice 平台 AccessKey（仅 porcupine 后端需要）
            stt_provider: STT 服务商 "groq" 或 "openai"
            stt_api_key: STT API key（不传则从环境变量读取）
            language: 语言代码，中文 "zh"
            silence_duration: 静音多久后结束录音（秒）
            max_record_seconds: 最大录音时长（秒）
            device_index: 音频设备索引（None=默认设备）
            keyboard_fallback: 是否启用键盘快捷键兜底模式（Ctrl+Alt+V 激活）
        """
        self.wake_word = wake_word
        self.wake_word_backend = wake_word_backend
        self.access_key = access_key
        self.keyboard_fallback = keyboard_fallback
        self.device_index = device_index

        # 唤醒词检测器（工厂模式）
        self._detector = WakeWordDetector(
            backend=wake_word_backend,
            keyword=wake_word,
            access_key=access_key,
            device_index=device_index,
        )

        # 录音器
        self._recorder = AudioRecorder(
            silence_duration=silence_duration,
            max_duration=max_record_seconds,
            device_index=device_index,
        )

        # STT
        self._stt = SpeechToText(
            provider=stt_provider,
            api_key=stt_api_key,
            language=language,
        )

        # 线程控制
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._ready = threading.Event()

        # ── Agent 后台线程控制（支持中断） ──
        self._chat_thread: Optional[threading.Thread] = None
        self._chat_cancel_event: Optional[threading.Event] = None

        # Agent（延迟加载，避免循环导入）
        self._agent = None

        # 唤醒抑制：TTS 播报期间暂停唤醒词检测，防止 TTS 语音误触发
        self._wake_suppress = threading.Event()
        self._wake_suppress_until: float = 0.0

        # ⭐ 唤醒后冷却期：防止连续误触发 + TTS 回声循环
        self._last_wake_time: float = 0.0
        self._wake_cooldown: float = 3.0  # 唤醒后 3 秒内不再响应

        # 回调
        self.on_wake_word: Optional[Callable[[], None]] = None
        self.on_recording_start: Optional[Callable[[], None]] = None
        self.on_recording_end: Optional[Callable[[str], None]] = None  # text
        self.on_response: Optional[Callable[[str], None]] = None  # response_text
        self.on_error: Optional[Callable[[str], None]] = None

        # 状态
        self.is_listening_for_wake = False
        self.is_recording = False
        self.is_processing = False

    def start(self) -> bool:
        """启动语音监听（后台线程）"""
        if self._running.is_set():
            logger.warning("语音监听已在运行中")
            return True

        # 初始化唤醒词检测器
        detector_ok = self._detector.init()

        if not detector_ok and self._detector.is_streaming:
            # 流式后端失败（如 Porcupine 缺 key），尝试键盘兜底
            if self.keyboard_fallback:
                logger.warning("唤醒词初始化失败，使用纯键盘兜底模式")
            else:
                logger.error("唤醒词初始化失败，语音监听不可用。可启用 --keyboard 兜底。")
                return False

        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="voice-listener")
        self._thread.start()

        # 等待就绪
        self._ready.wait(timeout=3)
        if self._ready.is_set():
            backend_name = self._detector.backend
            logger.info("语音监听器已启动 - 后端: %s, 关键词: %s",
                       backend_name, self._detector.keyword)
        return True

    def stop(self):
        """停止语音监听"""
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._detector.cleanup()
        logger.info("语音监听器已停止")

    def suppress_wake(self, duration_seconds: float):
        """暂停唤醒词检测（TTS 播报期间调用，防止 TTS 语音误触发唤醒词）

        参数:
            duration_seconds: 抑制时长（秒），建议根据回复文本长度估算
                             中文约 4 字/秒，加上安全余量
        """
        self._wake_suppress.set()
        self._wake_suppress_until = time.time() + duration_seconds
        # ⭐ 同时延长冷却期，确保 TTS 播报结束后不会立即被触发
        self._last_wake_time = time.time() + duration_seconds
        logger.debug("唤醒检测已抑制 %.1f 秒（TTS 播报中）", duration_seconds)

    def resume_wake(self):
        """恢复唤醒词检测"""
        self._wake_suppress.clear()
        self._last_wake_time = time.time()  # 恢复时重置冷却，避免刚恢复立即触发
        logger.debug("唤醒检测已恢复")

    def _is_wake_suppressed(self) -> bool:
        """检查当前是否应该抑制唤醒"""
        if not self._wake_suppress.is_set():
            return False
        # 检查是否已过抑制时间
        if time.time() >= self._wake_suppress_until:
            self._wake_suppress.clear()
            return False
        return True

    def _init_agent(self):
        """延迟初始化 Agent"""
        if self._agent is None:
            try:
                from core.agent import CoreAgent
                config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
                import yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                # 解析 ${VAR} 环境变量占位符
                from nanobot.config.loader import _resolve_env_vars
                config = _resolve_env_vars(config)

                # ⭐ 从 config 加载冷却期配置
                voice_cfg = config.get("voice", {})
                self._wake_cooldown = float(voice_cfg.get("wake_cooldown_seconds", 3.0))
                logger.info("唤醒冷却期: %.1f 秒", self._wake_cooldown)

                self._agent = CoreAgent(config)
                logger.info("Agent 初始化成功")
            except Exception as e:
                logger.error("Agent 初始化失败: %s", e)
                if self.on_error:
                    self.on_error(f"Agent 初始化失败: {e}")

    def _loop(self):
        """主循环：监听唤醒词/语音活动 → 录音 → STT → Agent"""
        # 启动键盘快捷键兜底线程
        if self.keyboard_fallback:
            kb_thread = threading.Thread(target=self._keyboard_listener, daemon=True)
            kb_thread.start()

        self._init_agent()
        self._ready.set()

        # 如果唤醒检测器不可用且无键盘兜底，直接退出
        if not self._detector.is_streaming and not self.keyboard_fallback:
            logger.warning("唤醒检测器不可用且无键盘兜底，监听循环退出")
            return

        try:
            while self._running.is_set():
                # 阶段 1: 等待唤醒（VAD: 等待语音活动；Porcupine/OWW: 等待唤醒词）
                self.is_listening_for_wake = True
                wake_detected = False

                # 只有流式后端才轮询 listen()
                if self._detector.is_streaming:
                    while self._running.is_set() and not wake_detected:
                        if self._detector.listen(timeout=0.1):
                            wake_detected = True
                        time.sleep(0.01)
                else:
                    # 纯键盘模式：等待 running 事件结束
                    while self._running.is_set():
                        time.sleep(0.5)

                if not self._running.is_set():
                    break

                self.is_listening_for_wake = False

                # 唤醒抑制：TTS 播报期间忽略唤醒词（防止 TTS 语音误触发）
                if self._is_wake_suppressed():
                    logger.debug("唤醒被抑制（TTS 播报中），忽略此次触发")
                    self.is_listening_for_wake = True
                    continue

                # ⭐ 唤醒后冷却期：防止同一句 Alexa 被重复检测多次
                elapsed_since_last_wake = time.time() - self._last_wake_time
                if elapsed_since_last_wake < self._wake_cooldown:
                    logger.debug("冷却中（剩余 %.1f 秒），忽略此次触发", 
                               self._wake_cooldown - elapsed_since_last_wake)
                    self.is_listening_for_wake = True
                    continue

                self._last_wake_time = time.time()  # 记录本次唤醒时间

                backend_label = self._detector.backend.upper()
                logger.info("检测到唤醒 (%s): %s", backend_label, self._detector.keyword)

                if self.on_wake_word:
                    try:
                        self.on_wake_word()
                    except Exception as e:
                        logger.debug("on_wake_word 回调异常: %s", e)

                # 阶段 2: 录音
                self.is_recording = True
                if self.on_recording_start:
                    try:
                        self.on_recording_start()
                    except Exception as e:
                        logger.debug("on_recording_start 回调异常: %s", e)

                audio_bytes = self._recorder.record()
                self.is_recording = False

                if not audio_bytes:
                    logger.warning("未录制到有效音频")
                    continue

                # 阶段 3: STT 语音转文字
                self.is_processing = True
                text = self._stt.transcribe(audio_bytes)

                if not text:
                    logger.warning("STT 转录结果为空")
                    self.is_processing = False
                    continue

                if self.on_recording_end:
                    try:
                        self.on_recording_end(text)
                    except Exception as e:
                        logger.debug("on_recording_end 回调异常: %s", e)

                # 阶段 4: Agent 处理（后台线程 + 可取消）
                try:
                    # ⭐ 停止关键词检测
                    STOP_KW = {"停止", "停了", "别做了", "取消", "别干了", "别搞了", "不准动"}
                    if text.strip() in STOP_KW or any(kw in text for kw in ["停下来", "别继续", "不要了"]):
                        # 取消当前正在运行的任务
                        if self._chat_cancel_event and self._chat_thread and self._chat_thread.is_alive():
                            self._chat_cancel_event.set()
                            self._agent.cancel()  # 直接触发 CoreAgent 取消
                            # ⭐ 同步触发进程级 GUI 紧急停止：
                            # agent.cancel() 只设 CoreAgent 的 cancel_event，
                            # 正在执行的 pyautogui 点击/输入不会中断，
                            # 必须靠 gui_abort 全局标志让 GUI 工具立刻刹车。
                            _trigger_gui_abort("语音指令「停止」")
                            logger.info("检测到停止关键词'%s'，已取消当前任务", text)
                            if self.on_response:
                                try:
                                    self.on_response("已停止当前任务")
                                except Exception:
                                    pass
                        else:
                            # 没有正在运行的任务
                            if self.on_response:
                                try:
                                    self.on_response("当前没有正在运行的任务。")
                                except Exception:
                                    pass
                        self.is_processing = False
                        continue

                    # ⭐ 如果已有任务在运行，忽略新命令（除非是停止关键词）
                    if self._chat_thread and self._chat_thread.is_alive():
                        logger.info("Agent 正在处理中，忽略新命令: %s", text[:50])
                        self.is_processing = False  # 让监听循环继续
                        if self.on_response:
                            try:
                                self.on_response("我正在处理上一个任务，请先说「停止」来中断。")
                            except Exception:
                                pass
                        continue

                    logger.info("用户语音: %s", text)

                    # 在新线程中运行 agent.chat()，不阻塞唤醒循环
                    self._chat_cancel_event = threading.Event()

                    def _run_chat():
                        try:
                            response = self._agent.chat(text, cancel_event=self._chat_cancel_event)
                            if not self._chat_cancel_event.is_set():
                                logger.info("幻帧回复: %s", response[:200])
                                if self.on_response:
                                    try:
                                        self.on_response(response)
                                    except Exception as e:
                                        logger.debug("on_response 回调异常: %s", e)
                        except Exception as e:
                            logger.error("Agent 处理失败: %s", e)
                            if self.on_error and not self._chat_cancel_event.is_set():
                                try:
                                    self.on_error(str(e))
                                except Exception:
                                    pass
                        finally:
                            self.is_processing = False
                            self._chat_cancel_event = None

                    self._chat_thread = threading.Thread(target=_run_chat, daemon=True, name="agent-chat")
                    self._chat_thread.start()
                    # 不阻塞主循环，让唤醒词检测继续工作
                except Exception as e:
                    logger.error("Agent 处理失败: %s", e)
                    if self.on_error:
                        try:
                            self.on_error(str(e))
                        except Exception:
                            pass
                    self.is_processing = False

        except Exception as e:
            logger.error("语音监听循环异常: %s", e, exc_info=True)
        finally:
            self._detector.cleanup()

    def _keyboard_listener(self):
        """键盘快捷键兜底：Ctrl+Alt+V 激活语音输入"""
        try:
            import keyboard
            logger.info("键盘兜底模式已启用：按 Ctrl+Alt+V 开始语音输入")

            def on_hotkey():
                if self.is_recording or self.is_processing:
                    logger.info("正在录音或处理中，忽略热键")
                    return
                if self._is_wake_suppressed():
                    logger.info("唤醒被抑制（TTS 播报中），忽略热键")
                    return
                # ⭐ 冷却期检查
                elapsed = time.time() - self._last_wake_time
                if elapsed < self._wake_cooldown:
                    logger.info("冷却中（剩余 %.1f 秒），忽略热键", 
                               self._wake_cooldown - elapsed)
                    return

                logger.info("键盘热键触发，开始录音...")
                self.is_recording = True

                if self.on_recording_start:
                    try:
                        self.on_recording_start()
                    except Exception:
                        pass

                audio_bytes = self._recorder.record()
                self.is_recording = False

                if not audio_bytes:
                    self.is_processing = False
                    return

                self.is_processing = True
                text = self._stt.transcribe(audio_bytes)

                if not text:
                    self.is_processing = False
                    return

                if self.on_recording_end:
                    try:
                        self.on_recording_end(text)
                    except Exception:
                        pass

                try:
                    self._init_agent()
                    # ⭐ 停止关键词检测
                    STOP_KW = {"停止", "停了", "别做了", "取消", "别干了", "别搞了", "不准动"}
                    if text.strip() in STOP_KW or any(kw in text for kw in ["停下来", "别继续", "不要了"]):
                        if self._chat_cancel_event and self._chat_thread and self._chat_thread.is_alive():
                            self._chat_cancel_event.set()
                            self._agent.cancel()
                            # ⭐ 同上：同步触发进程级 GUI 紧急停止，
                            # 让正在执行的 GUI 自动化动作立即中止
                            _trigger_gui_abort("语音指令「停止」（键盘模式）")
                            logger.info("检测到停止关键词，已取消当前任务")
                        if self.on_response:
                            self.on_response("已停止当前任务")
                        self.is_processing = False
                        return

                    # 后台线程运行
                    self._chat_cancel_event = threading.Event()

                    def _run_kb_chat():
                        try:
                            response = self._agent.chat(text, cancel_event=self._chat_cancel_event)
                            if not self._chat_cancel_event.is_set():
                                if self.on_response:
                                    self.on_response(response)
                        except Exception:
                            pass
                        finally:
                            self.is_processing = False
                            self._chat_cancel_event = None

                    self._chat_thread = threading.Thread(target=_run_kb_chat, daemon=True)
                    self._chat_thread.start()
                except Exception as e:
                    logger.error("Agent 处理失败: %s", e)
                    if self.on_error:
                        try:
                            self.on_error(str(e))
                        except Exception:
                            pass

                self.is_processing = False

            keyboard.add_hotkey("ctrl+alt+v", on_hotkey)
            while self._running.is_set():
                time.sleep(0.5)
            keyboard.remove_hotkey("ctrl+alt+v")

        except ImportError:
            logger.warning("keyboard 库未安装，键盘兜底模式不可用。请执行: pip install keyboard")
        except Exception as e:
            logger.error("键盘监听异常: %s", e)


# ---------------------------------------------------------------------------
# 便捷工厂函数
# ---------------------------------------------------------------------------

def create_voice_listener_from_config(config_path: Optional[str] = None) -> VoiceListener:
    """从 config.yaml 创建 VoiceListener 实例"""
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")

    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    voice_config = config.get("voice", {})

    return VoiceListener(
        wake_word=voice_config.get("wake_word", "computer"),
        wake_word_backend=voice_config.get("wake_word_backend", "vad"),
        access_key=voice_config.get("picovoice_access_key") or os.environ.get("PICOVOICE_ACCESS_KEY"),
        stt_provider=voice_config.get("stt_provider", "groq"),
        stt_api_key=voice_config.get("stt_api_key"),
        language=voice_config.get("language", "zh"),
        silence_duration=voice_config.get("silence_duration", 1.5),
        max_record_seconds=voice_config.get("max_record_seconds", 15.0),
        device_index=voice_config.get("device_index"),
        keyboard_fallback=voice_config.get("keyboard_fallback", True),
    )


# ---------------------------------------------------------------------------
# 独立运行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="幻帧语音监听器 - Phase 1")
    parser.add_argument("--wake-word", default="computer",
                       help="唤醒词（VAD后端仅作显示，Porcupine: computer/jarvis/...，OpenWakeWord: alexa/hey_jarvis/...）")
    parser.add_argument("--backend", default="vad",
                       choices=["vad", "porcupine", "openwakeword"],
                       help="唤醒后端 (默认: vad)")
    parser.add_argument("--access-key", default=None,
                       help="Picovoice AccessKey（仅 porcupine 后端需要）")
    parser.add_argument("--keyboard", action="store_true",
                       help="启用键盘快捷键兜底（Ctrl+Alt+V）")
    parser.add_argument("--stt", default="groq", choices=["groq", "openai"],
                       help="STT 服务商")
    parser.add_argument("--list-devices", action="store_true",
                       help="列出音频设备")
    parser.add_argument("--language", default="zh",
                       help="语言代码")
    args = parser.parse_args()

    if args.list_devices:
        AudioRecorder.list_devices()
        print("\n可用后端及唤醒词：")
        for backend in ["vad", "porcupine", "openwakeword"]:
            kws = WakeWordDetector.get_builtin_keywords(backend)
            print(f"  {backend}: {', '.join(kws) if kws else '(无需唤醒词)'}")
        sys.exit(0)

    print("=" * 50)
    print("  幻帧语音监听器 - Phase 1")
    print("  语音输入 → Agent → 文字回复")
    print("=" * 50)
    print()
    print(f"  唤醒后端: {args.backend}")
    print(f"  唤醒词:   {args.wake_word}")
    print(f"  STT:      {args.stt}")
    print(f"  键盘兜底: {'是' if args.keyboard else '否'}")
    print()

    listener = VoiceListener(
        wake_word=args.wake_word,
        wake_word_backend=args.backend,
        access_key=args.access_key,
        stt_provider=args.stt,
        language=args.language,
        keyboard_fallback=args.keyboard,
    )

    # 注册回调
    def on_wake():
        print("\n🎤 检测到唤醒！开始聆听...")

    def on_recording_start():
        print("🔴 录音中...")

    def on_recording_end(text):
        print(f"📝 识别结果: {text}")

    def on_response(response):
        print(f"🤖 幻帧: {response}")

    def on_error(error):
        print(f"❌ 错误: {error}")

    listener.on_wake_word = on_wake
    listener.on_recording_start = on_recording_start
    listener.on_recording_end = on_recording_end
    listener.on_response = on_response
    listener.on_error = on_error

    if listener.start():
        hint = "按 Ctrl+Alt+V 开始说话" if args.keyboard else "对着麦克风说话即可"
        print(f"✅ 语音监听已启动。{hint}，Ctrl+C 退出。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 正在退出...")
        finally:
            listener.stop()
    else:
        print("❌ 语音监听启动失败")
        sys.exit(1)
