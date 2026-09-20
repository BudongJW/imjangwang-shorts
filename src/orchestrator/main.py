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
    PUBLISH_TARGET_KST, PUBLISH_MIN_LEAD_MIN, SCRIPT_LEN_MODE, BROLL_VIDEO_N,
)
from src.collector import news, images
from src.utils import buildnotes
from src.collector.ai_image import generate_background
from src.collector.article_capture import build_article_visual
from src.collector.history import record_topic
from src.script_gen.generator import generate, ShortPlan
from src.script_gen.correct_terms import to_speech
from src.tts.narrate import narrate
from src.editor.title_card import (render_title_card, resolve_face, face_credit,
                                   layout_by_name,
                                   render_headline_banner, pick_accent)
from src.editor.composer import compose
from src.utils.logger import setup_logger
from src.utils.text import strip_links

log = setup_logger("main")


# 과거 정부/과거 연도 맥락 — 이 경우 '정부'에 현 정부명을 박으면 오귀속(오정보)이 됨
_PAST_CTX = re.compile(r"(?:19|20)\d{2}|문재인|박근혜|이명박|노무현|전 정부|과거 정부|당시\s*정부")


# '정부'가 단어의 일부일 때는 건드리면 안 된다. 그냥 re.sub(r"정부", ...)로
# 두었더니 "천정부지"가 "천이재명 정부지"가 됐다(2026-09-15 실측). 부동산
# 기사에 흔한 표현이라 자주 터진다. 반정부·친정부·정부처·정부미도 같다.
# 앞에 한글이 붙으면 제외하고, 뒤에는 조사만 허용한다.
_GOV_PARTICLES = "가|는|은|이|의|를|도|만|에|와|과|로|으로|부터|까지|라도|마저|조차"
_GOV_RE = re.compile(
    rf"(?<![가-힣])정부(?![가-힣])"
    rf"|(?<![가-힣])정부(?=(?:{_GOV_PARTICLES})(?![가-힣]))")


def _name_government(plan):
    """대본·제목·헤드라인의 '정부'를 정책 비판 대상(이재명 정부)으로 명시한다.

    단, 과거 정부(문재인 등)나 과거 연도 정책을 현 정부가 한 것처럼 오귀속하지 않도록,
    과거 맥락이 감지된 문장에는 '정부'→'이재명 정부' 강제 치환을 하지 않는다(오정보 방지).
    """
    def ng(t: str) -> str:
        t = t.replace("새 정부", GOV_NAME).replace("현 정부", GOV_NAME).replace("현정부", GOV_NAME)
        # 과거 맥락이 없을 때만 첫 '정부'에 현 정부명을 박음
        if GOV_NAME.split()[0] not in t and not _PAST_CTX.search(t):
            t = _GOV_RE.sub(GOV_NAME, t, count=1)
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
            layout=str(p.get("layout", "")).strip(),
        )
        # 지정 대본이 공개 시각을 직접 고를 수 있게 한다("HH:MM" KST).
        # 특정 시간대를 노린 기획물은 08:40 기본값이 맞지 않는다.
        plan.publish_at_kst = str(p.get("publish_at_kst", "")).strip()
        # 얼굴 지정: "none"이면 인물 사진을 아예 안 넣고, 파일명이면 그 사진을 쓴다.
        # 주제 인물이 이재명이 아닌 날(예: 다른 인사 의혹)에 엉뚱한 얼굴이
        # 붙는 것을 막는다. 미지정이면 기존 날짜 회전을 그대로 쓴다.
        face_pin = str(p.get("face", "")).strip()
        try:
            pin.unlink()   # 1회용(로컬 정리). 러너는 예약일 게이트로 재사용 방지.
        except OSError:
            pass
        return art, plan, face_pin
    except Exception as e:
        log.info(f"지정 대본 로드 실패(무시하고 자동 선택): {e}")
        return None


