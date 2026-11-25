"""움직임 묶음 — 정지 화면처럼 보이던 구성에 움직임을 넣는다(2026-11-25).

바꾸기 전 화면은 틀 하나가 50초 동안 그대로였다. 배너는 고정이고, 배경은 가운데로만
천천히 당겼다 밀었다 하고, 숫자 카드와 자막은 움직임 없이 켜졌다 꺼졌다. 화면에서 실제로
바뀌는 것이 3초에 한 번 바뀌는 사진뿐이라 슬라이드 쇼로 읽혔다.

여기서는 새 정보를 만들지 않는다. 이미 있는 요소가 '어떻게 나타나고 움직이는지'만 정한다.
    컷      : 줌 인·아웃에 좌우·상하 팬을 섞고, 컷이 바뀌는 순간 짧게 당겼다 놓는다.
    카드    : 숫자 카드는 튕기며 커지고, 전체 화면 카드는 옆에서 밀려 들어온다.
    비교 막대: 막대가 바닥에서 자란다(값 글자는 처음부터 대본 값 그대로다).
    기사 화면: 아래에서 밀려 올라왔다가 도로 내려가고, 머무는 시간을 줄인다.
    제목 배너: 강조어가 0.45초에 노랗게 켜진다(제목은 첫 프레임부터 떠 있다).
자막의 어절 강조는 자막을 만드는 composer에 있다.

근거는 2026-11-25 레퍼런스 조사다. 조회수가 나오는 뉴스·경제 쇼츠(WSJ, KBS 크랩 등)는
글자와 그래픽이 '나타나는 과정'을 보여 준다(제목을 쳐 넣고, 그래픽을 한 칸씩 쌓는다).
한국 상위 채널의 자막은 구절 단위에 핵심어만 노랑이라 그 틀은 그대로 두었다.
효과음, 진행 막대, 숫자 세어 올리기, 더 빠른 컷은 근거를 찾지 못해 넣지 않았다.

끄려면 MOTION_FX=0 (config/settings.py). 끄면 2026-11-10판 화면 그대로다.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from config.settings import SHORTS_WIDTH, SHORTS_HEIGHT, SHORTS_FPS

W, H, FPS = SHORTS_WIDTH, SHORTS_HEIGHT, SHORTS_FPS

# ── 컷 움직임 ────────────────────────────────────────────
# 줌만 번갈아 쓰면 모든 컷이 같은 움직임이다. 팬을 사이에 끼워 방향을 바꾼다.
# 0번은 늘 'in'이다. 끝 컷이 줌아웃으로 배율 1.0에서 끝나 첫 컷의 시작과 이어져야 한다.
CUT_KINDS = ("in", "pan_r", "out", "pan_u", "in", "pan_l", "out", "pan_d")
ZOOM_MAX = 1.12
ZOOM_RATE = 0.0012          # 프레임당. 3초 컷에서 약 11%
# 팬은 배율을 고정하고 옆으로 민다. 원본을 1.2배(1296px)로 올려 두므로 1.15배까지는
# 확대 손실이 없다(1296 / 1.15 = 1127px ≥ 1080px).
PAN_ZOOM = 1.15
# 컷이 바뀌는 순간 이만큼 더 당겼다가 PUNCH_FRAMES 동안 놓는다. 같은 자리에서 사진만
# 바뀌면 바뀐 줄 모르고 지나가는데, 짧게 튕기면 컷이 눈에 들어온다.
PUNCH = 0.06
PUNCH_FRAMES = 6


def cut_kind(i: int, loop_end: bool = False) -> str:
    if loop_end:
        return "out"
    return CUT_KINDS[i % len(CUT_KINDS)]


def kenburns(kind: str, dur: float, punch: bool = True, exact: bool = False) -> str:
    """zoompan 필터 한 개. exact면 12%를 이 컷 안에 정확히 다 쓴다(끝 컷을 1.0에 맞출 때)."""
    n = max(1.0, dur * FPS)
    rate = (ZOOM_MAX - 1.0) / n if exact else ZOOM_RATE
    p = f"+{PUNCH}*pow(max(0,1-on/{PUNCH_FRAMES}),2)" if punch else ""
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    prog = f"min(1,on/{n:.1f})"
    if kind == "out":
        z, x, y = f"max({ZOOM_MAX}-{rate:.6f}*on,1.0)", cx, cy
    elif kind == "pan_r":
        z, x, y = f"{PAN_ZOOM}", f"(iw-iw/zoom)*{prog}", cy
    elif kind == "pan_l":
        z, x, y = f"{PAN_ZOOM}", f"(iw-iw/zoom)*(1-{prog})", cy
    elif kind == "pan_u":
        z, x, y = f"{PAN_ZOOM}", cx, f"(ih-ih/zoom)*(1-{prog})"
    elif kind == "pan_d":
        z, x, y = f"{PAN_ZOOM}", cx, f"(ih-ih/zoom)*{prog}"
    else:
        z, x, y = f"min(1.0+{rate:.6f}*on,{ZOOM_MAX})", cx, cy
    return f"zoompan=z='{z}{p}':d=1:x='{x}':y='{y}':s={W}x{H}:fps={FPS}"


# ── 구간 전환 ────────────────────────────────────────────
# 기사 화면은 사진 컷과 성격이 다른 구간이다. 뚝 바뀌는 대신 문서를 끌어올렸다가
# 내려놓는 것처럼 민다. 사진 컷끼리는 그대로 끊는다(전부 밀면 슬라이드 쇼가 된다).
XFADE_SEC = 0.22
XFADE_IN = "slideup"
XFADE_OUT = "slidedown"
# 기사 화면이 머무는 시간 상한(초). 글자로 꽉 찬 화면이라 영상에서 가장 멈춰 보이는
# 구간이다. 구간별 시청 유지에서도 이 구간에 이탈이 몰렸다(docs/improvement-loop.md).
# 5초에서 줄인다. 강조 문장까지 내려가 1.2초 머무는 데는 충분하다.
ARTICLE_MAX_SEC = 3.5
ARTICLE_MIN_SEC = 3.0


def join_graph(seg_len: list[float], trans: list[str | None]) -> str:
    """세그먼트 [v0]…[vN]을 이어 [vc]로 내는 필터 그래프.

    trans[i]는 i번과 i+1번 사이의 전환 이름이다. None이면 그대로 끊는다(concat).
    전환이 있는 자리에서 앞 세그먼트는 XFADE_SEC만큼 길게 렌더돼 있어야 한다
    (seg_len에 그 길이가 들어 있다). 전환은 원래 컷 시각에 시작해 XFADE_SEC 뒤에 끝나므로
    전체 길이와 컷 시각은 전환이 없을 때와 같다.
    """
    groups: list[tuple[list[int], str | None]] = []
    cur = [0]
    for i, tr in enumerate(trans):
        if tr:
            groups.append((cur, tr))
            cur = [i + 1]
        else:
            cur.append(i + 1)
    groups.append((cur, None))
    out = []
    for gi, (idxs, _) in enumerate(groups):
        ins = "".join(f"[v{i}]" for i in idxs)
        # xfade는 두 입력의 프레임레이트·시간 단위·픽셀 형식이 같아야 한다.
        out.append(f"{ins}concat=n={len(idxs)}:v=1:a=0,fps={FPS},format=yuv420p,settb=AVTB"
                   f"[{'vc' if len(groups) == 1 else f'g{gi}'}]")
    label = "g0"
    total = sum(seg_len[i] for i in groups[0][0])
    for gi in range(1, len(groups)):
        nxt = "vc" if gi == len(groups) - 1 else f"x{gi}"
        out.append(f"[{label}][g{gi}]xfade=transition={groups[gi - 1][1]}:"
                   f"duration={XFADE_SEC}:offset={max(0.0, total - XFADE_SEC):.3f}[{nxt}]")
        total += sum(seg_len[i] for i in groups[gi][0]) - XFADE_SEC
        label = nxt
    return ";".join(out)


# ── 카드 ────────────────────────────────────────────────
POP_SEC = 0.18              # 0.62배에서 1.06배까지 커지는 시간
POP_SETTLE_SEC = 0.08       # 1.06배에서 1.0배로 가라앉는 시간
FADE_IN_SEC = 0.12
FADE_OUT_SEC = 0.14
SLIDE_SEC = 0.22            # 전체 화면 카드가 옆에서 들어오는 시간
BARS_FRAMES = 15            # 비교 막대가 자라는 그림 장수(30fps에서 0.5초)
# 제목 배너의 강조어가 노랗게 켜지는 시각과 걸리는 시간. 그 전에는 흰 글자로 떠 있다.
HOOK_HL_AT = 0.45
HOOK_HL_SEC = 0.25


def crop_card(path: Path) -> tuple[Path, int, int] | None:
    """투명 카드에서 그림이 있는 부분만 잘라 (경로, 중심 x, 중심 y)를 돌려준다.

    전체 화면(1080x1920) PNG를 프레임마다 키우고 줄이면 느리다. 카드만 잘라 그 중심을
    기준으로 키운다. 그림이 없으면 None.
    """
    with Image.open(path) as im:
        im = im.convert("RGBA")
        box = im.getbbox()
        if not box:
            return None
        out = Path(path).with_name(Path(path).stem + "_crop.png")
        im.crop(box).save(out)
    return out, (box[0] + box[2]) // 2, (box[1] + box[3]) // 2


def _fades(dur: float) -> str:
    out_at = max(0.0, dur - FADE_OUT_SEC)
    return (f"fade=t=in:st=0:d={FADE_IN_SEC}:alpha=1,"
            f"fade=t=out:st={out_at:.3f}:d={FADE_OUT_SEC}:alpha=1")


def pop_filter(idx: int, label: str, start: float, dur: float) -> str:
    """잘라 낸 카드가 튕기며 커지는 필터([idx:v] → [label])."""
    scale = (f"if(lt(t,{POP_SEC}),0.62+0.44*t/{POP_SEC},"
             f"max(1,1.06-0.06*(t-{POP_SEC})/{POP_SETTLE_SEC}))")
    return (f"[{idx}:v]format=rgba,scale=w='iw*({scale})':h=-1:eval=frame,"
            f"{_fades(dur)},setpts=PTS+{start:.3f}/TB[{label}]")


def pop_overlay(cx: int, cy: int) -> str:
    return f"overlay=x='{cx}-w/2':y='{cy}-h/2':eof_action=pass"


def hold_filter(idx: int, label: str, start: float, dur: float, frames: int = 0) -> str:
    """전체 화면 카드용([idx:v] → [label]). frames가 있으면 그 장수의 그림을 차례로
    보여 준 뒤 마지막 장을 dur까지 붙든다(막대가 자라는 비교 카드)."""
    hold = (f"tpad=stop_mode=clone:stop_duration={dur:.3f},trim=duration={dur:.3f},"
            if frames else "")
    return (f"[{idx}:v]format=rgba,{hold}{_fades(dur)},"
            f"setpts=PTS+{start:.3f}/TB[{label}]")


def slide_overlay(start: float) -> str:
    """오른쪽에서 밀려 들어와 제자리에 선다."""
    x = f"if(lt(t-{start:.3f},{SLIDE_SEC}),W*pow(1-(t-{start:.3f})/{SLIDE_SEC},3),0)"
    return f"overlay=x='{x}':y=0:eof_action=pass"


def ease_out(p: float) -> float:
    """빨리 자라다 끝에서 느려진다."""
    p = min(1.0, max(0.0, p))
    return 1.0 - (1.0 - p) ** 3
