"""Edge TTS 기반 음성 합성 + SRT 자막 생성."""

import asyncio
import random
from pathlib import Path

import aiohttp
import edge_tts
import edge_tts.communicate as _edge_comm
from edge_tts.constants import TRUSTED_CLIENT_TOKEN

from config.settings import AUDIO_DIR, SRT_DIR

# 합성 웹소켓 주소. 2026-10-10부터 Microsoft가 옛 주소(speech.platform.bing.com)의
# 합성 연결을 403으로 막았다(rany2/edge-tts#490). 10-13 정기 제작이 이 403으로 멈췄다.
# Edge가 지금 쓰는 api.msedgeservices.com은 같은 토큰을 받는다(같은 저장소 PR #491).
# edge-tts 7.3.1은 옛 주소에 새 브라우저 판 값을 실어 다시 열었고, 10-17 제작에서는
# 오히려 새 주소가 429를 냈다. 그래서 라이브러리 기본 주소를 먼저 쓰고, 막히면
# 새 주소로 한 번 더 간다.
_EDGE_WSS = ("wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
             f"?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}")
WSS_CANDIDATES = tuple(dict.fromkeys((_edge_comm.WSS_URL, _EDGE_WSS)))

# 한국어 음성 목록 (이름, 성별, 특성)
KO_VOICES = [
    ("ko-KR-SunHiNeural", "female", "밝고 명랑"),
    ("ko-KR-InJoonNeural", "male", "차분하고 신뢰감"),
    ("ko-KR-HyunsuMultilingualNeural", "male", "또렷하고 자연스러움"),
]

# 영어 음성 목록 (cat facts용). TikTok·Shorts에서 인기 있는 톤 기준 선별.
EN_VOICES = [
    ("en-US-AriaNeural", "female", "warm, story-telling"),
    ("en-US-JennyNeural", "female", "casual, friendly"),
    ("en-US-GuyNeural", "male", "neutral, informative"),
    ("en-US-ChristopherNeural", "male", "deep, authoritative"),
    ("en-GB-SoniaNeural", "female", "British, crisp"),
]

# 속도 변화 옵션
RATE_OPTIONS = ["+0%", "+5%", "+10%", "-5%"]


def pick_voice(gender: str | None = None, language: str = "ko") -> tuple[str, str]:
    """음성을 선택한다.

    Args:
        gender: "male", "female", 또는 None (랜덤)
        language: "ko" | "en"

    Returns:
        (voice_name, rate) 튜플
    """
    pool = EN_VOICES if language == "en" else KO_VOICES
    if gender:
        pool = [v for v in pool if v[1] == gender]

    voice_name, _, _ = random.choice(pool)
    rate = random.choice(RATE_OPTIONS)
    return voice_name, rate


async def _synthesize(
    text: str,
    audio_path: Path,
    srt_path: Path,
    voice: str | None = None,
    rate: str | None = None,
    language: str = "ko",
) -> dict:
    """Edge TTS로 음성과 자막을 동시에 생성한다."""
    if voice is None or rate is None:
        voice, rate = pick_voice(language=language)

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.parent.mkdir(parents=True, exist_ok=True)

    last_err: Exception | None = None
    for url in WSS_CANDIDATES:
        _edge_comm.WSS_URL = url
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        submaker = edge_tts.SubMaker()
        try:
            with open(audio_path, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                        submaker.feed(chunk)
        except (aiohttp.WSServerHandshakeError, edge_tts.exceptions.NoAudioReceived) as e:
            print(f"  edge-tts 연결 실패({url.split('/')[2]}): {type(e).__name__} {str(e)[:60]}")
            last_err = e
            continue
        srt_path.write_text(submaker.get_srt(), encoding="utf-8")
        return {"voice": voice, "rate": rate, "endpoint": url.split("/")[2]}
    raise last_err or RuntimeError("edge-tts: 합성 주소가 없다")


def synthesize(
    text: str,
    filename: str = "narration",
    voice: str | None = None,
    rate: str | None = None,
    language: str = "ko",
) -> tuple[Path, Path, dict]:
    """음성 + 자막 생성 동기 진입점.

    Args:
        text: 읽을 텍스트
        filename: 출력 파일명
        voice: 음성 이름 (None이면 랜덤)
        rate: 속도 (None이면 랜덤)
        language: "ko" | "en" — voice 미지정 시 풀 선택에 사용.

    Returns:
        (audio_path, srt_path, meta) 튜플. meta에 사용된 voice/rate 포함.
    """
    audio_path = AUDIO_DIR / f"{filename}.mp3"
    srt_path = SRT_DIR / f"{filename}.srt"
    meta = asyncio.run(_synthesize(text, audio_path, srt_path, voice, rate, language))
    return audio_path, srt_path, meta