def _build_description(plan, art, face=None) -> str:
    tags = " ".join(f"#{t.lstrip('#')}" for t in (plan.hashtags or DEFAULT_HASHTAGS))
    # 출처는 매체명 + 기사 제목으로만 쓴다. URL은 넣지 않는다.
    #
    # 2026-09-19 유튜브 조치: 09-19 업로드(1GWtMyDh3Ic)의 설명에 있던
    # 뉴시스 기사 URL이 "스팸, 현혹 행위, 사기에 대한 정책" 위반으로
    # 삭제됐다. 채널 경고는 없었지만 링크는 제거됐다.
    #
    # 이 채널은 160편 전부가 같은 자리에 외부 기사 링크를 달고 있었다.
    # 매일 같은 형태로 외부 링크를 붙이는 것 자체가 링크 스팸 신호이고,
    # 그건 영상 하나가 아니라 채널 단위로 도달에 걸린다. 실제로 09-14
    # 이후 Shorts 피드 유입이 비중은 97%로 유지된 채 절대량만 1/8로
    # 줄었다(1,046 → 136 → 140).
    #
    # 출처 표시는 링크가 아니라 사실로 하는 것이다. 매체명과 기사 제목이면
    # 독자가 원문을 찾을 수 있고, 인용 표시 의무도 충족된다.
    if getattr(art, "source", "") or getattr(art, "title", ""):
        head = (art.title or "").strip().replace("\n", " ")
        if len(head) > 60:
            head = head[:60].rstrip() + "…"
        bits = " ".join(x for x in [art.source, f"「{head}」" if head else ""] if x)
        src = f"\n\n출처: {bits}".rstrip()
    else:
        src = ""
    # 인물 사진 출처 — KOGL 1유형·CC BY 계열은 표시가 의무다
    credit = face_credit(face)
    photo = f"\n사진: {credit}" if credit else ""
    body = (
        f"{plan.youtube_title}\n\n"
        f"{plan.caption_script}\n\n"
        f"{FIXED_CTA}{src}{photo}\n\n{tags}\n\n"
        "※ 본 영상은 공개된 뉴스를 바탕으로 한 정보 제공용이며 투자 권유가 아닙니다."
    )
    # 마지막 방어선. 대본은 모델이 쓰기 때문에 본문에 링크가 섞여 들어올 수
    # 있고, 설명에 외부 링크가 하나라도 남으면 같은 조치를 또 받는다.
    return strip_links(body)


def _review_summary(plan, art, video_id: str, final) -> None:
    """검토에 필요한 것만 Actions 요약 탭에 쓴다.

    승인하는 사람이 휴대폰으로 5분 안에 읽고 판단할 수 있어야 한다.
    대본 전문과 원문 대조에 필요한 것만 싣고 나머지는 로그에 둔다.
    """
    lines = [
        f"# 검토 대기 — {plan.youtube_title}",
        "",
        f"**비공개로 올라가 있다.** 승인해야 공개된다. `{video_id}`",
        "",
        f"- 원문: {art.source} 「{art.title}」",
        f"- {art.url}" if getattr(art, "url", "") else "",
        f"- 대본 {len(plan.caption_script or '')}자",
        "",
        "## 대본 전문",
        "",
        plan.caption_script or "(없음)",
        "",
        "## 승인하려면",
        "",
        "`output/admin_request.json` 에 아래를 적고 `run/admin-*` 브랜치를 푸시한다.",
        "",
        "```json",
        f'{{"action": "approve", "video_id": "{video_id}"}}',
        "```",
        "",
        "버리려면 `\"action\": \"delete\"`.",
    ]
    text = "\n".join(x for x in lines if x is not None)
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    log.info(f"검토 대기 — 비공개 업로드 완료: {video_id}")


_QUOTE_RE = re.compile(r'["\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f\']')


def _title_style(title: str) -> str:
    """제목이 인용형인지 주장형인지 기록한다.

    2026-09-09에 규칙 12를 "따옴표 인용 + 발언 주체" 형식으로 바꿨다.
    경쟁 채널 조사가 근거였는데, 같은 뉴스에서 정치대학 20만 vs 우리
    1,100이라는 한 건 비교였고 구독자 규모(7.9만 vs 우리) 차이가 통제되지
    않았다. 표본 1로 제목 문법을 통째로 바꾼 셈이다.

    성적 비교는 지금 불가능하다. 규칙 변경(09-09)이 토큰 만료로 8일간
    업로드가 끊긴 구간(09-02~09-09) 바로 뒤에 얹혀 있고, 그 다음엔
    도달 붕괴(09-14~)가 왔다. 어느 쪽도 제목 탓으로 못 돌린다.

    그래서 되돌리지도, 밀어붙이지도 않는다. 영상마다 어느 문법이었는지만
    박아 두면 도달이 회복된 뒤에 갈라 볼 수 있다. 날짜로 추정하면 지정
    대본·수동 재업로드 한 번에 경계가 무너진다.

      주장형  이재명 정부 1년, 서울 집값 14% 폭등 미스터리!
      인용형  뉴시스 "서울 월세 160만원 시대"
    """
    return "인용" if _QUOTE_RE.search(title or "") else "주장"


def _title_with_tags(plan) -> str:
    base = plan.youtube_title.strip()
    tags = " ".join(f"#{t.lstrip('#')}" for t in (plan.hashtags or [])[:3])
    title = f"{base} {tags}".strip()
    return title[:100]  # 유튜브 제목 100자 제한


