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

from config.settings import (
    GEMINI_API_KEYS, GEMINI_MODEL, GEMINI_FALLBACK_MODELS, DEFAULT_HASHTAGS, FIXED_CTA,
    SCRIPT_CHARS_MIN, SCRIPT_CHARS_MAX, SCRIPT_CHARS_CAP, SCRIPT_LEN_MODE, CHARS_PER_SEC,
)
from src.script_gen.correct_terms import normalize_caption, to_speech
from src.utils.buildnotes import note
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
    layout: str = ""      # 지정 대본에서 타이틀카드 구도를 고정할 때만 사용


PROMPT = """당신은 한국 부동산 유튜브 쇼츠 대본 작가입니다.
아래 뉴스 기사를 바탕으로 {sec_min}~{sec_max}초 분량 쇼츠 대본을 만드세요.

[기사]
제목: {title}
출처: {source}
본문: {summary}

[반드시 지킬 규칙]
1. 구조: 훅(첫 문장 3초 안에 궁금증/충격) → 수치 근거 → 해석 → 실전 시사점/결론.
2. 구체적 지역·단지·물건명과 '숫자'를 넣어 신뢰와 후킹을 동시에.
3. 톤: 뉴스 앵커처럼 단정적. 단, 특정 정당·정치인 '실명' 저격/인신공격은 금지
   (파당적 어그로 X). 비판의 대상은 '사람'이 아니라 '정책과 그 결과'다.
4. 문장은 짧고 끊어읽기 좋게. 과장·허위·확정적 투자권유 금지.
5. 사실이 불확실하면 단정하지 말 것.
6. [독창성] 기사 문장을 그대로 옮기지 말 것. 현직 공인중개사 '임장왕'의 시각으로
   기사가 말하지 않는 맥락·배경(수요/공급, 금리, 정책 의도)을 한 겹 더 해석해 덧붙일 것.
7. [실전 시사점] 마지막은 시청자가 확인해야 할 사실·제도로 끝낼 것. 사거나 팔
   시점을 권하는 것이 아니라, 무엇을 알아야 하는지를 말한다(규칙 19 참조).
   (좋은 예: "전세 낀 매물은 실거주 의무 적용 여부가 갈립니다",
    "특별공급은 기준일 이전 거주 요건이 따로 있습니다")
   (나쁜 예: "지금은 급매 대신 대기", "지금 사세요" — 매수·매도 타이밍 조언)
8. [정책 부작용 지적 — 핵심] 이슈에 정부 부동산 정책(대출·세금·규제·공급·임대차법·
   토허제 등)이 얽혀 있으면, 그 정책이 '의도와 달리' 시장에 낳는 부작용·역효과를
   인과관계로 분명히 짚을 것. 예: 대출 규제→오히려 현금부자만 유리·실수요 배제,
   임대차법→전세 매물 급감·전세가 급등, 공급 억제→가격 상승, 세금 강화→매물 잠김.
   '정치인 비난'이 아니라 '정책 메커니즘 → 시장 왜곡·서민 피해'로 날카롭게 분석할 것.
   단, 분석은 날카롭게 하되 문장 형식은 규칙 16을 따른다. 메커니즘을 구체적으로
   설명하고, 그것이 원인이라는 '판단'은 귀속해서 쓴다.
   (좋은 예: "임대차법 이후 전세 매물이 줄었고, 이를 원인으로 보는 지적이 나옵니다")
   (나쁜 예: "임대차법이 전세난을 부른 역설적 결과입니다")
   2026-09-16 실측: 이 규칙과 규칙 16이 서로 부딪혀 대본이 "…고령층을 집 밖으로
   내모는 역설적 결과입니다"로 단정했다. 둘 다 지키는 길은 위 형식뿐이다.
9. [기조 — 절대 준수] 이 채널의 일관된 관점은 '정부 부동산 정책이 시장을 왜곡하고
   실수요자·서민에게 피해를 준다'이다. 기사가 '집값 안정/멈칫/보합'이라고 해도 그것을
   '정책 성공'으로 그리지 말 것. 훅·헤드라인·제목은 반드시 정책의 '문제·부작용'을 겨냥한다.
   (집값이 멈칫이면 → '표면적·일시적이며 거래절벽·전세폭등 같은 더 큰 대가가 따른다'로 재구성)
10. [형식 — 엄수] script는 성우가 입으로 '읽는 문장'만 담는다. 지문·괄호 설명·타임스탬프
   ((4초) 등)·[대괄호 지시]·자막표기·이모지·마크다운(*#>)은 절대 넣지 말 것.
11. [사실·귀속 정확성 — 필수] 정책의 도입 시점·주체를 사실대로 쓸 것. 과거 정부가 시작한
   정책(예: 문재인 정부가 2019년 도입한 민간택지 분양가상한제)을 현 정부가 한 것처럼
   쓰지 말 것. 현 정부의 책임은 '이어받아 유지·강화·악화시켰다'로 정확히 구분해 서술한다.
   확인 안 된 주체·연도는 아예 특정하지 말 것(허위 귀속은 채널 신뢰를 무너뜨림).

12. [제목 형식 — 중요] youtube_title은 기사 요약이 아니라 '누가 무슨 말을
   했는지'가 드러나야 한다. 다음 중 하나를 쓴다.
   (a) "발언 인용" + 주체:  "전세 감소는 정상화 과정" 이 대통령 발언의 속뜻
   (b) 주체 + "발언 인용":  국토부가 확인해 줬다 "월세 비중 68% 역대 최고"
   기사에 사람의 발언이 없으면 발표 주체(국토교통부·한국부동산원·국세청 등)를
   주체로 쓰고, 그 기관이 낸 수치를 따옴표 안에 넣는다.
   따옴표 안 문장은 기사에 실제로 있는 표현이어야 한다. 지어내지 말 것.
   주체를 특정할 수 없으면 따옴표 없이 쓰되, 이 경우에도 숫자를 앞에 둔다.

13. [발언 주체 명시] script 안에서도 수치를 처음 말할 때 출처 기관을 붙인다.
   "국토교통부 7월 주택통계입니다" 처럼. 출처 없는 숫자는 신뢰를 못 얻는다.

14. [인용 변조 금지 — 필수] 따옴표 안에는 기사에 있는 낱말을 그대로 옮긴다.
   더 세게 들리도록 단어를 바꾸지 말 것. "상승"을 "폭등"으로, "우려"를
   "재앙"으로 바꾸는 식은 인용 변조이고, 시청자가 원문과 대조하면 바로
   걸린다. 세게 쓰고 싶으면 따옴표 밖에서 쓴다.
   (2026-09-11분 실제 사고: 원문 '86주 연속 상승'을 제목에서 '86주 연속
   폭등'으로 바꿔 따옴표 안에 넣었다.)

15. [주장과 사실의 구분 — 필수] 정치인·이해관계자·업계가 한 말은 사실이
   아니라 주장이다. script에서 "OOO 의원이 ~라고 주장했습니다"처럼 말한
   사람을 밝힌다. 언론이 그 주장을 받아쓴 기사를 근거로 "언론 보도에
   따르면 ~이다"라고 쓰면, 주장을 검증된 사실로 둔갑시키는 것이 된다.
   통계 수치는 발표 기관(한국부동산원·국토교통부 등)을 밝히고, 집계 방식에
   다툼이 있으면 그대로 적는다.

16. [인과 단정 금지 — 단, 없는 말을 만들지 말 것] 어떤 정책이 어떤 결과의
   "근본 원인"이라는 단정은 쓰지 않는다. 논쟁 중인 해석이다. 그렇다고
   하지 않은 말을 지어 붙여도 안 된다. "~라는 지적이 나옵니다",
   "전문가들은 ~라고 봅니다", "~를 원인으로 보는 시각이 있습니다"는
   기사에 그 지적·발언이 실제로 있을 때만 쓴다. 기사에 없으면 진행자
   본인의 읽기로 쓴다 — "저는 ~로 봅니다", "~로 읽힙니다",
   "~일 가능성이 있습니다".
   (2026-09-22 실패: 기사에 없는 "과도한 대출 규제와 세금 중과가 매물
   잠김을 초래했다는 지적이 나옵니다"를 붙였다. 수치는 다섯 개 다
   맞았는데 그 문장만 출처가 없었다. 없는 출처를 붙이는 것이 해석을
   내 것으로 말하는 것보다 나쁘다.)

17. [구체 수치 최소 3개 — 필수] script 안에 기사에 실제로 있는 수치를 단위와
   함께 최소 3개 넣는다. "몇 주째", "크게 줄어든", "급감", "역대급" 같은
   두루뭉술한 표현으로 숫자를 대신하지 말 것. 기사에 숫자가 있으면 그대로
   쓴다(17.16%, 4,278가구, 160만원, 86주). 기사에 없는 숫자는 절대 만들어
   내지 말 것 — 기사에 숫자가 부족하면 있는 것만 쓰고 개수를 못 채워도 된다.
   이 채널은 숫자로 보는 채널이고,
   화면 중앙에 뜨는 숫자 카드도 이 수치에서 뽑는다. 숫자가 없으면 그 자리가
   빈 화면이 된다.
   수치의 '성격'을 바꾸지 말 것. 숫자가 맞아도 뜻이 달라지면 틀린 것이다.
   2026-09-16 실측: 기사의 "월 생활비 최저 190만원(식사·세탁·컨시어지 포함,
   61㎡ 1인 기준)"이 대본에서 "월세 190만원"이 됐다. 금액은 맞지만 시청자는
   임대료로 이해한다. 단위·기준·'최저/평균' 표시를 기사에 적힌 그대로 옮긴다.
   반올림해서 문턱을 넘겼다고 쓰지 말 것. 2026-09-16 실측: 기사의 "평균 월세
   95만6000원으로 100만원에 육박"이 대본 훅에서 "월 100만 원 시대가
   현실입니다"가 됐다. 아직 넘지 않은 선을 넘었다고 쓴 것이다. 기사가
   '육박·근접·앞두고'라고 쓴 것은 그대로 '육박'이라고 쓴다.
   수치를 앞쪽 '근거' 문단에만 몰아 넣지 말 것. 최소 1개는 뒤쪽 해석·결론
   문단에 둔다. 규칙 1의 구조를 그대로 따르면 숫자가 대본 중간에만 모여
   후반 20초 넘게 화면 중앙이 빈다(2026-09-14 실측: 수치 5개가 전부
   11.6~31.9초 구간). 해석에도 근거를 붙이면 된다 — "인허가가 32% 줄어든
   것이 지금의 공급 부족으로 이어졌다는 지적입니다" 처럼.

18. [범위를 한 값으로 줄이지 말 것] 기사가 "10억원 후반~11억원 초반"
   처럼 범위로 쓴 것을 "10억원대"로 줄이면 시작값이 낮아져 상승폭이
   실제보다 커 보인다. 2026-09-20 실측에서 이 압축 하나로 3~3.5억
   상승이 4억 상승처럼 읽혔다. 범위는 범위로 쓰거나, 한 값만 쓸
   거면 기사에 그 값이 그대로 있어야 한다. "~대", "약", "가까이"로
   뭉개는 것도 같은 문제다.

19. [투자 조언 금지 — 필수] 사거나 팔 시점을 권하지 않는다. "급매물을
   노리세요", "지금 사라/팔아라", "영끌하지 마세요" 모두 금지. 영상 설명에
   투자 권유가 아니라고 적어 두고 본문에서 매수 타이밍을 조언하면 그 자체로
   모순이다. 제도와 숫자를 설명하는 데서 끝낸다.

20. [수치의 기준 시점 — 필수] 한 기사 안에서도 수치마다 기준 시점이 다르다.
   8월 통계와 7월 통계를 "같은 기간"으로 묶지 말 것. 시점이 다르면 수치마다
   "8월", "7월", "1~7월"을 붙여 말한다.
   (2026-09-22 실패: 8월 수도권 전세 0.78%·월세 0.68% 뒤에 7월 기준
   월세 거래 비중 67.6%를 "같은 기간"으로 이어 붙였다. 두 수치 다
   맞았는데 묶음이 틀렸다.)

[출력: 아래 JSON만, 다른 텍스트 없이. 주석(//)을 달지 말 것.
 값 안에 큰따옴표를 쓸 때는 반드시 \\" 로 이스케이프할 것]
{{
  "headline": ["타이틀 1줄(12자 이내, 정책 문제 겨냥)", "타이틀 2줄", "(선택)3줄"],
  "hook_word": "헤드라인에서 노랗게 강조할 핵심 단어 1개",
  "highlight_sentence": "기사에서 형광펜 칠할 핵심 한 문장(20자 내외)",
  "script": "말하는 문장만. {chars_min}~{chars_max}자. 지문·괄호·타임스탬프 없이.",
  "youtube_title": "따옴표 인용 + 발언 주체 형식의 제목(38자 이내, 해시태그·이모지 제외)",
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
    # 주 모델 → 백업 모델 순으로, 각 모델마다 키 로테이션
    models = list(dict.fromkeys([GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]))
    last = None
    for model_name in models:
        for _ in range(min(len(GEMINI_API_KEYS), 3)):
            key = next(_key_cycle)
            try:
                genai.configure(api_key=key)
                resp = genai.GenerativeModel(model_name).generate_content(prompt)
                if resp.text:
                    log.info(f"  Gemini 생성({model_name})")
                    return resp.text
            except Exception as e:  # 쿼터/모델명/키 → 다음 키·모델
                last = e
                log.info(f"  Gemini 실패({model_name}), 로테이션: {str(e)[:80]}")
    log.info(f"  Gemini 전체 실패: {str(last)[:120]}")
    return None


# 모델이 내는 JSON의 키. 파싱이 깨졌을 때 필드 단위로 건져 내는 데 쓴다.
_JSON_KEYS = ("headline", "hook_word", "highlight_sentence", "script",
              "youtube_title", "hashtags")


def _loose_fields(txt: str) -> dict:
    """깨진 JSON에서 아는 키만 골라 값을 건져 낸다.

    프롬프트가 youtube_title에 '따옴표 인용'을 요구하기 때문에 모델이
    문자열 안에 이스케이프 없는 "를 넣는 일이 잦다. 출력 예시에 //
    주석까지 들어 있어 그걸 따라 쓰기도 한다. 둘 다 json.loads를
    통째로 실패시킨다.

    따옴표 한 개 때문에 대본을 버리고 템플릿 폴백으로 내려가는 손해가
    너무 크다. 스키마를 아는 쪽이 우리이므로 키 위치로 잘라 읽는다.
    """
    spans = []
    for key in _JSON_KEYS:
        m = re.search(r'"%s"\s*:' % re.escape(key), txt)
        if m:
            spans.append((m.start(), m.end(), key))
    spans.sort()

    out: dict = {}
    for i, (_start, val_at, key) in enumerate(spans):
        end = spans[i + 1][0] if i + 1 < len(spans) else len(txt)
        chunk = txt[val_at:end].strip()
        if chunk.startswith("["):
            arr = chunk[:chunk.rfind("]") + 1] if "]" in chunk else chunk
            vals = [v.strip() for v in re.findall(r'"([^"]*)"', arr)]
            out[key] = [v for v in vals if v]
        else:
            # 값의 시작 "와 끝 " 사이. 뒤에 붙은 쉼표·주석·중괄호는 버린다.
            a, b = chunk.find('"'), chunk.rfind('"')
            if a != -1 and b > a:
                out[key] = (chunk[a + 1:b]
                            .replace('\\"', '"').replace("\\n", " ").strip())
    return out


def _parse_json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        log.info(f"  대본 JSON: 중괄호를 못 찾음 (raw {len(raw)}자): {raw[:120]!r}")
        return None
    blob = m.group(0)
    try:
        return json.loads(blob)
    except Exception as e:
        first = str(e)[:100]

    # 1차 수선: // 줄 주석 제거 + 후행 쉼표 제거
    fixed = re.sub(r'(?m)//[^"\n]*$', "", blob)
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    try:
        data = json.loads(fixed)
        log.info("  대본 JSON: 주석·후행쉼표 수선 후 파싱 성공")
        return data
    except Exception:
        pass

    # 2차: 키 위치로 잘라 읽기(이스케이프 안 된 따옴표 대응)
    loose = _loose_fields(blob)
    if loose.get("script"):
        log.info(f"  대본 JSON: 느슨한 파싱으로 복구 "
                 f"({', '.join(sorted(loose))}) — 원인: {first}")
        return loose
    log.info(f"  대본 JSON 파싱 실패: {first} / raw {len(raw)}자: {blob[:200]!r}")
    return None


def _fallback_plan(art) -> ShortPlan:
    """Gemini 실패 시 기사 메타로 최소 대본 구성(제목 중복 제거, 본문 활용)."""
    title = (getattr(art, "title", "") or "부동산 시장 이슈").strip()
    summary = (getattr(art, "summary", "") or "").strip()
    words = title.split()
    hl = words[0] if words else "부동산"
    head = _split_headline(title)

    # 본문에서 제목과 겹치는 앞부분 제거
    body = summary
    if body[:20] and body[:20] in title:
        body = body[len(title):].strip(" .,·-")

    # 기사 머리의 부제·사진설명·발신지·기자 바이라인을 걷어낸다.
    # 2026-09-19 실측(1GWtMyDh3Ic): 이걸 안 지워서 나레이션이
    # "변해정 기자 = 서울 민간아파트 3.3㎡당..."으로 시작했다.
    body = re.sub(r"\[[^\]]{0,40}\]", " ", body)          # [서울=뉴시스]
    # 한국 기사는 "[서울=뉴시스] 변해정 기자 = " 뒤부터가 본문이다. 그 앞은
    # 부제와 사진 설명이라 나레이션에 들어가면 안 된다. 바이라인만 지우면
    # 앞의 부제·사진설명이 그대로 남아 첫 문장에 붙는다 — 실측에서
    # "HUG 집계 최근 1년 평균…서울 강북구의 한 아파트 단지 모습."이 실렸다.
    byline = re.search(r"\S{1,10}\s*기자\s*=\s*", body)
    if byline and byline.end() < len(body) * 0.5:
        body = body[byline.end():]
    else:
        body = re.sub(r"\S{1,10}\s*기자\s*=\s*", "", body)
    # 바이라인이 없는 매체는 사진 설명이 맨 앞에 남는다("…단지 모습.")
    body = re.sub(r"^.{0,120}?(?:모습|사진)\s*[.。]\s*", "", body)
    body = re.sub(r"\S+@\S+\.\S+", " ", body)              # 메일 주소
    body = re.sub(r"\s+", " ", body).strip()

    # 완결된 문장만 쓴다. 부제·사진설명은 '~다.'로 끝나지 않아 여기서 걸러진다.
    #
    # 문장 끝은 '다/요 + 마침표'로만 본다. 마침표 하나로 자르면 소수점에서
    # 끊긴다 — "34.2%"가 "34." + "2%..."로 갈려 나레이션에 "2%(484만8000원)
    # 각각 증가한 것이다"가 실렸다. 뒤에 숫자가 오는 마침표는 문장 끝이 아니다.
    parts = re.split(r"(?<=[다요])[.!?]+(?!\d)", body)
    sents = []
    for part in parts:
        part = part.strip()
        if len(part) >= 20:
            sents.append(part + ".")

    # 길이는 문장 단위로 맞춘다. 글자 수로 자르면 수치 한가운데가 잘린다 —
    # 실측에서 "1.26%(23만70.." 이 그대로 나레이션과 자막에 실렸다.
    budget = max(120, SCRIPT_CHARS_MAX - 90)
    picked: list[str] = []
    for sent in sents:
        if sum(len(x) + 1 for x in picked) + len(sent) > budget:
            break
        picked.append(sent)
    body_text = " ".join(picked)

    hook = f"{title.rstrip('.')}, 지금 무슨 일이 벌어지고 있을까요?"
    # 맺음말에 매수·매도 타이밍을 암시하지 않는다(규칙 19). 예전 문구
    # "놓치면 내 집 마련 타이밍이 달라질 수 있습니다"는 설명란의
    # '투자 권유가 아닙니다' 고지와 정면으로 어긋났다.
    if body_text:
        script = f"{hook} {body_text} 관련 소식은 계속 정리해 전해 드리겠습니다."
    else:
        script = (
            f"{hook} 정부 정책과 대출·전세 시장이 맞물리며 실수요자 부담이 "
            "커지는 흐름입니다. 관련 소식은 계속 정리해 전해 드리겠습니다."
        )
    script = _trim_incomplete_tail(_cap_length(_clean_script(normalize_caption(script))))
    return ShortPlan(
        headline=head,
        hook_word=hl,
        highlight_sentence=title[:24],
        caption_script=script,
        speech_script=to_speech(script),
        youtube_title=title[:38],
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


# 지문/타임스탬프/자막표기 등 '말하지 않는 것' 제거용
_STAGE_RE = re.compile(
    r"\[[^\]]*\]"                                      # [뉴스기사 띄우며] 류 대괄호 전부
    r"|\([^)]*(?:초|분|띄우|자막|화면|컷|인서트|자료|효과음|BGM|음악|나레이션|성우|장면|영상)[^)]*\)"
)


def _clean_script(text: str) -> str:
    """대본에서 지문·타임스탬프·마크다운·이모지를 제거하고 말하는 문장만 남긴다."""
    text = _STAGE_RE.sub(" ", text)
    text = re.sub(r"[\*#>`\_]+", "", text)                 # 마크다운 잔여
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)  # 이모지
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


# 문장이 끝났다고 볼 수 있는 자리. 종결어미를 화이트리스트로 둔다.
# '다'나 '요'만 보면 "계약보다"의 '다'를 문장 끝으로 오인한다(실측).
_SENT_END_RE = re.compile(
    r"(?:[.!?]|"
    r"(?:습니다|니다|었다|았다|였다|는다|한다|된다|겠다|이다|아니다|더라|"
    r"네요|세요|어요|아요|에요|예요|해요|이죠|죠|군요|까요|나요)"
    r"(?=[.!?\s]|$))")


def _trim_incomplete_tail(text: str, keep_ratio: float = 0.6) -> str:
    """끝이 잘린 문장을 버린다.

    모델이 "…눈높이를 현실에 맞추세요. 급한 계약보다" 처럼 문장을 미완으로
    내는 경우가 있다(2026-09-14 실측). 그대로 두면 나레이션도 자막도 중간에
    끊긴 채로 나가 시청자에게 바로 보인다.

    잘라내면 keep_ratio 미만만 남는 경우에는 손대지 않는다 — 어색한 끝맺음이
    토막글보다 낫다.
    """
    t = (text or "").strip()
    if not t or t[-1] in ".!?":
        return t
    ends = list(_SENT_END_RE.finditer(t))
    if not ends:
        return t
    cut = t[: ends[-1].end()].strip()
    # 종결어미 뒤에 문장부호가 없으면 붙여 준다("…삽니다" → "…삽니다.")
    if cut and cut[-1] not in ".!?":
        cut += "."
    if len(cut) < len(t) * keep_ratio:
        log.info(f"  대본 끝이 잘린 듯하나 남는 분량이 적어 그대로 둔다({len(cut)}/{len(t)}자)")
        return t
    if cut != t:
        log.info(f"  대본 미완성 끝 문장 제거: …{t[len(cut):][:20]!r}")
    return cut


def _cap_length(text: str, max_chars: int | None = None) -> str:
    """너무 긴 대본은 문장 경계에서 안전하게 자른다(쇼츠 길이 폭주 방지).

    실측 환산은 약 6.4자/초다. 목표 구간 310~350자가 48~55초, 상한 380자가
    약 59초다.

    이 구간을 고른 근거는 지속률이 잡힌 영상 20개다(2026-09-13 기준).
      50~60초  9개 → 지속률 중앙 72.2%, 조회수 중앙 1,741
      60초 초과 11개 → 지속률 중앙 61.6%, 조회수 중앙 1,201
      상관: 길이↔지속률 -0.52, 지속률↔조회수 +0.68, 길이↔조회수 -0.45
    지속률이 조회수를 끌어올리는 가장 강한 변수이고, 길이는 그 지속률을
    깎는다. 그래서 60초를 넘기지 않는 선에서 최대한 붙인다.

    이전 목표는 240~280자(38~44초)였는데, 이는 리포트의 '50초 초과가
    하루당 조회수 2.3배'라는 줄을 뒤집어 잡은 값이었다. 그 줄은 150개
    전체를 섞어 2025년 영상과 2026년 영상을 비교하고 있었던 잘못된
    비교다(scripts/analyze_performance.py에서 함께 고쳤다). 다만 관측된
    길이가 45~92초뿐이라 45초 미만이 더 나은지는 데이터가 없다.
    """
    if max_chars is None:
        max_chars = SCRIPT_CHARS_CAP
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    m = list(re.finditer(r"[.!?다요]\s", cut))
    if m:
        return cut[: m[-1].end()].strip()
    return cut.rstrip()


# 대본에 들어 있어야 하는 최소 수치 개수.
MIN_STATS = 3
_STAT_COUNT_RE = re.compile(
    r"\d[\d,\.]*\s?(?:%|퍼센트|억|만원|만|천|원|배|채|가구|세대|호|명|건|위|조|평|㎡|개월|주|년)")


def _count_stats(script: str) -> int:
    """단위가 붙은 수치의 개수. 연도는 세지 않는다."""
    hits = [m.group(0) for m in _STAT_COUNT_RE.finditer(script or "")]
    return sum(1 for h in hits if not re.fullmatch(r"(1[89]\d{2}|20\d{2})\s?년", h))


# 본문이 이만큼은 돼야 '수치를 뽑아 쓸 근거가 있다'고 본다(기사 선정 기준과 동일).
MIN_BODY_FOR_STATS = 80


# 따옴표 안은 남의 말이다. 지어내면 규칙 위반이 아니라 허위 인용이다.
#
# 2026-09-22 실측: 모델이 남혁우 우리은행 부동산연구원의 발언을 "매입 약정
# 가격이 물량 확보를 좌우할 것"이라며 우려했다고 썼다. 기사에서 그는 매입
# 약정이 "신축 공급까지 시차를 줄이려는 성격이 있다"고 긍정 평가했다.
# 실명 인물의 입장을 반대로 뒤집은 것이고, 수치는 다 맞았으니 숫자 검증
# 으로는 안 걸린다. 규칙 14로 금지해 뒀지만 프롬프트만으로는 막히지 않았다.
#
# 그래서 규칙이 아니라 검사로 막는다. 따옴표 안 문장이 기사 본문에 그대로
# 있는지 문자 단위로 확인한다.
_QUOTE_SPAN_RE = re.compile(
    r"[\"“‟]([^\"“”‟]{1,120})[\"”‟]"
    r"|'([^']{1,120})'"
    r"|‘([^’]{1,120})’"
)

# 비교는 글자만 남겨서 한다. 기사와 대본은 띄어쓰기·가운뎃점·말줄임표가
# 제각각이라 원문 그대로 옮겨도 문자열이 안 맞는다.
_QUOTE_NORM_RE = re.compile(r"[^0-9A-Za-z가-힣%]")

# 짧은 따옴표는 인용이 아니라 강조다('실입주 물량', '갭투자'). 기사에
# 그 낱말이 그대로 없어도 문제가 아니라서 검사하지 않는다. 사람의 말을
# 옮긴 인용은 이보다 길다.
QUOTE_CHECK_MIN = 12


def _norm_quote(s: str) -> str:
    return _QUOTE_NORM_RE.sub("", s or "")


def _fake_quotes(text: str, source_text: str) -> list[str]:
    """따옴표 안 내용 중 기사에 없는 것들을 돌려준다."""
    hay = _norm_quote(source_text)
    bad: list[str] = []
    for m in _QUOTE_SPAN_RE.finditer(text or ""):
        span = next((g for g in m.groups() if g), "")
        n = _norm_quote(span)
        if len(n) < QUOTE_CHECK_MIN or n in hay:
            continue
        if span not in bad:
            bad.append(span)
    return bad


# 퍼센트는 분모가 있어야 뜻이 생긴다. 모델은 개별 수치는 잘 옮기고
# 수치 사이의 '관계'를 자주 뒤집는다.
#
# 2026-09-22 실측: 기사는 "향후 5년간 공급되는 임대 성격의 공적주택은
# 94만5000가구다 … 전체 공적주택 118만8000가구의 79.5%에 달한다"고 썼다.
# 대본은 "공적주택 94만 5천 가구 … 이 중 무려 79.5%가 임대주택"이라고
# 했다. 부분과 전체를 맞바꾼 것이다. 두 숫자 다 기사에 있으니 수치 검증
# 으로는 안 걸리고, 따옴표도 없으니 인용 검사로도 안 걸린다.
#
# 그래서 퍼센트 옆에 붙은 규모를 본다. 대본이 어떤 퍼센트 옆에 규모를
# 적었다면, 기사에서 같은 퍼센트 옆에도 그 규모가 있어야 한다.
_PCT_RE = re.compile(r"\d+(?:\.\d+)?\s?%")
_MAG_RE = re.compile(r"\d[\d,\.]*\s?(?:억|만|천)\s?\d*(?:천)?")
_KOR_UNIT = {"억": 100_000_000, "만": 10_000, "천": 1_000}

# 이보다 작은 값은 비교하지 않는다. "10%", "2년" 같은 것이 섞여 들어와
# 우연히 안 맞는 일이 생긴다.
MAG_COMPARE_MIN = 10_000


def _kor_num(tok: str) -> float | None:
    """'94만5000', '94만 5천'을 같은 수로 읽는다."""
    t = re.sub(r"[,\s]", "", tok)
    total, cur, seen = 0.0, "", False
    for c in t:
        if c.isdigit() or c == ".":
            cur += c
        elif c in _KOR_UNIT:
            total += (float(cur) if cur else 1.0) * _KOR_UNIT[c]
            cur, seen = "", True
        else:
            break
    if cur:
        total += float(cur)          # '94만5000'의 뒤 5000
        seen = True
    return total if seen else None


def _mags_near(text: str, start: int, end: int, pad: int) -> set[float]:
    win = text[max(0, start - pad):end + pad]
    out = set()
    for m in _MAG_RE.finditer(win):
        v = _kor_num(m.group(0))
        if v and v >= MAG_COMPARE_MIN:
            out.add(v)
    return out


def _pct_problems(script: str, source_text: str) -> list[tuple[str, str]]:
    """(대본에 있는 퍼센트 표기, 문제 사유). 표기는 문장 삭제용 바늘로도 쓴다."""
    src = re.sub(r"\s+", " ", source_text or "")
    out: list[tuple[str, str]] = []
    for m in _PCT_RE.finditer(script or ""):
        raw = m.group(0)
        pct = raw.replace(" ", "")
        hits = list(re.finditer(re.escape(pct[:-1]) + r"\s?%", src))
        if not hits:
            out.append((raw, f"{pct}는 기사에 없는 수치다"))
            continue
        mine = _mags_near(script, m.start(), m.end(), 60)
        if not mine:
            continue                  # 규모를 안 붙였으면 관계 주장도 없다
        theirs: set[float] = set()
        for h in hits:
            theirs |= _mags_near(src, h.start(), h.end(), 80)
        if theirs and not (mine & theirs):
            out.append((raw, f"{pct} 옆에 붙인 규모가 기사의 분모와 다르다"))
    return out

def _drop_sentences_with(script: str, quotes: list[str]) -> str:
    """위조 인용이 들어간 문장만 버린다. 파이프라인은 멈추지 않는다."""
    if not quotes:
        return script
    parts = [p.strip() for p in re.split(r"(?<=[다요])[.!?]+(?!\d)", script or "")]
    keep = [p for p in parts if p and not any(q in p for q in quotes)]
    return (". ".join(keep) + ".") if keep else ""


def generate(art) -> ShortPlan:
    body = (getattr(art, "summary", "") or "").strip()
    has_body = len(body) >= MIN_BODY_FOR_STATS
    prompt = PROMPT.format(
        title=getattr(art, "title", ""),
        source=getattr(art, "source", ""),
        # 본문 추출을 고친 뒤 실제 본문이 1,000~2,000자로 들어온다.
        # 1,200자에서 자르면 기사 후반의 수치를 통째로 못 본다.
        summary=(body or getattr(art, "title", ""))[:2200],
        chars_min=SCRIPT_CHARS_MIN,
        chars_max=SCRIPT_CHARS_MAX,
        sec_min=round(SCRIPT_CHARS_MIN / CHARS_PER_SEC),
        sec_max=round(SCRIPT_CHARS_MAX / CHARS_PER_SEC),
    )
    note(f"대본 길이 모드: {SCRIPT_LEN_MODE} "
         f"({SCRIPT_CHARS_MIN}~{SCRIPT_CHARS_MAX}자, 상한 {SCRIPT_CHARS_CAP}자)")
    # 본문 확보에 실패하는 날이 있다(구글뉴스 리다이렉트 해소 실패). 그때
    # 수치 3개를 요구하면 모델이 기사에 없는 숫자를 지어낸다 — 2026-09-15
    # 실측에서 본문 0자인데 "25만 가구대", "10주 연속", "150만원"이 나왔다.
    # 뉴스 채널에서 이건 조회수보다 훨씬 비싼 실수다. 근거가 없으면 요구하지
    # 않는다.
    if not has_body:
        prompt += ("\n\n[본문 없음 — 최우선] 이 기사는 본문을 확보하지 못했다. "
                   "제목에 있는 사실만 쓰고, 기사에 없는 수치·기관명·날짜·인용을 "
                   "절대 지어내지 말 것. 규칙 17의 '수치 최소 3개'는 이 경우 "
                   "적용하지 않는다. 숫자가 없으면 없는 대로, 사실만으로 써라.")
        note(f"대본: 기사 본문 {len(body)}자 → 수치 강제 해제(날조 방지)")
    raw = _gemini(prompt)
    data = _parse_json(raw) if raw else None
    if not data or not data.get("script"):
        log.info("대본: 폴백 사용")
        return _fallback_plan(art)

    # 수치가 없는 대본은 이 채널에서 쓸모가 없다. 상위 영상은 전부 제목에
    # 숫자가 있고, 화면 중앙 숫자 카드도 대본 수치에서 뽑는다. 실측(2026-09-14)
    # 에서 21개 구절에 수치가 0개인 대본이 나와 콜아웃이 한 개도 안 떴다.
    # 한 번만 더 요청하고, 그래도 없으면 그대로 간다(파이프라인은 멈추지 않는다).
    if has_body and _count_stats(str(data.get("script", ""))) < MIN_STATS:
        log.info(f"대본에 수치가 {_count_stats(str(data.get('script','')))}개뿐 → 재요청")
        raw2 = _gemini(prompt + "\n\n[재작성] 앞선 초안에 구체적인 수치가 없었다. "
                                "기사에 있는 숫자를 단위와 함께 최소 3개 넣어 다시 써라.")
        data2 = _parse_json(raw2) if raw2 else None
        if data2 and data2.get("script"):
            if _count_stats(str(data2["script"])) > _count_stats(str(data["script"])):
                data = data2
        log.info(f"  재요청 결과 수치 {_count_stats(str(data.get('script','')))}개")

    # 따옴표 검사. 한 번 다시 요청하고, 그래도 지어내면 그 문장을 버린다.
    # 제목은 기사 헤드라인을 인용하는 형식이라 본문에 제목까지 붙여서 본다.
    #
    # 제목은 재요청 사유로 쓰지 않는다. 제목 인용은 38자에 맞춰 압축되므로
    # 원문과 한 낱말만 달라도 걸린다. 그걸로 대본 전체를 다시 뽑으면 손해다.
    # 제목은 걸리면 기사 헤드라인으로 되돌린다.
    if has_body:
        hay = f"{getattr(art, 'title', '')} {body}"
        def _defects(text: str) -> tuple[list[str], list[str]]:
            """(문장 삭제에 쓸 바늘, 사람이 읽을 사유)."""
            q = _fake_quotes(text, hay)
            p = _pct_problems(text, hay)
            return q + [n for n, _ in p], [f'없는 인용: "{x}"' for x in q] + [r for _, r in p]

        bad, why = _defects(str(data.get("script", "")))
        if bad:
            log.info(f"대본: 사실 대조 실패 {len(bad)}건 → 재요청 {why[:2]}")
            raw3 = _gemini(
                prompt + "\n\n[재작성 — 기사와 안 맞는 부분] 앞선 초안에서 다음이 기사와 "
                "다르다: " + " / ".join(why[:3]) + ".\n"
                "따옴표 안에는 기사 본문에 그대로 있는 말만 옮긴다. 기사에 없으면 따옴표를 "
                "쓰지 말고 네 해석으로 풀어 써라. 실명 인물의 발언은 기사에 있는 취지를 "
                "바꾸지 말 것 — 긍정 평가를 우려로 뒤집는 것은 허위 인용이다.\n"
                "퍼센트는 기사가 쓴 분모를 그대로 따른다. 기사가 'A는 B의 N%'라고 썼으면 "
                "'A 중 N%가 B'로 뒤집지 말 것 — 부분과 전체를 맞바꾸는 것이다.")
            data3 = _parse_json(raw3) if raw3 else None
            if data3 and data3.get("script") and not _defects(str(data3["script"]))[0]:
                data = data3
                note(f"대본: 사실 대조 실패 {len(bad)}건 → 재요청으로 교체")
            else:
                left, lwhy = _defects(str(data.get("script", "")))
                data["script"] = _drop_sentences_with(str(data.get("script", "")), left)
                note(f"대본: 사실 대조 실패 {len(left)}건 → 해당 문장 삭제 ({lwhy[0][:40]})")
                log.info(f"  재요청도 실패 → 문장 {len(left)}건 삭제")
        if not str(data.get("script", "")).strip():
            log.info("대본: 인용 삭제 후 남은 문장이 없다 → 폴백")
            return _fallback_plan(art)
        tbad = _fake_quotes(str(data.get("youtube_title", "")), hay)
        if tbad:
            data["youtube_title"] = getattr(art, "title", "")[:38]
            note(f"제목: 기사에 없는 인용 → 기사 헤드라인으로 교체 ({tbad[0][:30]})")

    script = _trim_incomplete_tail(
        _cap_length(_clean_script(normalize_caption(str(data["script"]).strip()))))
    headline = [_clean_script(normalize_caption(h)) for h in (data.get("headline") or [])][:3]
    headline = [h for h in headline if h]
    if not headline:
        headline = _split_headline(getattr(art, "title", "부동산 뉴스"))
    plan = ShortPlan(
        headline=headline,
        hook_word=_clean_script(normalize_caption(str(data.get("hook_word", headline[0].split()[0] if headline else "")))),
        highlight_sentence=_clean_script(normalize_caption(str(data.get("highlight_sentence", ""))[:30])),
        caption_script=script,
        speech_script=to_speech(script),
        youtube_title=_clean_script(normalize_caption(str(data.get("youtube_title", getattr(art, "title", "")))[:38])),
        hashtags=(data.get("hashtags") or DEFAULT_HASHTAGS)[:8],
    )
    log.info(f"대본 생성 완료: {plan.youtube_title}")
    return plan
