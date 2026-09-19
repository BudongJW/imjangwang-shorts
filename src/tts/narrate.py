"""뉴스 앵커 톤 고정 나레이션 래퍼.

분석 결과 '신뢰감 있는 남성 앵커 톤'이 부동산 정보 콘텐츠에 적합.
기본 음성을 ko-KR-InJoonNeural로 고정한다(개선안 반영, 채널 톤 일관성).
"""

from pathlib import Path

from src.tts import azure_tts_engine
from src.tts.edge_tts_engine import synthesize as edge_synthesize
from src.utils.logger import setup_logger

log = setup_logger("narrate")

ANCHOR_VOICE = "ko-KR-InJoonNeural"   # 차분·신뢰감
ANCHOR_RATE = "+6%"                    # 쇼츠 템포에 맞춰 약간 빠르게


def narrate(speech_text: str, filename: str = "narration") -> tuple[Path, Path]:
    """발음 교정된 텍스트로 음성을 생성하고 (mp3, srt) 경로를 반환한다.

    edge-tts는 합성하면서 WordBoundary를 함께 주고, edge_tts_engine이 그것을
    SRT로 적어 둔다. 예전에는 그 SRT를 버리고 자막 타이밍을 글자수 비례로
    다시 만들었다. 실제 발화 시각이 이미 있는데 추정치를 쓴 셈이다.

    ANCHOR_VOICE는 원래 Azure 목소리다. AZURE_SPEECH_KEY가 있으면 공식
    API로 같은 목소리를 쓰고, 없으면 edge-tts로 떨어진다. 소리는 같다.

    공식 API를 쓰는 이유는 권리다. edge-tts는 엣지 브라우저 '읽어주기'
    엔드포인트를 역공학한 것이라 Azure 구독 없는 상업적 사용은 약관
    위반이라는 것이 MS의 답변이고, 유튜브 수익 창출 정책은 콘텐츠의 모든
    시청각 요소에 대한 상업적 사용 권리를 요구한다.
    """
    if azure_tts_engine.available():
        try:
            audio_path, srt_path, meta = azure_tts_engine.synthesize(
                speech_text, filename=filename,
                voice=ANCHOR_VOICE, rate=ANCHOR_RATE, language="ko",
            )
            log.info(f"  TTS: Azure ({meta['voice']})")
            return audio_path, srt_path
        except Exception as e:
            # 키 만료·할당량·네트워크. 영상이 통째로 안 나가는 것보다는
            # edge-tts로라도 나가는 편이 낫다. 대신 로그에 남긴다.
            log.warning(f"  TTS: Azure 실패 → edge-tts로 대체: {str(e)[:160]}")

    audio_path, srt_path, _meta = edge_synthesize(
        speech_text, filename=filename,
        voice=ANCHOR_VOICE, rate=ANCHOR_RATE, language="ko",
    )
    log.info("  TTS: edge-tts")
    return audio_path, srt_path
