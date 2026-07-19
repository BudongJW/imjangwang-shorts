"""Gemini 기반 대본 생성.

분석에서 확인된 '먹히는 공식'을 프롬프트로 강제한다:
  - 훅 → 수치 근거 → 해석 → 전망/결론 (앵커식)
  - 구체적 지역/물건 + 방향성 + 숫자 후킹 (TOP 영상 패턴)
  - 정치색 톤다운(정당·정치인 저격 대신 '정책이 내 집 마련에 미치는 영향' 각도) — 개선안 ⑤
산출은 JSON(headline/hook_word/highlight_sentence/script/youtube_title/hashtags).
Gemini 실패 시 기사 메타 기반 템플릿으로 폴백한다.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field

from config.settings import GEMINI_API_KEYS, GEMINI_MODEL, DEFAULT_HASHTAGS, FIXED_CTA
from src.script_gen.correct_terms import normalize_caption, to_speech
from src.utils.logger import setup_logger

log = setup_logger("script_gen")

_key_cycle = itertools.cycle(GEMINI_API_KEYS) if GEMINI_API_KEYS else None


@dataclass
class ShortPlan:
    headline: list[str]          # 타이틀 카드용 2~3줄
    hook_word: str               # 헤드라인 강조어(노란색)
    highlight_sentence: str      # 기사 캡처 하이라이트용 핵심 문장
    caption_script: str          # 화면 자막/나레이션 원문(정확 표기)
    speech_script: str           # TTS 입력(발음형)
    youtube_title: str
    hashtags: list[str] = field(default_factory=list)
    cta: str = FIXED_CTA


PROMPT = """당신은 한국 부동산 유튜브 쇼츠 대본 작가입니다.
아래 뉴스 기사를 바탕으로 45~55초 분량 쇼츠 대본을 만드세요.

[기사]
제목: {title}
출처: {source}
본문: {summary}

[반드시 지킬 규칙]
1. 구조: 훅(첫 문장 3초 안에 궁금증/충격) → 수치 근거 → 해석 → 전망/결론.
2. 구체적 지역·단지·물건명과 '숫자'를 넣어 신뢰와 후킹을 동시에.
3. 톤: 뉴스 앵커처럼 단정적이되, 특정 정당·정치인 저격/비난은 금지.
   정책은 '내 집 마련·전세·투자에 미치는 실질 영향' 관점으로만 다룬다.
4. 문장은 짧고 끊어읽기 좋게. 과장·허위·확정적 투자권유 금지.
5. 사실이 불확실하면 단정하지 말 것.

[출력: 아래 JSON만, 다른 텍스트 없이]
{{
  "headline": ["타이틀 1줄", "타이틀 2줄", "(선택)3줄"],   // 각 줄 12자 이내, 총 2~3줄
  "hook_word": "헤드라인에서 노랗게 강조할 핵심 단어 1개",
  "highlight_sentence": "기사에서 형광펜 칠할 핵심 한 문장(20자 내외)",
  "script": "본문 나레이션 전체(320~380자, 위 구조).",
  "youtube_title": "클릭 유도형 제목(35자 이내, 해시태그 제외)",
  "hashtags": ["부동산","집값","..."]
}}
"""


def _gemini(prompt: str) -> str | None:
    if not _key_cycle:
        return None
    try:
        import google.generativeai as genai
    except Exception:
        log.info("google-generativeai 미설치 → 폴백")
        return None
    last = None
    for _ in range(min(3, len(GEMINI_API_KEYS))):
        key = next(_key_cycle)
        try:
            genai.configure(api_key=key)
            model = genai.GenerativeModel(GEMINI_MODEL)
            resp = model.generate_content(prompt)
            return resp.text
        except Exception as e:  # 키 소진/쿼터 → 다음 키
            last = e
            log.info(f"  Gemini 키 실패, 로테이션: {e}")
    log.info(f"  Gemini 전체 실패: {last}")
    return None


def _parse_json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _fallback_plan(art) -> ShortPlan:
    """Gemini 실패 시 기사 메타로 최소 대본 구성."""
    title = getattr(art, "title", "부동산 시장 이슈")
    summary = getattr(art, "summary", "") or title
    words = title.split()
    hl = words[0] if words else "부동산"
    head = _split_headline(title)
    script = (
        f"{title}. {summary[:220]} "
        "지금 시장 흐름을 놓치면 내 집 마련 타이밍도 달라질 수 있습니다. "
        "앞으로의 방향, 꼭 확인해 두세요."
    )
    return ShortPlan(
        headline=head,
        hook_word=hl,
        highlight_sentence=title[:24],
        caption_script=normalize_caption(script),
        speech_script=to_speech(script),
        youtube_title=title[:35],
        hashtags=DEFAULT_HASHTAGS,
    )


def _split_headline(text: str, per_line: int = 12, max_lines: int = 3) -> list[str]:
    text = normalize_caption(text)[: per_line * max_lines]
    lines, cur = [], ""
    for tok in text.split():
        if len(cur) + len(tok) + 1 <= per_line:
            cur = (cur + " " + tok).strip()
        else:
            lines.append(cur)
            cur = tok
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines[:max_lines] or [text[:per_line]]


def generate(art) -> ShortPlan:
    prompt = PROMPT.format(
        title=getattr(art, "title", ""),
        source=getattr(art, "source", ""),
        summary=(getattr(art, "summary", "") or getattr(art, "title", ""))[:1200],
    )
    raw = _gemini(prompt)
    data = _parse_json(raw) if raw else None
    if not data or not data.get("script"):
        log.info("대본: 폴백 사용")
        return _fallback_plan(art)

    script = normalize_caption(str(data["script"]).strip())
    headline = [normalize_caption(h) for h in (data.get("headline") or [])][:3]
    if not headline:
        headline = _split_headline(getattr(art, "title", "부동산 뉴스"))
    plan = ShortPlan(
        headline=headline,
        hook_word=normalize_caption(str(data.get("hook_word", headline[0].split()[0] if headline else ""))),
        highlight_sentence=normalize_caption(str(data.get("highlight_sentence", ""))[:30]),
        caption_script=script,
        speech_script=to_speech(script),
        youtube_title=normalize_caption(str(data.get("youtube_title", getattr(art, "title", "")))[:40]),
        hashtags=(data.get("hashtags") or DEFAULT_HASHTAGS)[:8],
    )
    log.info(f"대본 생성 완료: {plan.youtube_title}")
    return plan
