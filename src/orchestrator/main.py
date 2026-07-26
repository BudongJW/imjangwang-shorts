"""임장왕 쇼츠 자동 생성·업로드 오케스트레이터.

흐름: 뉴스수집 → 대본생성(Gemini) → TTS → 배경/기사캡처 → 합성 → 업로드.
종료코드: 0 성공, 1 소재 없음, 2 전체 실패(워크플로우가 1회 재시도).
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime

from config.settings import CHANNEL_NAME, FIXED_CTA, DEFAULT_HASHTAGS, AI_THUMBNAIL
from src.collector import news, images
from src.collector.ai_image import generate_background
from src.collector.article_capture import build_article_visual
from src.collector.history import record_topic
from src.script_gen.generator import generate
from src.tts.narrate import narrate
from src.editor.title_card import render_title_card
from src.editor.composer import compose
from src.utils.logger import setup_logger

log = setup_logger("main")


def _build_description(plan, art) -> str:
    tags = " ".join(f"#{t.lstrip('#')}" for t in (plan.hashtags or DEFAULT_HASHTAGS))
    src = f"\n\n출처: {art.source} {art.url}".rstrip() if getattr(art, "url", "") else ""
    return (
        f"{plan.youtube_title}\n\n"
        f"{plan.caption_script}\n\n"
        f"{FIXED_CTA}{src}\n\n{tags}\n\n"
        "※ 본 영상은 공개된 뉴스를 바탕으로 한 정보 제공용이며 투자 권유가 아닙니다."
    )


def _title_with_tags(plan) -> str:
    base = plan.youtube_title.strip()
    tags = " ".join(f"#{t.lstrip('#')}" for t in (plan.hashtags or [])[:3])
    title = f"{base} {tags}".strip()
    return title[:100]  # 유튜브 제목 100자 제한


def run(skip_upload: bool = False) -> int:
    log.info(f"=== {CHANNEL_NAME} 쇼츠 생성 시작 {datetime.now():%Y-%m-%d %H:%M} ===")

    # 1) 뉴스 수집
    candidates = news.collect()
    if not candidates:
        log.error("수집된 뉴스 후보가 없습니다.")
        return 1
    art = news.pick_and_enrich(candidates)
    if not art:
        log.error("선정 가능한 기사가 없습니다.")
        return 1

    # 2) 대본
    plan = generate(art)

    # 3) TTS
    audio = narrate(plan.speech_script)

    # 4) 배경 이미지 + 기사 캡처
    bg_paths = images.collect_backgrounds(getattr(art, "image_url", ""), need=4)
    # AI 썸네일 배경(성공 시 타이틀카드 배경 + 첫 컷으로 사용, 실패 시 폴백)
    ai_bg = generate_background(" ".join(plan.headline) or plan.youtube_title) if AI_THUMBNAIL else None
    if ai_bg:
        bg_paths = [ai_bg] + bg_paths
    title_bg = ai_bg or (bg_paths[0] if bg_paths else None)
    title_card = render_title_card(plan.headline, plan.hook_word, background=title_bg)
    try:
        article_img = build_article_visual(art, highlight=plan.highlight_sentence)
    except Exception as e:
        log.info(f"기사 비주얼 생성 실패(건너뜀): {e}")
        article_img = None

    # 5) 합성
    final = compose(plan.caption_script, audio, title_card, article_img, bg_paths)

    # 6) 업로드
    video_id = ""
    if skip_upload:
        log.info(f"[skip-upload] 검증 완료: {final}")
    else:
        from src.uploader import youtube
        video_id = youtube.upload(
            final,
            title=_title_with_tags(plan),
            description=_build_description(plan, art),
            tags=[t.lstrip("#") for t in (plan.hashtags or DEFAULT_HASHTAGS)],
        )
        # 타이틀카드(AI배경+헤드라인)를 커스텀 썸네일로 설정
        youtube.set_thumbnail(video_id, title_card)

    record_topic(art.title, video_id)
    log.info("=== 완료 ===")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="임장왕 쇼츠 자동화")
    ap.add_argument("--skip-upload", action="store_true", help="업로드 없이 생성만(검증용)")
    args = ap.parse_args()
    try:
        return run(skip_upload=args.skip_upload)
    except Exception:
        log.error("전체 실패:\n" + traceback.format_exc())
        return 2


if __name__ == "__main__":
    sys.exit(main())
