"""dafeiyu_voice —— 只把"文字→语音"这一步换成 GPT-SoVITS 本地音色。

刻意做成"最小侵入"：
  · 不改人格、不改记忆、不改分条逻辑（那些由 dafeiyu_brain / dafeiyu_human 负责）
  · 只在出站阶段，按概率把这一条回复的文字换成一条语音
  · 换音色 = 换 ref_audio_root 里的 wav + asr.txt，不需要动任何代码

GPT-SoVITS 的 /tts 接口本身很朴素：
  POST {text, text_lang, ref_audio_path, prompt_text, prompt_lang, media_type}
"""

import asyncio
import os
import random
import re
import time
import uuid

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Plain, Record

PLUGIN_NAME = "dafeiyu_voice"

# 情绪 → 关键词。命中越多越优先，没命中就是"平静"

# 从心智层读激素，给情绪判定加"底色"：压力高→偏低落，亲近高→偏害羞
HORMONE_FILE = "/AstrBot/data/plugin_data/dafeiyu_hormone/hormones.json"


def _hormone_bias() -> dict:
    try:
        import json as _json
        with open(HORMONE_FILE, encoding="utf-8") as f:
            h = _json.load(f)
        return {
            "cort": float(h.get("cort", 0.2)),
            "oxy": float(h.get("oxy", 0.5)),
            "dopa": float(h.get("dopa", 0.4)),
            "sero": float(h.get("sero", 0.6)),
        }
    except Exception:
        return {}

EMOTION_HINTS = {
    "高兴": ["哈哈", "太好了", "终于", "开心", "高兴", "笑死", "嘿嘿", "呀！", "！"],
    "害羞": ["害羞", "脸红", "别乱说", "讨厌", "嗯——", "抱", "亲", "想你"],
    "生气": ["哼", "不理你", "生气", "气死", "凶", "坏", "讨厌你"],
    "着急": ["快点", "来不及", "怎么办", "急", "等等", "别走"],
}
DEFAULT_EMOTION = "平静"

# 用户明确要语音时，忽略概率强制转（否则她会说"我发语音"却发了文字，自相矛盾）
VOICE_REQUEST = ["语音", "听你说", "说话给我听", "想听你", "声音", "发条语音", "录音"]

# 情绪 → 语速微调（GPT-SoVITS 支持 speed_factor 就用，不支持会忽略）
SPEED = {"平静": 1.0, "高兴": 1.05, "害羞": 0.95, "生气": 1.08, "着急": 1.12}


def _speed_with_mood(base: float) -> float:
    """压力高语速变慢（没力气），多巴胺高略快。"""
    h = _hormone_bias()
    if not h:
        return base
    v = base
    if h.get("cort", 0.2) >= 0.45:
        v *= 0.94
    if h.get("dopa", 0.4) >= 0.55:
        v *= 1.03
    return round(max(0.85, min(1.15, v)), 3)


