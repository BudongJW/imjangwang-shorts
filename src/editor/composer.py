"""영상 합성 — 개선안 ①③④ 반영.

파이프라인:
  1) 비주얼 세그먼트 구성: [타이틀카드(≤4s)] → 이미지컷 → [기사캡처] → 이미지컷…
     · 타이틀카드는 도입 3~4초만(개선안 ①)
     · 이미지 1컷 ≤ 3초 + 켄번즈 모션(개선안 ③)
  2) 각 세그먼트를 ffmpeg zoompan 클립으로 렌더 후 concat
  3) 최종 패스에서 '교정된 자막'을 번인(개선안 ④) + TTS 오디오 mux
"""

from __future__ import annotations

import math
import random
import re
import shutil
import subprocess
import tempfile
from itertools import cycle
from pathlib import Path

from config.settings import (
    SHORTS_WIDTH, SHORTS_HEIGHT, SHORTS_FPS, FINAL_DIR, VIDEO_DIR, FONT_DIR, BGM_DIR,
    TITLE_CARD_MAX_SEC, IMAGE_MAX_SEC, STAT_MAX_SEC, KENBURNS, BGM_VOLUME,
)
from src.editor.fonts import font_bold
from src.utils.buildnotes import note
from src.utils.logger import setup_logger

log = setup_logger("composer")