def _publish_at(target_kst: str = "") -> str | None:
    """오늘(KST) 목표 시각의 RFC3339 UTC 문자열. 이미 지났으면 None(즉시 공개).

    스케줄 지연을 크론 분으로 보정하려 했는데 오프셋이 일정하지 않았다.
      예정 00:23 UTC → 지연 261~272분 (3일)
      예정 19:18 UTC → 지연 128분
    시간대마다 다르니 크론으로는 30분짜리 창을 못 맞춘다. 그래서 게시
    시각 자체를 YouTube 예약 공개로 고정한다. 워크플로가 몇 시에 돌든
    영상은 목표 시각에 공개된다.

    날짜를 넘기지 않는다. 목표가 이미 지났으면 내일로 미루지 않고 그냥
    즉시 공개한다. 업로드 날짜와 공개 날짜가 갈리면 '오늘 이미 올렸나'
    가드가 어긋나 하루 2개가 나갈 수 있다.
    """
    from datetime import timezone, timedelta
    kst = timezone(timedelta(hours=9))
    want = target_kst or PUBLISH_TARGET_KST
    try:
        hh, mm = (int(x) for x in want.split(":"))
    except ValueError:
        log.warning(f"공개 시각 형식 오류({want!r}) → 즉시 공개")
        return None
    now = datetime.now(kst)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target - now < timedelta(minutes=PUBLISH_MIN_LEAD_MIN):
        log.info(f"  게시: 즉시 공개 (목표 {want} KST가 이미 지났거나 "
                 f"{PUBLISH_MIN_LEAD_MIN}분 미만 남음)")
        return None
    log.info(f"  게시: {target:%H:%M} KST 예약 공개 "
             f"(지금 {now:%H:%M}, {(target - now).total_seconds() / 60:.0f}분 뒤)")
    return target.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _posted_today_in_history() -> bool:
    """output/topic_history.json에 오늘(KST) 업로드 기록이 있으면 True.

    YouTube API 확인이 실패했을 때 쓰는 2차 방어선. 러너가 업로드 직후
    커밋하는 파일이라 같은 날 앞선 슬롯의 결과가 남아 있다.
    """
    from datetime import timezone, timedelta
    path = OUTPUT_DIR / "topic_history.json"
    if not path.exists():
        return False
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    return any(r.get("video_id") and str(r.get("date", "")).startswith(today)
               for r in rows if isinstance(r, dict))