@register(
    PLUGIN_NAME, "zexuan604",
    "把机器人回复的语音换成 GPT-SoVITS 本地音色（只替换 TTS 这一步，不动人格和记忆）",
    "0.1.0",
)
class DafeiyuVoice(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.cfg = config or {}
        self._session: aiohttp.ClientSession | None = None
        logger.info(f"[{PLUGIN_NAME}] 已加载，音色目录={self._c('ref_audio_root', '')}")

    # ------------------------------------------------------------------ 工具

    def _c(self, key, default):
        v = self.cfg.get(key, default)
        return default if v is None else v

    def _ref_path(self, emotion: str) -> tuple[str, str]:
        """返回 (参考音频路径, 参考文本)。目录不存在就退回默认情绪。"""
        root = str(self._c("ref_audio_root", "/AstrBot/data/refaudio"))
        for emo in (emotion, DEFAULT_EMOTION):
            d = os.path.join(root, emo)
            wav = os.path.join(d, "ref.wav")
            if os.path.exists(wav):
                txt = ""
                p = os.path.join(d, "asr.txt")
                if os.path.exists(p):
                    txt = open(p, encoding="utf-8").read().strip()
                return wav, txt or str(self._c("prompt_text", ""))
        return "", ""

    @staticmethod
    def _pick_emotion(text: str) -> str:
        best, score = DEFAULT_EMOTION, 0
        for emo, words in EMOTION_HINTS.items():
            s = sum(1 for w in words if w in text)
            if s > score:
                best, score = emo, s
        # 情绪底色：文字里没明显情绪时，用激素决定
        if score == 0:
            h = _hormone_bias()
            if h:
                if h.get("cort", 0.2) >= 0.45:
                    return "生气" if h.get("cort", 0.2) >= 0.6 else "着急"
                if h.get("oxy", 0.5) >= 0.66:
                    return "害羞"
                if h.get("dopa", 0.4) >= 0.55:
                    return "高兴"
                if h.get("sero", 0.6) < 0.45:
                    return "着急"
        return best

    async def _synth(self, text: str, emotion: str) -> str:
        """调 GPT-SoVITS，返回本地 wav 路径；失败返回空串。"""
        ref, prompt = self._ref_path(emotion)
        if not ref:
            logger.warning(f"[{PLUGIN_NAME}] 找不到参考音频，跳过")
            return ""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        body = {
            "text": text,
            "text_lang": str(self._c("text_lang", "zh")),
            "ref_audio_path": ref,
            "prompt_text": prompt,
            "prompt_lang": str(self._c("prompt_lang", "zh")),
            "text_split_method": "cut5",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
            "speed_factor": _speed_with_mood(SPEED.get(emotion, 1.0)),
        }
        url = str(self._c("api_base", "http://sovits:9880")).rstrip("/") + "/tts"
        t0 = time.time()
        try:
            async with self._session.post(
                url, json=body,
                timeout=aiohttp.ClientTimeout(total=float(self._c("timeout_seconds", 120))),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[{PLUGIN_NAME}] TTS HTTP {resp.status}: "
                                   f"{(await resp.text())[:150]}")
                    return ""
                data = await resp.read()
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] TTS 请求失败: {e!r}")
            return ""
        if len(data) < 1000:
            logger.warning(f"[{PLUGIN_NAME}] 返回内容过小({len(data)}B)，跳过")
            return ""
        d = str(self._c("cache_dir", "/AstrBot/data/temp"))
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"dafeiyu_voice_{uuid.uuid4().hex[:8]}.wav")
        with open(path, "wb") as f:
            f.write(data)
        logger.info(f"[{PLUGIN_NAME}] [{emotion}] 合成 {len(text)} 字 → "
                    f"{len(data)//1024} KB，{time.time()-t0:.1f}s | 原文: {text[:40]}")
        return path

    # ------------------------------------------------------------------ 钩子

    @filter.on_decorating_result(priority=10000)
    async def voice_out(self, event: AstrMessageEvent):
        """出站阶段：按概率把文字换成一条语音。任何异常都放行原文。"""
        try:
            if not bool(self._c("enable", True)):
                return
            # 用户这句话是不是在要语音？
            incoming = str(getattr(event, "message_str", "") or "")
            # 命令回复不转语音（否则 /心智状态 这类长文本会浪费一次合成）
            if incoming.strip().startswith(("/", "／")):
                return
            wants_voice = any(k in incoming for k in VOICE_REQUEST)
            if wants_voice:
                logger.info(f"[{PLUGIN_NAME}] 用户要语音，强制合成")
            else:
                prob = float(self._c("prob", 0.25))
                if prob <= 0 or random.random() >= prob:
                    return

            result = event.get_result()
            chain = getattr(result, "chain", None) if result else None
            if not chain:
                return
            # 太长的不转（语音适合一两句话）
            plain_len = sum(len(getattr(c, "text", "") or "") for c in chain)
            if plain_len > int(self._c("max_chars", 120)):
                return

            texts = [c.text for c in chain if isinstance(c, Plain) and getattr(c, "text", "")]
            text = "".join(texts).strip()
            if not text:
                return
            max_chars = int(self._c("max_chars", 60))
            if len(text) > max_chars:
                text = text[:max_chars]

            emotion = self._pick_emotion(text)
            path = await self._synth(text, emotion)
            if not path:
                return

            # 保留图片/表情等其他组件，只把文字部分换成语音
            others = [c for c in chain if not isinstance(c, Plain)]
            result.chain = [Record.fromFileSystem(path)] + others
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] 出站处理异常（放行原文）: {e!r}")

    async def terminate(self):
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info(f"[{PLUGIN_NAME}] 已卸载")
