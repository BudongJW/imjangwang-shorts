"""Azure AI Speech 기반 음성 합성 + SRT 자막 생성.

edge-tts와 같은 인터페이스(synthesize)를 제공한다. 바꾸는 이유는 두 가지다.

1) 권리
   edge-tts는 엣지 브라우저의 '읽어주기' 엔드포인트를 역공학한 것이다.
   Azure 구독 없이 상업적으로 쓰는 것은 마이크로소프트 약관 위반이라는
   것이 MS Q&A의 답변이고, 유튜브 수익 창출 정책은 "콘텐츠의 모든 시청각
   요소에 대한 상업적 사용 권리"를 명시적으로 요구한다. 수익화를 유지할
   생각이면 여기를 정리해 두는 편이 낫다.

2) 품질
   공식 API는 SSML을 받는다. 끊어 읽기와 강조를 넣을 수 있어 나레이션이
   평평하지 않게 된다. edge-tts는 rate 말고는 손댈 수 있는 게 없다.

목소리(ko-KR-InJoonNeural)는 원래 Azure 목소리라 그대로 쓴다. 소리는
바뀌지 않는다. 무료 등급이 월 50만 자이고 이 채널은 월 1만 자 정도라
비용도 0원이다.

SRT는 edge-tts와 마찬가지로 실제 발화 시각에서 만든다. Azure SDK는
단어 경계 이벤트를 주므로 그것을 문장 단위로 묶는다 — composer가
누적 글자 위치를 시간축에 투영하는 방식이라 묶는 단위는 자유롭다.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

from config.settings import AUDIO_DIR, SRT_DIR

# 100ns 틱 → 초
_TICKS = 10_000_000

# 문장 끝으로 볼 문자. 여기서 cue를 끊는다.
_SENT_END = (".", "!", "?", "…")


def available() -> bool:
    """Azure 키가 있고 SDK를 import할 수 있으면 True."""
    if not os.getenv("AZURE_SPEECH_KEY"):
        return False
    try:
        import azure.cognitiveservices.speech  # noqa: F401
    except Exception:
        return False
    return True


def _srt_time(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}"


def _cues_from_words(words: list[tuple[str, float, float]]
                     ) -> list[tuple[str, float, float]]:
    """단어 경계를 문장 단위 cue로 묶는다."""
    cues: list[tuple[str, float, float]] = []
    buf: list[str] = []
    start = 0.0
    for text, st, en in words:
        if not buf:
            start = st
        buf.append(text)
        if text.endswith(_SENT_END) or sum(len(x) for x in buf) >= 40:
            cues.append((" ".join(buf), start, en))
            buf = []
    if buf:
        cues.append((" ".join(buf), start, words[-1][2] if words else 0.0))
    return cues


def _write_srt(cues: list[tuple[str, float, float]], path: Path) -> None:
    out = []
    for i, (text, st, en) in enumerate(cues, 1):
        out.append(f"{i}\n{_srt_time(st)} --> {_srt_time(max(en, st + 0.05))}\n{text}\n")
    path.write_text("\n".join(out), encoding="utf-8")


def _ssml(text: str, voice: str, rate: str) -> str:
    """읽을 텍스트를 SSML로 감싼다.

    문장 사이에 짧은 숨을 넣는다. 뉴스 나레이션은 문장이 붙어 나가면
    숫자가 귀에 안 걸린다. 본문은 escape 해서 대본 속 따옴표·부등호가
    SSML 태그로 해석되지 않게 한다.
    """
    body = html.escape(text, quote=False)
    body = re.sub(r"(?<=[.!?])\s+", '<break time="180ms"/> ', body)
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xml:lang="ko-KR">'
        f'<voice name="{voice}">'
        f'<prosody rate="{rate}">{body}</prosody>'
        "</voice></speak>"
    )


def synthesize(
    text: str,
    filename: str = "narration",
    voice: str = "ko-KR-InJoonNeural",
    rate: str = "+0%",
    language: str = "ko",
) -> tuple[Path, Path, dict]:
    """음성 + 자막 생성. edge_tts_engine.synthesize와 같은 반환 형식."""
    import azure.cognitiveservices.speech as speechsdk

    key = os.environ["AZURE_SPEECH_KEY"]
    region = os.getenv("AZURE_SPEECH_REGION", "koreacentral")

    audio_path = AUDIO_DIR / f"{filename}.mp3"
    srt_path = SRT_DIR / f"{filename}.srt"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = speechsdk.SpeechConfig(subscription=key, region=region)
    cfg.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio48Khz96KBitRateMonoMp3
    )
    out = speechsdk.audio.AudioOutputConfig(filename=str(audio_path))
    synth = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=out)

    words: list[tuple[str, float, float]] = []

    def _on_word(evt) -> None:
        # audio_offset은 100ns 틱, duration은 timedelta
        st = evt.audio_offset / _TICKS
        try:
            dur = evt.duration.total_seconds()
        except AttributeError:
            dur = 0.0
        words.append((evt.text, st, st + dur))

    synth.synthesis_word_boundary.connect(_on_word)

    result = synth.speak_ssml_async(_ssml(text, voice, rate)).get()
    reason = result.reason
    if reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        detail = ""
        if reason == speechsdk.ResultReason.Canceled:
            c = result.cancellation_details
            detail = f" {c.reason}: {c.error_details}"
        raise RuntimeError(f"Azure TTS 실패({reason}){detail}")

    _write_srt(_cues_from_words(words), srt_path)
    return audio_path, srt_path, {"voice": voice, "rate": rate, "engine": "azure"}
