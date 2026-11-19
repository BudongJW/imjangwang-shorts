"""로컬 미리보기 렌더. 키 없이(Gemini·TTS·Pexels·유튜브 없이) 화면 구성만 확인한다.

화면을 고친 뒤 결과를 보려면 지금까지는 run/daily-verify-* 브랜치를 밀어 Actions가
렌더한 아티팩트를 내려받아야 했다. 이 스크립트는 지정 대본 파일 하나로 같은 합성
경로(src/editor/composer.compose)를 그대로 돌린다. 자막 줄바꿈, 숫자·비교 카드가 뜨는
시각, 기사 화면 위치, 타이틀카드를 바로 볼 수 있다.

    python scripts/preview_render.py path/to/pinned_plan.json
    python scripts/preview_render.py plan.json --arm article_pos=late --arm bg_mode=graphic
    python scripts/preview_render.py plan.json --bg-dir ~/사진들 --frames

실제 영상과 다른 점: 목소리가 없고(무음, 길이는 글자 수로 환산), 배경은 폴백 그림이나
--bg-dir의 사진이고, 기사 화면은 캡처 대신 카드다(네트워크를 쓰지 않는다).
ffmpeg가 있어야 한다. 업로드와 토픽 기록은 하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
# 눈으로 확인할 시점(초). 첫 화면은 촘촘히 본다.
FRAME_TIMES = (0.0, 1.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0, 40.0)


def _srt_time(x: float) -> str:
    h, rem = divmod(x, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def _fake_narration(speech: str, audio: Path, srt: Path) -> float:
    """글자 수로 환산한 길이의 무음과, 문장별 발화 시각을 만든다."""
    from config.settings import CHARS_PER_SEC
    dur = max(5.0, len(speech) / CHARS_PER_SEC)
    audio.parent.mkdir(parents=True, exist_ok=True)
    srt.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono", "-t", f"{dur:.2f}", str(audio)], check=True)
    sents = [x for x in re.split(r"(?<=[.!?])\s+", speech) if x]
    total = sum(len(x) for x in sents) or 1
    rows, t = [], 0.0
    for i, sent in enumerate(sents, 1):
        d = dur * len(sent) / total
        rows.append(f"{i}\n{_srt_time(t)} --> {_srt_time(t + d)}\n{sent}\n")
        t += d
    srt.write_text("\n".join(rows), encoding="utf-8")
    return dur


def main() -> int:
    ap = argparse.ArgumentParser(description="키 없이 화면 구성만 확인하는 로컬 렌더")
    ap.add_argument("plan", help="지정 대본 JSON (output/pinned_plan.json과 같은 형식)")
    ap.add_argument("--arm", action="append", default=[], metavar="KEY=ARM",
                    help="실험 팔을 고정한다(예: article_pos=late). 여러 번 줄 수 있다")
    ap.add_argument("--bg-dir", help="배경으로 쓸 사진 폴더(없으면 폴백 그림)")
    ap.add_argument("--frames", action="store_true",
                    help="output/frames/ 에 확인용 프레임을 뽑는다")
    args = ap.parse_args()

    data = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    a, p = data["article"], data["plan"]

    from src import experiments
    forced = dict(x.split("=", 1) for x in args.arm)
    unknown = [k for k in forced if k not in experiments.BY_KEY]
    if unknown:
        print(f"모르는 실험: {', '.join(unknown)} (있는 것: {', '.join(experiments.BY_KEY)})")
        return 2
    if forced:
        real_arm = experiments.arm
        experiments.arm = lambda key, day=None, state=None: (
            forced.get(key) or real_arm(key, day, state))

    from config.settings import AUDIO_DIR, SRT_DIR, VIDEO_DIR, OUTPUT_DIR
    from src.collector import images, news
    from src.collector.article_capture import build_article_visual
    from src.editor.composer import compose
    from src.editor.title_card import (layout_by_name, pick_accent, render_headline_banner,
                                       render_title_card, resolve_face)
    from src.script_gen.correct_terms import to_speech
    from src.utils import buildnotes

    buildnotes.reset()
    cap = str(p["caption_script"]).strip()
    speech = to_speech(str(p.get("speech_script") or cap).strip())
    audio, srt = AUDIO_DIR / "narration.mp3", SRT_DIR / "narration.srt"
    dur = _fake_narration(speech, audio, srt)

    if args.bg_dir:
        bgs = sorted(x for x in Path(args.bg_dir).expanduser().iterdir()
                     if x.suffix.lower() in IMG_EXTS)[:6]
    else:
        bgs = []
    if not bgs:
        # 키가 없으면 collect_backgrounds가 폴백 그림을 만들어 준다.
        bgs = images.collect_backgrounds("", need=6, queries=["apartment", "city"])

    # url을 비워 Playwright 캡처를 건너뛰고 카드로 그린다.
    art = news.Article(title=a.get("title", ""), source=a.get("source", ""),
                       summary=a.get("summary", ""), published=a.get("published", ""))
    face = resolve_face(str(p.get("face", "")).strip(), f"{art.title} {art.summary}")
    lay = layout_by_name(str(p.get("layout", "")).strip()) if p.get("layout") else None
    accent = pick_accent()
    hook = p.get("hook_word", "")
    title_card = render_title_card(p["headline"], hook, background=bgs[0], accent=accent,
                                   face=face, layout=lay)
    banner = render_headline_banner(p["headline"], hook, accent=accent)
    article_img = build_article_visual(art, highlight=p.get("highlight_sentence", ""))
    buildnotes.note(f"대본 {len(cap)}자: {cap}")
    final = compose(cap, audio, title_card, article_img, list(bgs), banner=banner, srt_path=srt)

    print(f"\n완성: {final} ({dur:.1f}초, 무음)")
    print(f"타이틀카드: {title_card}")
    notes = VIDEO_DIR / "build_notes.txt"
    if notes.exists():
        print("\n" + notes.read_text(encoding="utf-8"))
    if args.frames:
        out = OUTPUT_DIR / "frames"
        out.mkdir(parents=True, exist_ok=True)
        for t in (x for x in FRAME_TIMES if x < dur):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", str(final),
                            "-frames:v", "1", "-vf", "scale=432:-2",
                            str(out / f"t{t:05.1f}.png")], check=True)
        print(f"프레임: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