W, H, FPS = SHORTS_WIDTH, SHORTS_HEIGHT, SHORTS_FPS


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _ffprobe_duration(path: Path) -> float:
    exe = shutil.which("ffprobe")
    if exe:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        )
        try:
            return float(out.stdout.strip())
        except ValueError:
            pass
    # 폴백: ffmpeg 로그 파싱
    out = subprocess.run([_ffmpeg(), "-i", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out.stderr)
    if m:
        h, mm, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mm * 60 + s
    return 50.0


# ── 자막(교정된 표기) ASS 생성 ──────────────────────────
# force_style은 일부 ffmpeg 빌드에서 렌더를 죽인다 → 스타일을 담은 ASS를 직접 생성.
def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# 자막 한 줄 글자 수 상한. 폭 1080에 좌우 여백 60씩이면 960px가 남고,
# NanumGothic 72px는 한글 14자에서 딱 찬다(실측). 예전 상한 20자는 58px
# 기준이었고 그때도 순한글 구절은 폭 경계였다.
#   58px 17자 / 64px 15자 / 72px 14자 / 80px 12자
# 쇼츠는 대부분 무음으로 보므로 자막이 사실상 주력 전달 수단인데, 58px는
# 화면에서 가장 작은 요소였다(프레임 높이의 4%).
CAPTION_MAX_CHARS = 14


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,NanumGothic,72,&H00FFFFFF,&H000000FF,&H80101010,&H00000000,-1,0,0,0,100,100,0,0,3,12,0,2,60,60,330,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# 자막 인라인 강조색(ASS는 &HBBGGRR). 노랑=RGB(255,214,10)
_HL_ON = r"{\c&H0AD6FF&}"
_HL_OFF = r"{\c&HFFFFFF&}"
# 숫자+단위, 임팩트 키워드를 노랑 강조
_NUM_RE = re.compile(r"\d[\d,\.]*\s?(?:%|퍼센트|억|만원|만|천|년|배|채|가구|평|㎡|조|위|건|일|개월)?")
_KEYWORDS = ["폭등", "급등", "급락", "폭락", "역대급", "신고가", "최고치", "최고", "최저",
             "하락", "급증", "반등", "규제", "완화", "비상", "경고"]


def _highlight(text: str) -> str:
    """숫자·핵심 키워드를 노랑으로 감싸 시선을 끈다(ASS 인라인 태그)."""
    text = _NUM_RE.sub(lambda m: f"{_HL_ON}{m.group(0)}{_HL_OFF}", text)
    for kw in _KEYWORDS:
        text = text.replace(kw, f"{_HL_ON}{kw}{_HL_OFF}")
    return text


# 자막 줄을 끊으면 안 되는 자리. 숫자와 단위가 갈리면(2억 / 2천, 전국 / 1위)
# 읽는 쪽에서 한 덩어리로 안 보이고, 숫자에 거는 노랑 강조도 두 줄로 쪼개진다.
# 2026-09-14 검증 프레임에서 "…이광수 애널리스트가 정부" 처럼 조사 뒤에서
# 끊기는 것도 같이 나왔다.
_UNIT_TOK = re.compile(
    r"^(?:%|퍼센트|억|만원|만|천|원|년|배|채|가구|평|㎡|제곱미터|조|위|건|개월|명|주|일|개)")
# 앞 토큰이 숫자로 끝나면 다음 단위 토큰은 붙여 둔다.
_ENDS_NUM = re.compile(r"[\d]$")
# "2억 2천"처럼 금액이 두 토큰으로 이어지는 경우. 앞이 단위로 끝나고
# 뒤가 숫자로 시작하면 같은 수 하나다.
_ENDS_UNIT = re.compile(r"(?:%|억|만원|만|천|원|년|배|채|가구|평|㎡|조|위|건|개월|명|주|일|개)$")
_STARTS_NUM = re.compile(r"^\d")


def _glue_tokens(tokens: list[str]) -> list[list[str]]:
    """끊으면 안 되는 토큰끼리 미리 묶는다(숫자+단위, 관형사+의존명사)."""
    groups: list[list[str]] = []
    for tok in tokens:
        prev = groups[-1][-1] if groups else ""
        # 앞이 숫자거나 이미 수 단위로 끝났으면, 뒤따르는 단위·조수사는
        # 같은 수의 일부다. "3천 건", "2억 2천", "3,481 가구" 모두 해당한다.
        num_like = bool(_ENDS_NUM.search(prev) or _ENDS_UNIT.search(prev))
        if groups and (
            (num_like and _UNIT_TOK.match(tok))
            or (_ENDS_UNIT.search(prev) and _STARTS_NUM.match(tok))
            or prev in ("한", "두", "세", "네", "이", "그", "저")
        ):
            groups[-1].append(tok)
        else:
            groups.append([tok])
    return groups


def _wrap_tokens(tokens: list[str], limit: int = CAPTION_MAX_CHARS) -> list[str]:
    """토큰을 limit자 이내 줄로 묶되, 붙여야 할 덩어리는 쪼개지 않는다."""
    lines: list[str] = []
    cur = ""
    for grp in _glue_tokens(tokens):
        piece = " ".join(grp)
        if not cur:
            cur = piece
        elif len(cur) + len(piece) + 1 <= limit:
            cur = f"{cur} {piece}"
        else:
            lines.append(cur)
            cur = piece
    if cur:
        lines.append(cur)
    return lines


def _split_phrases(text: str) -> list[str]:
    """문장을 짧은 구절(자막 한 줄)로 분할."""
    text = re.sub(r"\s+", " ", text).strip()
    # 문장부호 기준 1차 분할 후, 너무 길면 공백으로 2차 분할
    rough = re.split(r"(?<=[.!?])\s+|(?<=[다요])\s+", text)
    phrases: list[str] = []
    for r in rough:
        r = r.strip()
        if not r:
            continue
        if len(r) <= CAPTION_MAX_CHARS + 1:
            phrases.append(r)
        else:
            phrases.extend(_wrap_tokens(r.split(), limit=CAPTION_MAX_CHARS))
    return [p for p in phrases if p]


_SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def parse_srt_cues(path) -> list[tuple[str, float, float]]:
    """SRT를 (텍스트, 시작초, 끝초) 리스트로. 실패하면 빈 리스트.

    edge-tts가 합성하면서 남긴 실제 발화 시각이다. 없거나 깨져도 파이프라인은
    글자수 비례 배분으로 돌아간다.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, TypeError):
        return []
    cues, lines = [], raw.splitlines()
    for i, ln in enumerate(lines):
        m = _SRT_TIME_RE.search(ln)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / (1000 if len(m.group(4)) == 3 else 100)
        en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / (1000 if len(m.group(8)) == 3 else 100)
        text = []
        for nxt in lines[i + 1:]:
            if not nxt.strip() or _SRT_TIME_RE.search(nxt):
                break
            text.append(nxt.strip())
        if text:
            cues.append((" ".join(text), st, en))
    return cues


def _timings_from_cues(phrases: list[str], cues: list[tuple[str, float, float]],
                       total_sec: float) -> list[tuple[str, float, float]] | None:
    """발화 시각(cues)에 자막 구절을 맞춘다. 맞출 수 없으면 None.

    자막 원문(caption_script)과 발화 원문(speech_script)은 표기가 다르다
    ("84㎡" → "팔십사 제곱미터"). 그래서 글자를 직접 맞추지 않고, 각 구절의
    '발화 기준 길이'만큼 cue를 소비해 경계를 잡는다.
    """
    if not cues or not phrases:
        return None
    from src.script_gen.correct_terms import to_speech
    targets = [max(1, len(to_speech(p))) for p in phrases]
    total_target = sum(targets)
    total_cue = sum(len(c[0]) for c in cues) or 1
    scale = total_cue / total_target

    # edge-tts SubMaker는 문장 단위로 묶어 준다(실측: 대본 32구절 → 8구간).
    # 구절이 구간보다 많으므로 '구간 하나를 소비'하는 방식으로는 못 나눈다.
    # 대신 누적 발화 길이를 구간 시간축에 선형으로 투영해 경계를 잡는다.
    # 오차가 한 문장 안으로 묶여, 전체를 글자수로 배분하던 것보다 정확하다.
    spans, acc = [], 0.0
    for text, st, en in cues:
        n = max(1, len(text))
        spans.append((acc, acc + n, st, en))
        acc += n
    total_cue_chars = acc or 1

    def at(pos: float) -> float:
        """발화 누적 글자 위치 → 초."""
        pos = min(max(pos, 0.0), total_cue_chars)
        for lo, hi, st, en in spans:
            if pos <= hi:
                ratio = (pos - lo) / max(1e-6, hi - lo)
                return st + (en - st) * ratio
        return spans[-1][3]

    out, cursor = [], 0.0
    for ph, tgt in zip(phrases, targets):
        start = at(cursor)
        cursor += tgt * scale
        end = min(total_sec, max(at(cursor), start + 0.4))
        out.append((ph, start, end))
    if not out:
        return None
    # 마지막은 오디오 끝까지 붙인다.
    ph, st, _ = out[-1]
    out[-1] = (ph, st, total_sec)
    return out


def _phrase_timings(caption_script: str, total_sec: float,
                    cues: list[tuple[str, float, float]] | None = None
                    ) -> list[tuple[str, float, float]]:
    """구절별 (구절, 시작, 끝). cues가 있으면 실제 발화 시각을 쓴다.

    없으면 예전처럼 오디오 길이에 글자수 비례로 배분한다. 비례 배분은
    숫자를 읽느라 길어지는 구간을 반영하지 못한다("4,278가구"는 글자수보다
    오래 걸린다).
    """
    phrases = _split_phrases(caption_script)
    if cues:
        fitted = _timings_from_cues(phrases, cues, total_sec)
        if fitted:
            return fitted
    total_chars = sum(len(p) for p in phrases) or 1
    t = 0.0
    out = []
    for ph in phrases:
        dur = max(0.9, total_sec * len(ph) / total_chars)
        start, end = t, min(total_sec, t + dur)
        out.append((ph, start, end))
        t = end
    return out


def build_caption_ass(caption_script: str, total_sec: float, out: Path,
                      cues: list[tuple[str, float, float]] | None = None) -> Path:
    """교정된 자막을 ASS로 저장(스타일 내장). cues가 있으면 실제 발화 시각을 쓴다."""
    out.parent.mkdir(parents=True, exist_ok=True)
    body = []
    for ph, start, end in _phrase_timings(caption_script, total_sec, cues):
        text = _highlight(ph.replace("\n", " ").strip())
        body.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{text}")
    out.write_text(ASS_HEADER + "\n".join(body) + "\n", encoding="utf-8")
    return out


def _plan_stat_overlays(caption_script: str, total_sec: float, title_dur: float,
                        max_n: int = 6,
                        blocked: tuple[float, float] | None = None,
                        cues: list[tuple[str, float, float]] | None = None
                        ) -> list[tuple[Path, float, float]]:
    """대본 구절에서 핵심 수치를 뽑아 (스탯카드경로, 시작, 끝) 오버레이 계획 생성.

    앞에서부터 max_n개를 집고 끊으면 콜아웃이 도입부에만 몰린다. 55초짜리
    실측에서 후보가 11개였는데 앞 3개가 전부 0~9초에 있었고, 나머지 46초에는
    하나도 뜨지 않았다(검증 프레임 8장 중 0장). 화면 중앙을 채우라고 만든
    기능인데 사실상 없는 기능이었다.

    그래서 영상 전체를 max_n 구간으로 나눠 구간마다 하나씩 고른다. 같은
    수치가 연달아 나오면 건너뛴다.

    blocked 구간(기사 캡처)에는 띄우지 않는다. 기사 카드 자체가 글자로 꽉 찬
    화면이라, 그 위에 210px 숫자 패널을 얹으면 둘 다 못 읽는다
    (2026-09-14 검증 프레임 12초 지점에서 실제로 그렇게 나왔다).
    """
    from src.editor.stat_callout import (pick_stat, pick_keyword,
                                          render_stat_card, is_weak)
    cands, kw_cands = [], []
    n_phrase = n_stat = n_blocked = 0
    for ph, s, e in _phrase_timings(caption_script, total_sec, cues):
        if e <= title_dur:      # 타이틀카드 구간은 건너뜀
            continue
        n_phrase += 1
        cs, ce = max(s, title_dur), min(total_sec, e + 0.4)
        in_article = bool(blocked and cs < blocked[1] and blocked[0] < ce)
        st = pick_stat(ph)
        if st:
            n_stat += 1
            if in_article:
                n_blocked += 1
                continue      # 기사 캡처 구간과 겹치면 버린다
            cands.append((st, cs, ce))
            continue
        # 수치가 없는 구절은 핵심어 후보로 남겨 둔다. 대본 후반은 해석이라
        # 숫자가 없고, 없는 숫자를 만들 수는 없다(규칙 17). 화면을 채우면서
        # 없는 사실을 만들지 않는 방법이 이것뿐이다.
        if not in_article:
            kw = pick_keyword(ph)
            if kw:
                kw_cands.append(((kw, "flat"), cs, ce))
    note(f"콜아웃 후보: 구절 {n_phrase}개 중 수치 {n_stat}개 "
         f"(기사구간 제외 {n_blocked}개) → 수치 {len(cands)}개 · 핵심어 {len(kw_cands)}개")
    if not cands and not kw_cands:
        return []

    span_start = title_dur
    span = max(0.1, total_sec - span_start)
    picked: list[tuple] = []
    used = set()
    for i in range(max_n):
        lo = span_start + span * i / max_n
        hi = span_start + span * (i + 1) / max_n
        bucket = [c for c in cands
                  if c[1] not in used and lo <= c[1] < hi
                  and not (picked and picked[-1][0][0] == c[0][0])]
        if not bucket:
            continue
        # 한 구간에 여러 후보가 있으면 강한 수치(%·억·가구 …)를 먼저 쓴다.
        bucket.sort(key=lambda c: (is_weak(c[0][0]), c[1]))
        st, s, e = bucket[0]
        picked.append((st, s, e))
        used.add(s)

    # 구간마다 하나씩만 고르면, 수치가 한쪽에 몰린 대본에서 전체가 한두 개로
    # 끝난다(실측 58초 영상에서 1개). 자리가 남으면 남은 후보로 채운다.
    # 다만 서로 최소 4초는 떨어뜨려 연달아 튀어나오지 않게 한다.
    if len(picked) < max_n:
        # 수치가 남아 있으면 먼저 쓰고, 그다음에 핵심어로 빈 구간을 채운다.
        for st, s, e in cands + kw_cands:
            if len(picked) >= max_n:
                break
            if s in used:
                continue
            if any(abs(s - ps) < 4.0 for _, ps, _ in picked):
                continue
            if any(st[0] == pst[0] for pst, _, _ in picked):
                continue      # 같은 수치 반복은 안 쓴다
            picked.append((st, s, e))
            used.add(s)

    # 겹침·과다 노출 정리. 구절이 길면 그 구절 길이만큼(10초까지) 큰 숫자가
    # 화면에 박혀 있게 되고, 앞뒤 구간에서 하나씩 고르다 보면 두 개가 동시에
    # 떠 있는 구간도 생긴다. 다음 콜아웃 직전까지로 자르고 상한을 둔다.
    picked.sort(key=lambda c: c[1])
    overlays = []
    for i, (st, s, e) in enumerate(picked):
        e = min(e, s + STAT_MAX_SEC)
        if i + 1 < len(picked):
            e = min(e, picked[i + 1][1] - 0.2)
        if e - s < 0.8:           # 너무 짧으면 깜빡이는 것처럼 보인다
            continue
        path = VIDEO_DIR / f"stat_{len(overlays)}.png"
        render_stat_card(st[0], st[1], path)
        overlays.append((path, s, e))
    return overlays


def _seg_filter(idx: int, dur: float, zoom_in: bool,
                scroll: bool = False, fit: bool = False,
                punch: bool = False) -> str:
    """한 세그먼트의 필터 체인([idx:v] → [vidx]).

    scroll=True(긴 기사): 폭 맞추고 위→아래로 천천히 세로 스크롤.
    fit=True(짧은 기사 카드): 폭 맞추고 어두운 배경 중앙에 정적 배치(잘림·백지 없음).
    punch=True(타이틀카드): 같은 12% 줌을 세그먼트 길이 안에 다 쓴다. 기본 줌
        속도는 프레임당 고정이라 1.5초짜리에서는 5%밖에 안 움직여 사실상 정지
        화면으로 보인다. 첫 화면이 움직이는지 아닌지가 이 변경의 핵심이다.
    그 외: zoompan 켄번즈(d=1, 출력프레임 on 으로 줌 구동).
    """
    if scroll:
        # 폭 1080에 맞춘 세로 긴 기사 이미지를 위→아래로 스크롤(끝 0.4s는 정지)
        hold = max(0.1, dur - 0.4)
        chain = (
            f"scale={W}:-2,"
            f"crop={W}:{H}:0:'(ih-{H})*min(1,t/{hold:.3f})'"
        )
        return f"[{idx}:v]{chain},setsar=1[v{idx}]"
    if fit:
        # 짧은 카드: 폭 맞춘 뒤 어두운 캔버스 중앙에 배치(패딩)
        chain = (f"scale={W}:-2,"
                 f"pad={W}:{H}:0:(oh-ih)/2:color=0x101624")
        return f"[{idx}:v]{chain},setsar=1[v{idx}]"
    if KENBURNS:
        # 과도한 업스케일은 CI에서 느리다 → 1.2배(1296x2304)면 충분.
        rate = 0.0012
        if punch:
            # 12%를 이 세그먼트 안에서 다 쓴다(프레임 수로 나눈다).
            rate = 0.12 / max(1.0, dur * FPS)
        if zoom_in:
            z = f"min(1.0+{rate:.6f}*on,1.12)"
        else:
            z = f"max(1.12-{rate:.6f}*on,1.0)"
        chain = (
            f"scale=1296:2304:force_original_aspect_ratio=increase,crop=1296:2304,"
            f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={W}x{H}:fps={FPS}"
        )
    else:
        chain = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    return f"[{idx}:v]{chain},setsar=1[v{idx}]"


def _plan_segments(title_card: Path, article_img: Path | None,
                   bg_paths: list[Path], dur: float) -> list[tuple[Path, float]]:
    """도입 3~4초 룰 + 3초 컷 + 기사 중간 배치."""
    t_title = min(TITLE_CARD_MAX_SEC, dur * 0.2)
    t_article = min(8.0, max(4.0, dur * 0.22)) if article_img else 0.0
    rest = max(1.0, dur - t_title - t_article)
    n_img = max(1, math.ceil(rest / IMAGE_MAX_SEC))
    per = rest / n_img

    imgs = cycle(bg_paths) if bg_paths else cycle([title_card])
    img_segs = [(next(imgs), per) for _ in range(n_img)]

    segs: list[tuple[Path, float]] = [(title_card, t_title)]
    if article_img and img_segs:
        # 첫 이미지컷 뒤에 기사 캡처 삽입
        segs.append(img_segs[0])
        segs.append((article_img, t_article))
        segs.extend(img_segs[1:])
    elif article_img:
        segs.append((article_img, t_article))
    else:
        segs.extend(img_segs)

    # 반올림 오차를 마지막 세그먼트에서 보정
    diff = dur - sum(d for _, d in segs)
    if segs:
        last_img, last_d = segs[-1]
        segs[-1] = (last_img, max(0.5, last_d + diff))
    return segs


def _sub_filter(ass_path: Path, fonts_dir: Path) -> str:
    """ASS 자막 번인 필터. 스타일은 ASS에 내장, fontsdir로 번들 폰트 지정."""
    p = ass_path.as_posix().replace(":", r"\:")
    fd = fonts_dir.as_posix().replace(":", r"\:")
    return f"subtitles='{p}':fontsdir='{fd}'"


def compose(caption_script: str, audio_path: Path, title_card: Path,
            article_img: Path | None, bg_paths: list[Path],
            out_name: str = "final", banner: Path | None = None,
            srt_path: Path | None = None) -> Path:
    """단일 filter_complex 패스로 켄번즈+concat+(상단배너)+자막번인+오디오mux.

    per-세그먼트 클립을 만들어 concat 데뮤서로 잇는 방식은 zoompan 타임스탬프
    문제로 세그먼트가 유실될 수 있어, concat '필터'로 한 번에 합친다.
    banner가 주어지면 타이틀카드 이후 전 구간 상단에 헤드라인 배너를 오버레이해
    어떤 프레임이 Shorts 썸네일로 뽑혀도 헤드라인이 보이게 한다.
    """
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FINAL_DIR / f"{out_name}.mp4"
    dur = _ffprobe_duration(audio_path)
    log.info(f"  오디오 길이 {dur:.1f}s")
    # TTS가 남긴 실제 발화 시각. 자막과 콜아웃이 같은 타임라인을 쓰도록
    # 한 번만 읽어 둘 다에 넘긴다.
    cues = parse_srt_cues(srt_path) if srt_path else []
    note(f"자막 타이밍: {'발화 시각 ' + str(len(cues)) + '구간' if cues else '글자수 비례(폴백)'}")

    # 자막 SRT·폰트는 비ASCII(한글) 경로에서 libass가 실패할 수 있어 임시 ASCII 폴더에 둔다.
    asset_dir = Path(tempfile.gettempdir()) / "imjang_subs"
    asset_dir.mkdir(exist_ok=True)
    for ttf in FONT_DIR.glob("*.ttf"):
        dst = asset_dir / ttf.name
        if not dst.exists():
            shutil.copyfile(ttf, dst)
    ass = build_caption_ass(caption_script, dur, asset_dir / "display.ass", cues=cues)
    segs = _plan_segments(title_card, article_img, bg_paths, dur)
    title_dur = segs[0][1]
    log.info(f"  세그먼트 {len(segs)}개 (타이틀 {title_dur:.1f}s 등)")
    # 기사 캡처 세그먼트의 시간대를 구해 콜아웃 금지 구간으로 넘긴다.
    blocked = None
    if article_img:
        t = 0.0
        for img, d in segs:
            if str(img) == str(article_img):
                blocked = (t, t + d)
                break
            t += d
    stats = _plan_stat_overlays(caption_script, dur, title_dur, blocked=blocked, cues=cues)
    # 계획을 파일로 남긴다. 콜아웃이 떴는지 아닌지는 프레임 몇 장을 떠서
    # 눈으로 맞히기 어렵다(2~3.5초씩만 뜬다). 검증 아티팩트에 같이 실어
    # 몇 시에 무엇이 뜨는지 바로 보게 한다.
    note(f"영상 {dur:.1f}s · 컷 {len(segs)}개 · 타이틀카드 {title_dur:.1f}s"
         + (f" · 기사 {blocked[0]:.1f}~{blocked[1]:.1f}s(콜아웃 금지)" if blocked else ""))
    for pth, a, b in stats:
        note(f"콜아웃 {a:6.1f}~{b:5.1f}s  {pth.name}")
    if not stats:
        note("콜아웃 없음")
    log.info(f"  숫자 콜아웃 {len(stats)}개"
             + (f" (기사 구간 {blocked[0]:.1f}~{blocked[1]:.1f}s 제외)" if blocked else ""))

    # 입력 구성: 각 세그먼트 이미지 (+상단 배너 +스탯카드) + 오디오
    inputs: list[str] = []
    for img, d in segs:
        # -framerate FPS 로 입력 프레임수를 dur*FPS 로 고정 (zoompan d=1 과 정합)
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{d:.3f}", "-i", str(img)]
    banner_idx = None
    if banner and Path(banner).exists():
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(banner)]
        banner_idx = len(segs)
    stat_start_idx = len(segs) + (1 if banner_idx is not None else 0)
    for path, _s, _e in stats:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(path)]
    inputs += ["-i", str(audio_path)]
    audio_idx = stat_start_idx + len(stats)
    # 배경음(BGM): 나레이션 아래 저음량. 랜덤 트랙, 루프.
    bgm_idx = None
    bgms = list(BGM_DIR.glob("*.mp3")) if BGM_VOLUME > 0 else []
    if bgms:
        inputs += ["-stream_loop", "-1", "-i", str(random.choice(bgms))]
        bgm_idx = audio_idx + 1

    # 기사 세그먼트는 높이에 따라 스크롤(긴 캡처) 또는 정적 맞춤(짧은 카드)
    art_p = str(article_img) if article_img else None
    art_h = 0
    if art_p:
        try:
            from PIL import Image as _Img
            with _Img.open(art_p) as _im:
                art_h = int(_im.height * W / _im.width)  # 폭 1080 기준 높이
        except Exception:
            art_h = 0
    parts = []
    for i, (img, d) in enumerate(segs):
        is_art = str(img) == art_p
        parts.append(_seg_filter(
            i, d, zoom_in=(i % 2 == 0),
            scroll=(is_art and art_h >= H),
            fit=(is_art and art_h < H),
            punch=(i == 0),        # 타이틀카드만
        ))
    concat_ins = "".join(f"[v{i}]" for i in range(len(segs)))
    graph = ";".join(parts) + f";{concat_ins}concat=n={len(segs)}:v=1:a=0[vc]"
    cur = "vc"
    if banner_idx is not None:
        graph += (
            f";[{banner_idx}:v]scale={W}:{H}[bn]"
            f";[{cur}][bn]overlay=0:0:enable='gte(t,{title_dur:.2f})'[vb]"
        )
        cur = "vb"
    for i, (path, s, e) in enumerate(stats):
        idx = stat_start_idx + i
        graph += (
            f";[{idx}:v]scale={W}:{H}[sc{i}]"
            f";[{cur}][sc{i}]overlay=0:0:enable='between(t,{s:.2f},{e:.2f})'[vs{i}]"
        )
        cur = f"vs{i}"
    graph += f";[{cur}]{_sub_filter(ass, asset_dir)}[vout]"

    # 오디오: 나레이션 + (BGM 저음량, 끝 페이드아웃) 믹스
    if bgm_idx is not None:
        graph += (
            f";[{bgm_idx}:a]volume={BGM_VOLUME},afade=t=out:st={max(0.0, dur - 2):.2f}:d=2[bgm]"
            f";[{audio_idx}:a][bgm]amix=inputs=2:duration=first:normalize=0[aout]"
        )
        amap = "[aout]"
    else:
        amap = f"{audio_idx}:a"

    cmd = [
        _ffmpeg(), "-y", *inputs,
        "-filter_complex", graph,
        "-map", "[vout]", "-map", amap,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log.error(f"합성 실패: {(r.stderr or '')[-800:]}")
        raise subprocess.CalledProcessError(r.returncode, "compose")

    log.info(f"  완성 → {out_path.name} ({out_path.stat().st_size // 1024}KB)")
    return out_path
