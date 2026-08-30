"""임장왕 쇼츠 자동 생성·업로드 오케스트레이터.

흐름: 뉴스수집 → 대본생성(Gemini) → TTS → 배경/기사캡처 → 합성 → 업로드.
종료코드: 0 성공, 1 소재 없음, 2 전체 실패(워크플로우가 1회 재시도).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime

from config.settings import (
    CHANNEL_NAME, FIXED_CTA, DEFAULT_HASHTAGS, AI_THUMBNAIL,
    POLITICIAN_FACE, POLITICIAN_FACE_ENABLED, GOV_NAME, OUTPUT_DIR,
)
from src.collector import news, images
from src.collector.ai_image import generate_background
from src.collector.article_capture import build_article_visual
from src.collector.history import record_topic
from src.script_gen.generator import generate, ShortPlan
from src.script_gen.correct_terms import to_speech
from src.tts.narrate import narrate
from src.editor.title_card import render_title_card, render_headline_banner, pick_accent
from src.editor.composer import compose
from src.utils.logger import setup_logger

log = setup_logger("main")


# 과거 정부/과거 연도 맥락 — 이 경우 '정부'에 현 정부명을 박으면 오귀속(오정보)이 됨
_PAST_CTX = re.compile(r"(?:19|20)\d{2}|문재인|박근혜|이명박|노무현|전 정부|과거 정부|당시\s*정부")


def _name_government(plan):
    """대본·제목·헤드라인의 '정부'를 정책 비판 대상(이재명 정부)으로 명시한다.

    단, 과거 정부(문재인 등)나 과거 연도 정책을 현 정부가 한 것처럼 오귀속하지 않도록,
    과거 맥락이 감지된 문장에는 '정부'→'이재명 정부' 강제 치환을 하지 않는다(오정보 방지).
    """
    def ng(t: str) -> str:
        t = t.replace("새 정부", GOV_NAME).replace("현 정부", GOV_NAME).replace("현정부", GOV_NAME)
        # 과거 맥락이 없을 때만 첫 '정부'에 현 정부명을 박음
        if GOV_NAME.split()[0] not in t and not _PAST_CTX.search(t):
            t = re.sub(r"정부", GOV_NAME, t, count=1)
        return t
    plan.caption_script = ng(plan.caption_script)
    plan.speech_script = to_speech(plan.caption_script)
    plan.youtube_title = ng(plan.youtube_title)[:40]
    plan.headline = [ng(h) for h in plan.headline]
    return plan


def _load_pinned_plan():
    """output/pinned_plan.json 이 있고 예약일(use_on, KST)이 오늘이면 (art, plan)을 반환한다.

    민감·팩트검증 필요한 주제는 자동선택·Gemini 대신 사람이 직접 검수한 대본을 태우기 위한
    1회용 지정 기능. 예약일이 오늘이 아니면 무시(자동 선택으로 진행), 오류 시에도 무시한다.
    """
    pin = OUTPUT_DIR / "pinned_plan.json"
    if not pin.exists():
        return None
    try:
        from datetime import timezone, timedelta
        data = json.loads(pin.read_text(encoding="utf-8"))
        today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        if data.get("use_on") and data["use_on"] != today:
            log.info(f"지정 대본 예약일({data.get('use_on')})이 오늘({today})이 아님 → 자동 선택")
            return None
        a, p = data["article"], data["plan"]
        art = news.Article(
            title=a.get("title", ""), source=a.get("source", ""),
            url=a.get("url", ""), summary=a.get("summary", ""),
            image_url=a.get("image_url", ""),
        )
        cap = str(p["caption_script"]).strip()
        plan = ShortPlan(
            headline=list(p.get("headline", [])),
            hook_word=p.get("hook_word", ""),
            highlight_sentence=p.get("highlight_sentence", ""),
            caption_script=cap,
            speech_script=to_speech(cap),
            youtube_title=str(p.get("youtube_title", ""))[:40],
            hashtags=list(p.get("hashtags", []) or DEFAULT_HASHTAGS),
        )
        try:
            pin.unlink()   # 1회용(로컬 정리). 러너는 예약일 게이트로 재사용 방지.
        except OSError:
            pass
        return art, plan
    except Exception as e:
        log.info(f"지정 대본 로드 실패(무시하고 자동 선택): {e}")
        return None


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

    # 0) 중복 방지: 오늘(KST) 이미 올린 영상이 있으면 자동 실행을 건너뜀.
    #    (수동 업로드한 날 스케줄이 겹쳐 하루 2개가 되는 것을 막음. 확인 실패 시 그냥 진행.)
    if not skip_upload and os.getenv("SKIP_IF_POSTED_TODAY", "1") == "1":
        from src.uploader.youtube import already_posted_today
        if already_posted_today():
            log.info("오늘 이미 업로드된 영상이 있어 자동 생성을 건너뜁니다(중복 방지).")
            return 0

    # 1~2) 지정 대본(pin)이 오늘용으로 있으면 그것을 사용(사람 검수본), 없으면 자동 수집·생성
    pinned = _load_pinned_plan()
    if pinned:
        art, plan = pinned
        log.info(f"지정 대본 사용(pin): {plan.youtube_title}")
    else:
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
        if POLITICIAN_FACE_ENABLED:          # 정책 비판 대상(이재명 정부) 명시
            plan = _name_government(plan)

    # 3) TTS
    audio = narrate(plan.speech_script)

    # 4) 배경 이미지 + 기사 캡처
    bg_paths = images.collect_backgrounds(getattr(art, "image_url", ""), need=4)
    # AI 썸네일 배경(성공 시 타이틀카드 배경 + 첫 컷으로 사용, 실패 시 폴백)
    ai_bg = generate_background(" ".join(plan.headline) or plan.youtube_title) if AI_THUMBNAIL else None
    if ai_bg:
        bg_paths = [ai_bg] + bg_paths
    title_bg = ai_bg or (bg_paths[0] if bg_paths else None)
    accent = pick_accent()   # 영상마다 액센트 색 변주(획일성 완화)
    # 정책 비판 대상 정치인 얼굴 부각(타이틀카드 + 영상 중간 세그먼트)
    face = POLITICIAN_FACE if (POLITICIAN_FACE_ENABLED and POLITICIAN_FACE.exists()) else None
    title_card = render_title_card(plan.headline, plan.hook_word,
                                   background=title_bg, accent=accent, face=face)
    if face:
        bg_paths = bg_paths + [face]   # 영상 중간에도 얼굴 등장
    # 상단 헤드라인 배너(타이틀카드 이후 전 구간) — 자동 프레임 썸네일 품질 개선
    banner = render_headline_banner(plan.headline, plan.hook_word, accent=accent)
    try:
        article_img = build_article_visual(art, highlight=plan.highlight_sentence)
    except Exception as e:
        log.info(f"기사 비주얼 생성 실패(건너뜀): {e}")
        article_img = None

    # 5) 합성
    final = compose(plan.caption_script, audio, title_card, article_img, bg_paths, banner=banner)

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
