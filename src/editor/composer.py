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
import re
import shutil
import subprocess
import tempfile
from itertools import cycle
from pathlib import Path

from config.settings import (
    SHORTS_WIDTH, SHORTS_HEIGHT, SHORTS_FPS, FINAL_DIR, VIDEO_DIR, FONT_DIR,
    TITLE_CARD_MAX_SEC, IMAGE_MAX_SEC, KENBURNS,
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
Style: Cap,NanumGothic,60,&H00FFFFFF,&H000000FF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,5,2,2,50,50,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


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


def build_caption_ass(caption_script: str, total_sec: float, out: Path) -> Path:
    """교정된 자막을 오디오 길이에 비례 배분해 ASS로 저장(스타일 내장)."""
    phrases = _split_phrases(caption_script)
    total_chars = sum(len(p) for p in phrases) or 1
    out.parent.mkdir(parents=True, exist_ok=True)
    t = 0.0
    body = []
    for ph in phrases:
        dur = max(0.9, total_sec * len(ph) / total_chars)
        start, end = t, min(total_sec, t + dur)
        text = ph.replace("\n", " ").strip()
        body.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{text}")
        t = end
    out.write_text(ASS_HEADER + "\n".join(body) + "\n", encoding="utf-8")
    return out


def _seg_filter(idx: int, dur: float, zoom_in: bool) -> str:
    """한 세그먼트의 zoompan 켄번즈 필터 체인([idx:v] → [vidx]).

    핵심: d=1 로 두고 줌을 '출력 프레임 인덱스(on)'로 구동한다.
    (d=frames + 루프 입력은 프레임 수가 N×d로 폭증하므로 금지)
    """
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
            out_name: str = "final") -> Path:
    """단일 filter_complex 패스로 켄번즈+concat+자막번인+오디오mux.

    per-세그먼트 클립을 만들어 concat 데뮤서로 잇는 방식은 zoompan 타임스탬프
    문제로 세그먼트가 유실될 수 있어, concat '필터'로 한 번에 합친다.
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
    log.info(f"  세그먼트 {len(segs)}개 (타이틀 {segs[0][1]:.1f}s 등)")

    # 입력 구성: 각 세그먼트 이미지 + 오디오
    inputs: list[str] = []
    for img, d in segs:
        # -framerate FPS 로 입력 프레임수를 dur*FPS 로 고정 (zoompan d=1 과 정합)
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{d:.3f}", "-i", str(img)]
    inputs += ["-i", str(audio_path)]
    audio_idx = len(segs)

    # 필터그래프: 세그먼트별 켄번즈 → concat → 자막 번인
    parts = [_seg_filter(i, d, zoom_in=(i % 2 == 0)) for i, (img, d) in enumerate(segs)]
    concat_ins = "".join(f"[v{i}]" for i in range(len(segs)))
    graph = (
        ";".join(parts)
        + f";{concat_ins}concat=n={len(segs)}:v=1:a=0[vc]"
        + f";[vc]{_sub_filter(ass, asset_dir)}[vout]"
    )

    cmd = [
        _ffmpeg(), "-y", *inputs,
        "-filter_complex", graph,
        "-map", "[vout]", "-map", f"{audio_idx}:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log.error(f"합성 실패: {(r.stderr or '')[-800:]}")
        raise subprocess.CalledProcessError(r.returncode, "compose")

    log.info(f"  완성 → {out_path.name} ({out_path.stat().st_size // 1024}KB)")
    return out_path