def run(skip_upload: bool = False) -> int:
    log.info(f"=== {CHANNEL_NAME} 쇼츠 생성 시작 {datetime.now():%Y-%m-%d %H:%M} ===")
    buildnotes.reset()

    # 0) 중복 방지: 오늘(KST) 이미 올린 영상이 있으면 자동 실행을 건너뜀.
    #    스케줄 지연이 4시간 넘게 일정해 슬롯을 늘려 보정하는데, 슬롯이 여럿이면
    #    가드가 한 번 헛돌 때마다 그만큼 중복 업로드가 나간다. 그래서 확인에
    #    실패하면 '올린 적 없다'로 넘기지 않고 건너뛴다. 하루 빠지는 것은
    #    force 브랜치로 되돌릴 수 있지만, 중복 업로드는 계정이 걸린다.
    if not skip_upload and os.getenv("SKIP_IF_POSTED_TODAY", "1") == "1":
        from src.uploader.youtube import already_posted_today
        posted = already_posted_today()
        if posted:
            log.info("오늘 이미 업로드된 영상이 있어 자동 생성을 건너뜁니다(중복 방지).")
            return 0
        if posted is None:
            # API로 확인이 안 됐다. 로컬 이력으로 한 번 더 본다 — 러너가 업로드
            # 직후 커밋해 두는 파일이라, 같은 날 앞 슬롯이 올렸으면 여기 남는다.
            if _posted_today_in_history():
                log.info("오늘자 업로드 이력이 있어 건너뜁니다(API 확인 실패 → 로컬 이력).")
                return 0
            log.error("업로드 여부를 확인할 수 없어 중복 방지를 위해 건너뜁니다. "
                      "다시 올리려면 run/daily-force-* 브랜치로 실행하세요.")
            return 0

    # 1~2) 지정 대본(pin)이 오늘용으로 있으면 그것을 사용(사람 검수본), 없으면 자동 수집·생성
    face_pin = ""
    pinned = _load_pinned_plan()
    if pinned:
        art, plan, face_pin = pinned
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
    audio, srt = narrate(plan.speech_script)

    # 4) 배경 이미지 + 기사 캡처
    bg_paths = images.collect_backgrounds(getattr(art, "image_url", ""), need=4)
    # 실사 b-roll을 섞는다. "정적 이미지 루프"는 유튜브가 AI 양산 채널을
    # 가려내는 지표로 직접 지목한 형태고, 지금 배경은 사진에 켄번즈만 건
    # 것이라 정확히 그 모양이다. Pexels 영상은 사진과 같은 라이선스라
    # 상업적 이용이 되고 출처 표기 의무도 없다.
    #
    # 전부 영상으로 바꾸지는 않는다. 켄번즈 사진과 번갈아 나오는 편이
    # 화면 변화가 크고, 받아오는 데 실패해도 그대로 굴러간다.
    if BROLL_VIDEO_N > 0:
        try:
            brolls = images.collect_video_broll(need=BROLL_VIDEO_N)
        except Exception as e:
            log.info(f"b-roll 영상 확보 실패(사진만 사용): {e}")
            brolls = []
        if brolls:
            mixed: list = []
            for i, bg in enumerate(bg_paths):
                mixed.append(bg)
                if i < len(brolls):
                    mixed.append(brolls[i])
            bg_paths = mixed
    # AI 썸네일 배경(성공 시 타이틀카드 배경 + 첫 컷으로 사용, 실패 시 폴백)
    ai_bg = generate_background(" ".join(plan.headline) or plan.youtube_title) if AI_THUMBNAIL else None
    if ai_bg:
        bg_paths = [ai_bg] + bg_paths
    title_bg = ai_bg or (bg_paths[0] if bg_paths else None)
    accent = pick_accent()   # 영상마다 액센트 색 변주(획일성 완화)
    # 정책 비판 대상 정치인 얼굴 부각(타이틀카드 + 영상 중간 세그먼트)
    # 기사에 실제로 나오는 인물의 사진만 쓴다. 발언자와 화면 속 인물이
    # 다르면 시청자에게는 그 자체가 허위로 읽힌다(2026-09-11분 사례).
    article_text = f"{art.title} {art.summary}"
    face = resolve_face(face_pin, article_text) if POLITICIAN_FACE_ENABLED else None
    log.info(f"  얼굴: {face.name if face else '없음'}"
             + (f" (지정: {face_pin})" if face_pin else ""))
    lay = layout_by_name(plan.layout) if getattr(plan, "layout", "") else None
    if plan.layout and lay is None:
        log.warning(f"지정 구도 '{plan.layout}'를 찾지 못해 날짜 회전을 씁니다")
    title_card = render_title_card(plan.headline, plan.hook_word,
                                   background=title_bg, accent=accent, face=face,
                                   layout=lay)
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
    buildnotes.note(f"대본 {len(plan.caption_script)}자: {plan.caption_script}")
    final = compose(plan.caption_script, audio, title_card, article_img, bg_paths,
                    banner=banner, srt_path=srt)

    # 6) 업로드
    #
    # REVIEW_MODE(기본 켬)에서는 비공개로 올리고 사람이 승인해야 공개된다.
    #
    # 유튜브가 2026년에 정리한 채널들의 공통점은 "사람의 편집 판단 없이
    # 기계가 끝까지 내보내는 것"이었다. 이 파이프라인의 진짜 가치는 앞단
    # (수집·선정·본문 추출·수치 대조)에 있고, 뒷단을 사람 없이 내보내는
    # 것은 이득이 아니라 위험이다. 실제로 09-19 영상은 폴백 대본이
    # 기자 바이라인과 잘린 수치를 그대로 읽었는데 아무도 못 막았다.
    #
    # 승인은 run/admin-* 브랜치의 {"action":"approve","video_id":"..."} 로 한다.
    review = os.getenv("REVIEW_MODE", "1") != "0"
    # 지정 대본이 공개 시각을 콕 집었으면 예약 공개를 쓴다. 예약 공개는
    # 그 시각까지 비공개라 검토 시간이 그대로 남고, 사람이 시각을 직접
    # 고른 것 자체가 편집 판단이다.
    pinned_time = getattr(plan, "publish_at_kst", "")
    if review and pinned_time:
        log.info(f"  검토 대기 해제: 지정 대본이 {pinned_time} KST 예약 공개를 지정")
        review = False
    video_id = ""
    if skip_upload:
        log.info(f"[skip-upload] 검증 완료: {final}")
    else:
        from src.uploader import youtube
        video_id = youtube.upload(
            final,
            title=_title_with_tags(plan),
            description=_build_description(plan, art, face=face),
            tags=[t.lstrip("#") for t in (plan.hashtags or DEFAULT_HASHTAGS)],
            publish_at=None if review else _publish_at(pinned_time),
            privacy="private" if review else None,
        )
        # 타이틀카드(AI배경+헤드라인)를 커스텀 썸네일로 설정
        youtube.set_thumbnail(video_id, title_card)
        if review:
            _review_summary(plan, art, video_id, final)

    record_topic(art.title, video_id,
                 title_style=_title_style(plan.youtube_title),
                 len_mode=SCRIPT_LEN_MODE,
                 script_chars=len(plan.caption_script or ""),
                 source=getattr(art, "source", "") or None,
                 age_days=getattr(art, "pick_age_days", None),
                 topic_score=getattr(art, "pick_topic_score", None),
                 recency_score=getattr(art, "pick_recency_score", None))
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
