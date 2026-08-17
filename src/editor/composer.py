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
    TITLE_CARD_MAX_SEC, IMAGE_MAX_SEC, KENBURNS, BGM_VOLUME,
)
from src.editor.fonts import font_bold
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


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,NanumGothic,58,&H00FFFFFF,&H000000FF,&H80101010,&H00000000,-1,0,0,0,100,100,0,0,3,10,0,2,60,60,300,1

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
        if len(r) <= 22:
            phrases.append(r)
        else:
            cur = ""
            for tok in r.split():
                if len(cur) + len(tok) + 1 <= 20:
                    cur = (cur + " " + tok).strip()
                else:
                    phrases.append(cur)
                    cur = tok
            if cur:
                phrases.append(cur)
    return [p for p in phrases if p]


def _phrase_timings(caption_script: str, total_sec: float) -> list[tuple[str, float, float]]:
    """구절을 오디오 길이에 비례 배분해 (구절, 시작, 끝) 리스트로."""
    phrases = _split_phrases(caption_script)
    total_chars = sum(len(p) for p in phrases) or 1
    t = 0.0
    out = []
    for ph in phrases:
        dur = max(0.9, total_sec * len(ph) / total_chars)
        start, end = t, min(total_sec, t + dur)
        out.append((ph, start, end))
        t = end
    return out


def build_caption_ass(caption_script: str, total_sec: float, out: Path) -> Path:
    """교정된 자막을 오디오 길이에 비례 배분해 ASS로 저장(스타일 내장)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    body = []
    for ph, start, end in _phrase_timings(caption_script, total_sec):
        text = _highlight(ph.replace("\n", " ").strip())
        body.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{text}")
    out.write_text(ASS_HEADER + "\n".join(body) + "\n", encoding="utf-8")
    return out


def _plan_stat_overlays(caption_script: str, total_sec: float, title_dur: float,
                        max_n: int = 3) -> list[tuple[Path, float, float]]:
    """대본 구절에서 핵심 수치를 뽑아 (스탯카드경로, 시작, 끝) 오버레이 계획 생성."""
    from src.editor.stat_callout import pick_stat, render_stat_card
    overlays: list[tuple[Path, float, float]] = []
    for ph, s, e in _phrase_timings(caption_script, total_sec):
        if e <= title_dur:      # 타이틀카드 구간은 건너뜀
            continue
        st = pick_stat(ph)
        if not st:
            continue
        path = VIDEO_DIR / f"stat_{len(overlays)}.png"
        render_stat_card(st[0], st[1], path)
        overlays.append((path, max(s, title_dur), min(total_sec, e + 0.4)))
        if len(overlays) >= max_n:
            break
    return overlays


def _seg_filter(idx: int, dur: float, zoom_in: bool,
                scroll: bool = False, fit: bool = False) -> str:
    """한 세그먼트의 필터 체인([idx:v] → [vidx]).

    scroll=True(긴 기사): 폭 맞추고 위→아래로 천천히 세로 스크롤.
    fit=True(짧은 기사 카드): 폭 맞추고 어두운 배경 중앙에 정적 배치(잘림·백지 없음).
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
        if zoom_in:
            z = "min(1.0+0.0012*on,1.12)"
        else:
            z = "max(1.12-0.0012*on,1.0)"
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
            out_name: str = "final", banner: Path | None = None) -> Path:
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

    # 자막 SRT·폰트는 비ASCII(한글) 경로에서 libass가 실패할 수 있어 임시 ASCII 폴더에 둔다.
    asset_dir = Path(tempfile.gettempdir()) / "imjang_subs"
    asset_dir.mkdir(exist_ok=True)
    for ttf in FONT_DIR.glob("*.ttf"):
        dst = asset_dir / ttf.name
        if not dst.exists():
            shutil.copyfile(ttf, dst)
    ass = build_caption_ass(caption_script, dur, asset_dir / "display.ass")
    segs = _plan_segments(title_card, article_img, bg_paths, dur)
    title_dur = segs[0][1]
    log.info(f"  세그먼트 {len(segs)}개 (타이틀 {title_dur:.1f}s 등)")
    stats = _plan_stat_overlays(caption_script, dur, title_dur)
    if stats:
        log.info(f"  숫자 콜아웃 {len(stats)}개")

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
