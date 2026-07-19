"""뉴스 앵커 톤 고정 나레이션 래퍼.

분석 결과 '신뢰감 있는 남성 앵커 톤'이 부동산 정보 콘텐츠에 적합.
기본 음성을 ko-KR-InJoonNeural로 고정한다(개선안 반영, 채널 톤 일관성).
"""

from pathlib import Path

from src.tts.edge_tts_engine import synthesize

ANCHOR_VOICE = "ko-KR-InJoonNeural"   # 차분·신뢰감
ANCHOR_RATE = "+6%"                    # 쇼츠 템포에 맞춰 약간 빠르게


def narrate(speech_text: str, filename: str = "narration") -> Path:
    """발음 교정된 텍스트로 음성을 생성하고 mp3 경로를 반환한다."""
    audio_path, _srt, _meta = synthesize(
        speech_text, filename=filename,
        voice=ANCHOR_VOICE, rate=ANCHOR_RATE, language="ko",
    )
    return audio_path
