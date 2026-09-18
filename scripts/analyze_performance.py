"""업로드된 영상들의 성적을 수집·비교 분석한다.

두 종류의 데이터를 쓴다.
  1. Data API v3  — 누적 조회수/좋아요/댓글. `youtube` 스코프로 항상 가능.
  2. Analytics API v2 — 시청지속률·평균시청시간·유입경로. 스코프나 API 활성화가
     안 돼 있으면 403이 나므로, 실패해도 1번 결과만으로 리포트를 낸다.

스냅샷을 output/performance_history.json 에 누적 저장해서, 다음 실행 때
'지난 실행 이후 얼마나 늘었는지'(증가 속도)를 계산한다. 누적 조회수만으로는
어제 올린 영상과 한 달 전 영상을 공정하게 비교할 수 없기 때문.

사용법:
    python -m scripts.analyze_performance          # 전체 리포트
    python -m scripts.analyze_performance --days 30
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.uploader.youtube import get_credentials

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).parent.parent
SNAPSHOT_PATH = ROOT / "output" / "performance_history.json"
TOPIC_HISTORY_PATH = ROOT / "output" / "topic_history.json"

_ISO_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")

# 하루당 조회수로 '평균 이상/이하'를 판정하기 위한 최소 경과 시간.
MIN_AGE_H_FOR_VERDICT = 24.0
# 게시 시각대 비교에 쓸 기간(일). 이 안의 영상끼리만 하루당 조회수를 견준다.
HOUR_WINDOW_DAYS = 45
# 시각대 하나가 이 개수는 돼야 중앙값을 믿는다.
MIN_HOUR_N = 5
# 이 시간 이상 증가가 0이면 배포가 멈춘 것으로 보고 경고한다.
STALL_H = 1.0


def _corr(xs: list[float], ys: list[float]) -> float:
    """피어슨 상관계수. 표본이 부족하거나 분산이 0이면 0.0."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = sum((a - mx) ** 2 for a in xs)
    dy = sum((b - my) ** 2 for b in ys)
    if dx <= 0 or dy <= 0:
        return 0.0
    return num / (dx * dy) ** 0.5


def _duration_seconds(iso: str) -> int:
    m = _ISO_DUR.fullmatch(iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def _parse_rfc3339(value: str) -> datetime:
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def fetch_videos(youtube) -> list[dict]:
    """채널의 업로드 영상 전체를 statistics와 함께 가져온다."""
    ch = youtube.channels().list(part="contentDetails,statistics,snippet", mine=True).execute()
    if not ch.get("items"):
        raise RuntimeError("채널을 찾을 수 없습니다 (토큰이 다른 계정일 수 있음).")
    channel = ch["items"][0]
    uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    video_ids: list[str] = []
    page = None
    while True:
        pl = youtube.playlistItems().list(
            part="contentDetails", playlistId=uploads, maxResults=50, pageToken=page
        ).execute()
        video_ids += [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
        page = pl.get("nextPageToken")
        if not page:
            break

    videos: list[dict] = []
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        res = youtube.videos().list(
            part="snippet,statistics,contentDetails,status", id=",".join(chunk)
        ).execute()
        for it in res.get("items", []):
            st = it.get("statistics", {})
            published = _parse_rfc3339(it["snippet"]["publishedAt"])
            videos.append({
                "video_id": it["id"],
                "title": it["snippet"]["title"],
                "published_at": it["snippet"]["publishedAt"],
                "published_kst": published.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
                "age_hours": (datetime.now(timezone.utc) - published).total_seconds() / 3600,
                "duration_s": _duration_seconds(it["contentDetails"].get("duration", "")),
                "privacy": it.get("status", {}).get("privacyStatus", "?"),
                "views": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
            })
    videos.sort(key=lambda v: v["published_at"], reverse=True)
    return channel, videos


def fetch_analytics(creds, video_ids: list[str], start: str, end: str) -> dict:
    """Analytics API로 영상별 시청 지표를 가져온다. 실패 시 {'error': ...}."""
    if not video_ids:
        return {"error": "대상 영상 없음"}
    try:
        ya = build("youtubeAnalytics", "v2", credentials=creds)
        res = ya.reports().query(
            ids="channel==MINE",
            startDate=start,
            endDate=end,
            metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,likes,shares",
            dimensions="video",
            filters="video==" + ",".join(video_ids[:200]),
            maxResults=200,
            sort="-views",
        ).execute()
    except HttpError as e:
        content = e.content.decode("utf-8", errors="replace") if e.content else ""
        return {"error": f"status={getattr(e.resp, 'status', '?')} {content[:400]}"}
    except Exception as e:  # 자격증명/네트워크 등
        return {"error": str(e)[:400]}

    cols = [h["name"] for h in res.get("columnHeaders", [])]
    out = {}
    for row in res.get("rows", []):
        rec = dict(zip(cols, row))
        out[rec.pop("video")] = rec
    return out


def fetch_daily(creds, start: str, end: str) -> list[dict]:
    """채널 전체의 일별 지표. 개별 영상 성적으로는 안 보이는 것을 잡는다.

    2026-09-18 실측: 09-15 474회 → 09-16 200 → 09-17 114 → 09-18 0(2.4시간).
    소재를 지정 대본으로 바꾼 날에도 0이었다. 영상 하나하나가 아니라 채널
    노출 자체가 줄고 있는지 봐야 판단이 된다.
    """
    try:
        ya = build("youtubeAnalytics", "v2", credentials=creds)
        res = ya.reports().query(
            ids="channel==MINE", startDate=start, endDate=end,
            metrics="views,estimatedMinutesWatched,subscribersGained",
            dimensions="day", sort="day",
        ).execute()
    except Exception as e:
        log_err = str(e)[:200]
        return [{"error": log_err}]
    cols = [h["name"] for h in res.get("columnHeaders", [])]
    return [dict(zip(cols, row)) for row in res.get("rows", [])]


def fetch_traffic_sources(creds, start: str, end: str) -> dict:
    """채널 전체 유입경로 분포."""
    try:
        ya = build("youtubeAnalytics", "v2", credentials=creds)
        res = ya.reports().query(
            ids="channel==MINE", startDate=start, endDate=end,
            metrics="views", dimensions="insightTrafficSourceType", sort="-views",
        ).execute()
    except Exception as e:
        return {"error": str(e)[:200]}
    return {r[0]: r[1] for r in res.get("rows", [])}


def fetch_traffic_by_video(creds, video_ids: list[str], start: str, end: str) -> dict:
    """최근 업로드별 유입경로. 채널 도달이 끊긴 건지 소재가 나쁜 건지 가른다.

    이 채널은 조회수의 95%가 SHORTS 피드에서 온다. 즉 피드에 안 실리면
    영상이 아무리 좋아도 0이다. 영상별 성적만 보면 '소재가 나빴나'와
    '피드가 안 실어줬나'가 구분이 안 된다. 업로드별로 SHORTS 비중과
    절대량을 같이 보면 갈린다 — 비중은 그대로인데 절대량만 준다면
    피드 배포량이 줄어든 것이고, SHORTS 비중 자체가 무너졌다면 그 영상이
    Shorts로 안 잡혔거나 피드에서 빠진 것이다.
    """
    if not video_ids:
        return {}
    try:
        ya = build("youtubeAnalytics", "v2", credentials=creds)
        res = ya.reports().query(
            ids="channel==MINE", startDate=start, endDate=end,
            metrics="views", dimensions="video,insightTrafficSourceType",
            filters="video==" + ",".join(video_ids[:200]), sort="-views",
        ).execute()
    except Exception as e:
        return {"error": str(e)[:200]}
    out: dict = {}
    for vid, src, views in res.get("rows", []):
        out.setdefault(vid, {})[src] = views
    return out


def load_snapshots() -> list[dict]:
    if SNAPSHOT_PATH.exists():
        try:
            return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def save_snapshot(videos: list[dict], snapshots: list[dict]) -> None:
    snapshots.append({
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "videos": {v["video_id"]: {"views": v["views"], "likes": v["likes"],
                                   "comments": v["comments"]} for v in videos},
    })
    # 90개까지만 보관 (하루 1회 기준 3개월)
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshots[-90:], ensure_ascii=False, indent=1), encoding="utf-8"
    )


def previous_views(snapshots: list[dict], video_id: str) -> tuple[int, float] | None:
    """가장 최근 스냅샷의 (조회수, 경과시간h). 없으면 None."""
    for snap in reversed(snapshots):
        rec = snap["videos"].get(video_id)
        if rec:
            taken = datetime.fromisoformat(snap["taken_at"])
            hours = (datetime.now(timezone.utc) - taken).total_seconds() / 3600
            return rec["views"], hours
    return None


# 소재 비교용. 하루당 조회수는 어린 영상일수록 높아 시기 교란이 심하다
# (2026-09-16 실측: 09-12 영상이 404/d인데 총 1,628회, 08-08 영상은 32/d인데
# 1,266회). 게시 30일이 지나 조회수가 대체로 멈춘 영상만 절대 조회수로 견준다.
TOPIC_MIN_AGE_DAYS = 30
MIN_TOPIC_N = 5
_METRO_RE = re.compile(r"서울|수도권|강남|송파|서초|마포|노원|성북|경기|인천|분당|판교|과천")
_LOCAL_RE = re.compile(r"대구|부산|광주|대전|울산|세종|창원|청주|천안|전주|포항|강원|제주|지방")
_NUM_RE = re.compile(r"\d+\s*(?:%|퍼센트|억|만원|만 원|조|배|주|가구|채|실)")


# 기사 나이 ↔ 성적. 2026-08-31에 신선도 감점(90일 초과 -12)을 넣은 뒤 9월
# 조회수가 8월 말 피크에서 내려앉았다. topic_score 최대치가 12 안팎이라
# -12는 소재 점수를 통째로 상쇄한다. 그 커밋 메시지에 반례도 적혀 있다 —
# "3월 기사 기반 08-27 영상 2,239회".
# 추측으로 감점을 되돌리지 않기로 하고(866일 된 기사를 다시 고르면 더 나쁘다)
# 2026-09-16(커밋 e53b3e6)부터 영상마다 기사 나이와 점수를 기록하기 시작했다.
PICK_TARGET_N = 7


def _pick_meta() -> dict[str, dict]:
    """video_id → 선정 당시 기록(age_days, topic_score, recency_score, source)."""
    path = ROOT / "output" / "topic_history.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for r in rows:
        if not isinstance(r, dict) or not r.get("video_id"):
            continue
        if r.get("age_days") is None:
            continue          # 계측 이전 영상. 추정해 채우지 않는다.
        out[r["video_id"]] = r
    return out


def _topic_groups(videos: list[dict]) -> list[tuple[str, list[dict]]]:
    """제목 기준으로 소재를 나눈다. 서울·수도권 / 지방 도시 / 지역 언급 없음."""
    metro, local, none = [], [], []
    for v in videos:
        t = v["title"]
        if _LOCAL_RE.search(t) and not _METRO_RE.search(t):
            local.append(v)
        elif _METRO_RE.search(t):
            metro.append(v)
        else:
            none.append(v)
    return [("서울·수도권", metro), ("지방 도시", local), ("지역 언급 없음", none)]


def _med(vals: list[int]) -> int:
    vals = sorted(vals)
    return vals[len(vals) // 2] if vals else 0

def _len_modes() -> dict[str, str]:
    """video_id → 대본 길이 모드(normal/short). topic_history.json에서 읽는다.

    2026-09-15 이전 영상에는 len_mode가 없다(그 시절은 전부 normal이지만,
    추정해 채우지 않는다 — 없는 것은 없는 대로 둔다).
    """
    path = ROOT / "output" / "topic_history.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {r["video_id"]: r["len_mode"]
            for r in rows
            if isinstance(r, dict) and r.get("video_id") and r.get("len_mode")}


def _bar(value: float, peak: float, width: int = 18) -> str:
    if peak <= 0:
        return ""
    return "█" * max(1, round(value / peak * width)) if value > 0 else ""


def build_report(channel, videos, analytics, traffic, snapshots, days, daily=None, tbv=None) -> str:
    now = datetime.now(KST)
    lines: list[str] = []
    cs = channel.get("statistics", {})
    lines.append(f"# 채널 성적 리포트 — {channel['snippet']['title']}")
    lines.append(f"기준 {now.strftime('%Y-%m-%d %H:%M')} KST · 최근 {days}일 구간")
    lines.append("")
    lines.append(f"- 구독자 {int(cs.get('subscriberCount', 0)):,}명 · "
                 f"총 조회수 {int(cs.get('viewCount', 0)):,} · "
                 f"업로드 {int(cs.get('videoCount', 0)):,}개")

    public = [v for v in videos if v["privacy"] == "public"]
    if not public:
        lines.append("\n공개 영상이 없습니다.")
        return "\n".join(lines)

    # 하루당 조회수 = 게시 시점이 다른 영상들을 공정하게 비교하는 기준
    for v in public:
        v["views_per_day"] = v["views"] / max(v["age_hours"] / 24, 0.25)
        prev = previous_views(snapshots, v["video_id"])
        v["delta"] = None
        if prev and prev[1] >= 0.5:
            v["delta"] = (v["views"] - prev[0], prev[1])

    total = sum(v["views"] for v in public)
    ranked = sorted(public, key=lambda v: v["views"], reverse=True)
    med = sorted(v["views"] for v in public)[len(public) // 2]
    lines.append(f"- 공개 영상 {len(public)}개 · 조회수 합계 {total:,} · "
                 f"영상당 중앙값 {med:,} · 평균 {total // len(public):,}")
    lines.append("")

    lines.append("## 조회수 순위")
    lines.append("")
    lines.append("| # | 조회수 | 하루당 | 좋아요 | 게시(KST) | 제목 |")
    lines.append("|--:|------:|------:|------:|----------|------|")
    for i, v in enumerate(ranked[:20], 1):
        title = v["title"].split(" #")[0][:38]
        lines.append(f"| {i} | {v['views']:,} | {v['views_per_day']:.1f} | "
                     f"{v['likes']} | {v['published_kst']} | {title} |")
    lines.append("")

    # 최신 영상 집중 분석
    latest = public[0]
    peer = [v for v in public if v["video_id"] != latest["video_id"]]
    lines.append(f"## 최신 영상 — {latest['title'].split(' #')[0]}")
    lines.append("")
    lines.append(f"`{latest['video_id']}` · 게시 {latest['published_kst']} KST · "
                 f"경과 {latest['age_hours']:.1f}시간 · 길이 {latest['duration_s']}초")
    lines.append("")
    lines.append(f"- 조회수 **{latest['views']:,}** / 좋아요 {latest['likes']} / 댓글 {latest['comments']}")
    if peer:
        rank = ranked.index(latest) + 1
        peer_med_vpd = sorted(v["views_per_day"] for v in peer)[len(peer) // 2]
        # 하루당 환산은 24시간이 지나야 의미가 있다. 갓 올린 영상은 초기
        # 몇 분의 조회수를 24배로 부풀려 무조건 '평균 이상'으로 나온다.
        # (2026-08-31 실측: 3시간짜리 영상이 1위로 표시됐다가 하루 뒤 하위권)
        if latest["age_hours"] >= MIN_AGE_H_FOR_VERDICT:
            verdict = "평균 이상" if latest["views_per_day"] >= peer_med_vpd else "평균 이하"
            lines.append(f"- 하루당 {latest['views_per_day']:.1f}회 vs 나머지 중앙값 "
                         f"{peer_med_vpd:.1f}회 → **{verdict}**")
        else:
            lines.append(f"- 경과 {latest['age_hours']:.1f}시간 → **판정 보류** "
                         f"(하루당 환산은 {MIN_AGE_H_FOR_VERDICT:.0f}시간 이후부터)")
        lines.append(f"- 전체 {len(public)}개 중 조회수 {rank}위")
    if latest["delta"]:
        gained, hours = latest["delta"]
        lines.append(f"- 지난 스냅샷 이후 {hours:.1f}시간 동안 +{gained:,}회")
        # 조회수가 멈춘 것이 실제로는 가장 중요한 신호다. 누적 숫자만 보면
        # 놓치므로 따로 경고한다.
        if gained == 0 and hours >= STALL_H:
            lines.append(f"- ⚠️ **{hours:.1f}시간째 증가 0** — 쇼츠 피드에 노출되지 "
                         f"않고 있다는 뜻이다. 누적 숫자와 무관하게 나쁜 신호.")
    lines.append("")

    # 최근 증가 속도 (스냅샷이 쌓여야 의미 있음)
    moving = [v for v in public if v["delta"] and v["delta"][0] > 0]
    if moving:
        moving.sort(key=lambda v: v["delta"][0] / max(v["delta"][1], 0.5), reverse=True)
        lines.append("## 지금 조회수가 도는 영상 (스냅샷 대비 증가)")
        lines.append("")
        peak = moving[0]["delta"][0] / max(moving[0]["delta"][1], 0.5)
        for v in moving[:8]:
            rate = v["delta"][0] / max(v["delta"][1], 0.5)
            lines.append(f"- `{rate:5.1f}/h` {_bar(rate, peak)} +{v['delta'][0]:,}회 · "
                         f"{v['title'].split(' #')[0][:34]}")
        lines.append("")
    else:
        lines.append("## 증가 속도")
        lines.append("")
        lines.append("비교할 이전 스냅샷이 없습니다. 이번 실행이 첫 기록이며, "
                     "다음 실행부터 '지난번 대비 몇 회 늘었는지'가 표시됩니다.")
        lines.append("")

    # 시청지속률 (Analytics API가 열려 있을 때만)
    if isinstance(analytics, dict) and "error" in analytics:
        lines.append("## 시청지속률 · 유입경로")
        lines.append("")
        lines.append("Analytics API를 읽지 못했습니다. 조회수 기반 분석만 위에 반영돼 있습니다.")
        lines.append("")
        lines.append("```")
        lines.append(analytics["error"])
        lines.append("```")
        lines.append("")
    elif analytics:
        lines.append("## 시청지속률 (Analytics)")
        lines.append("")
        lines.append("| 조회 | 평균시청 | 지속률 | 구독증가 | 제목 |")
        lines.append("|----:|--------:|------:|--------:|------|")
        by_views = sorted(analytics.items(), key=lambda kv: kv[1].get("views", 0), reverse=True)
        titles = {v["video_id"]: v["title"] for v in videos}
        for vid, m in by_views[:20]:
            lines.append(
                f"| {m.get('views', 0):,} | {m.get('averageViewDuration', 0)}s | "
                f"{m.get('averageViewPercentage', 0):.1f}% | "
                f"{m.get('subscribersGained', 0)} | "
                f"{titles.get(vid, vid).split(' #')[0][:34]} |"
            )
        lines.append("")
        pcts = [m.get("averageViewPercentage", 0) for m in analytics.values()]
        if pcts:
            avg = sum(pcts) / len(pcts)
            lines.append(f"채널 평균 시청지속률 **{avg:.1f}%**")
            lines.append("")

    # 업로드별 유입경로 — 채널 도달이 끊긴 건지 소재가 나쁜 건지 가르는 지표.
    # 조회수의 95%가 SHORTS 피드라, 피드 배포량이 곧 성적이다.
    if tbv and "error" not in tbv:
        recent = sorted((v for v in videos if v["privacy"] == "public"),
                        key=lambda v: v["published_at"], reverse=True)[:10]
        rows = [(v, tbv.get(v["video_id"], {})) for v in recent]
        if any(src for _, src in rows):
            lines.append("## 업로드별 유입경로 (최근 10편)")
            lines.append("")
            lines.append("| 게시 | 총조회 | SHORTS | 비중 | 검색 | 제목 |")
            lines.append("|------|------:|-------:|-----:|-----:|------|")
            for v, src in rows:
                tot = sum(src.values())
                sh = src.get("SHORTS", 0)
                se = src.get("YT_SEARCH", 0)
                pct = f"{sh / tot * 100:.0f}%" if tot else "-"
                pub = _parse_rfc3339(v["published_at"]).astimezone(KST)
                lines.append(
                    f"| {pub.strftime('%m-%d %H:%M')} | {tot:,} | {sh:,} | {pct} | "
                    f"{se:,} | {v['title'].split(' #')[0][:30]} |"
                )
            lines.append("")
            lines.append("SHORTS 비중은 그대로인데 절대량만 줄면 피드 배포량이 준 것이고, "
                         "비중 자체가 무너지면 그 영상이 피드에서 빠진 것이다.")
            lines.append("")

    if traffic and "error" not in traffic:
        lines.append("## 유입경로")
        lines.append("")
        tot = sum(traffic.values()) or 1
        for src, val in sorted(traffic.items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"- {src}: {val:,}회 ({val / tot * 100:.1f}%)")
        lines.append("")

    # 채널 전체 일별 추이. 영상 하나하나의 성적으로는 '이 소재가 나빴나'까지만
    # 보이고, 채널 노출 자체가 줄고 있는지는 안 보인다.
    if daily and "error" not in daily[0]:
        rows = daily[-21:]
        peak = max((r.get("views", 0) for r in rows), default=0) or 1
        lines.append("## 채널 일별 조회수 (최근 3주)")
        lines.append("")
        for r in rows:
            v = r.get("views", 0)
            sub = r.get("subscribersGained", 0)
            lines.append(f"- {r.get('day', '')[5:]} {v:>6,}회 "
                         f"{_bar(v, peak)} 구독 {sub:+d}")
        half = len(rows) // 2
        if half:
            old_avg = sum(r.get("views", 0) for r in rows[:half]) / half
            new_avg = sum(r.get("views", 0) for r in rows[half:]) / (len(rows) - half)
            lines.append("")
            lines.append(f"앞 {half}일 평균 {old_avg:,.0f}회 → 뒤 {len(rows)-half}일 평균 "
                         f"{new_avg:,.0f}회 ({(new_avg/old_avg-1)*100:+.0f}%)")
        lines.append("")
    elif daily:
        lines.append(f"채널 일별 조회수 조회 실패: {daily[0]['error'][:120]}")
        lines.append("")

    # 업로드 시각 / 길이가 성적과 관계있는지
    lines.append("## 패턴")
    lines.append("")
    # 시기를 통제하지 않으면 시각대가 아니라 시기를 비교하게 된다.
    # 하루당 조회수는 오래된 영상일수록 분모(경과일)가 커져 낮게 나온다.
    # 2026-09-16 실측: 10시대 20개의 중앙값이 3.8인데 그 안에 176.7짜리가
    # 들어 있었다. 19개가 2025년 영상이라 생긴 착시다. 길이 비교에서 이미
    # 한 번 고쳤던 실수가 여기 그대로 남아 있었고, 게시 시각을 08:40으로
    # 옮긴 근거가 바로 이 수치였다.
    recent = [v for v in public if v["age_hours"] <= HOUR_WINDOW_DAYS * 24]
    by_hour: dict[int, list[float]] = {}
    for v in recent:
        hour = _parse_rfc3339(v["published_at"]).astimezone(KST).hour
        by_hour.setdefault(hour, []).append(v["views_per_day"])
    lines.append(f"게시 시각대 비교 대상: 최근 {HOUR_WINDOW_DAYS}일 영상 "
                 f"{len(recent)}개 (전체 {len(public)}개 중)")
    lines.append("")
    if len(by_hour) > 1:
        lines.append("게시 시각대별 하루당 조회수 중앙값:")
        for hour in sorted(by_hour):
            vals = sorted(by_hour[hour])
            mark = "" if len(vals) >= MIN_HOUR_N else "  (표본 부족, 판단 금지)"
            lines.append(f"- {hour:02d}시대 ({len(vals)}개): "
                         f"{vals[len(vals) // 2]:.1f}{mark}")
        solid = [h for h, v in by_hour.items() if len(v) >= MIN_HOUR_N]
        # 버킷 안을 보여 준다. 시기를 45일로 좁혀도 한 시각대에 잘 터진 주가
        # 몰려 있으면 시각이 아니라 그 주를 보는 것이 된다. 날짜가 흩어져
        # 있는지 눈으로 확인할 수 있어야 게시 시각을 바꿀지 정할 수 있다.
        if solid:
            lines.append("")
            lines.append(f"표본 {MIN_HOUR_N}개 이상 시각대의 내역 (시기 쏠림 확인용):")
            for hour in sorted(solid):
                rows = sorted((v for v in recent
                               if _parse_rfc3339(v["published_at"]).astimezone(KST).hour == hour),
                              key=lambda v: v["published_at"])
                inner = " · ".join(
                    f"{v['published_kst'][5:10]} {v['views']:,}({v['views_per_day']:.0f}/d)"
                    for v in rows)
                lines.append(f"- {hour:02d}시대: {inner}")
        if not solid:
            lines.append("")
            lines.append(f"어느 시각대도 표본 {MIN_HOUR_N}개를 못 채웠다. "
                         "게시 시각은 이 데이터로 정하지 말 것.")
        lines.append("")
    # 길이 ↔ 성적. 예전에는 150개 전체를 '50초 초과/이하'로 갈라 하루당
    # 조회수를 비교했는데, 2025년 영상과 2026년 영상이 섞여 있어 길이가
    # 아니라 시기를 비교하고 있었다. 그래서 "50초 초과가 2.3배 낫다"는
    # 반대 방향의 결론이 나왔다. 지속률이 잡히는 영상(최근 구간)으로만,
    # 그리고 지속률 기준으로 본다.
    # analytics dict에는 지속률이 안 잡힌 영상도 들어 있다. 그 영상의
    # averageViewPercentage는 0이라, 그냥 넣으면 중앙값이 0%로 깔리고
    # 상관계수도 0 더미에 끌려간다(첫 판에서 실제로 그렇게 나왔다).
    # 값이 실제로 있는 것만 쓴다.
    def _ret(v):
        return (analytics.get(v["video_id"]) or {}).get("averageViewPercentage", 0) or 0

    def _watched(v):
        """평균 시청시간(초). 지속률(%)은 길이로 나눈 값이라 길이와 구조적으로
        얽힌다. 절대 시청시간을 함께 봐야 '짧아서 %가 높은 것'과 '실제로 오래
        본 것'이 구분된다."""
        return (analytics.get(v["video_id"]) or {}).get("averageViewDuration", 0) or 0

    scored = [v for v in public if v.get("duration_s") and _ret(v) > 0]
    if len(scored) >= 6:

        lines.append(f"길이별 시청지속률 중앙값 (지속률이 잡힌 {len(scored)}개):")
        # 40초 경계를 넣은 이유: 길이 실험(SCRIPT_LEN_MODE=short)이 31~37초를
        # 노린다. '50초 미만' 한 칸에 두면 45~49초 영상과 섞여 실험 결과가
        # 보이지 않는다.
        for lo, hi, label in ((0, 40, "40초 미만"), (40, 50, "40~50초"),
                              (50, 60, "50~60초"), (60, 10 ** 6, "60초 초과")):
            g = [v for v in scored if lo <= v["duration_s"] < hi]
            if not g:
                continue
            rets = sorted(_ret(v) for v in g)
            vws = sorted(v["views"] for v in g)
            durs = sorted(_watched(v) for v in g)
            lines.append(f"- {label} ({len(g)}개): 지속률 {rets[len(rets) // 2]:.1f}% · "
                         f"시청시간 중앙 {durs[len(durs) // 2]:.0f}초 · "
                         f"조회수 중앙 {vws[len(vws) // 2]:,}")
        lines.append("")
        lines.append(f"상관계수: 길이↔지속률 {_corr([v['duration_s'] for v in scored], [_ret(v) for v in scored]):+.2f} · "
                     f"지속률↔조회수 {_corr([_ret(v) for v in scored], [v['views'] for v in scored]):+.2f} · "
                     f"길이↔조회수 {_corr([v['duration_s'] for v in scored], [v['views'] for v in scored]):+.2f}")
        lines.append(f"           길이↔시청시간 {_corr([v['duration_s'] for v in scored], [_watched(v) for v in scored]):+.2f} · "
                     f"시청시간↔조회수 {_corr([_watched(v) for v in scored], [v['views'] for v in scored]):+.2f}")
        lines.append("")
        lines.append("이 상관계수로 제작 방향을 바꾸기 전에: 표본 20개였던 "
                     "2026-09-13에는 길이↔지속률이 -0.52, 지속률↔조회수가 +0.68이었다. "
                     "표본 38개가 된 2026-09-16에는 각각 +0.58, +0.07로 뒤집혔다. "
                     "이 표본 크기에서는 부호가 안 굳는다. 방향을 정하려면 "
                     "관측이 아니라 통제된 실험이 필요하다.")
        durs = sorted(v["duration_s"] for v in scored)
        lines.append(f"(표본 {len(scored)}개라 방향만 본다. 관측된 길이는 "
                     f"{durs[0]}~{durs[-1]}초뿐이므로 그 밖은 말할 수 없다.)")
        lines.append("")

    # ── 길이 실험 코호트 (SCRIPT_LEN_MODE) ──────────────────────
    # 날짜로 코호트를 가르지 않는다. 크론이 하루 실패하거나 수동 재업로드가
    # 한 번 끼면 경계가 무너진다. 업로드 시점에 topic_history.json에 박아 둔
    # len_mode를 그대로 읽는다.
    modes = _len_modes()
    if modes:
        tagged = [v for v in scored if modes.get(v["video_id"])]
        by_mode = {}
        for v in tagged:
            by_mode.setdefault(modes[v["video_id"]], []).append(v)
        if len(by_mode) >= 2 or (tagged and len(tagged) >= 3):
            lines.append("길이 실험 (대본 길이 모드별):")
            for mode in sorted(by_mode):
                g = by_mode[mode]
                rets = sorted(_ret(v) for v in g)
                vws = sorted(v["views"] for v in g)
                durs = sorted(v["duration_s"] for v in g)
                lines.append(
                    f"- {mode} ({len(g)}개, {durs[0]}~{durs[-1]}초): "
                    f"지속률 중앙 {rets[len(rets) // 2]:.1f}% · "
                    f"조회수 중앙 {vws[len(vws) // 2]:,}")
            if len(by_mode) >= 2 and min(len(g) for g in by_mode.values()) < 7:
                lines.append("(한쪽 표본이 7개 미만이다. 아직 판단하지 말 것.)")
            lines.append("")

    # ── 소재 비교 (시기 통제) ────────────────────────────────
    grown = [v for v in public if v["age_hours"] >= TOPIC_MIN_AGE_DAYS * 24]
    if len(grown) >= MIN_TOPIC_N * 2:
        lines.append(f"소재별 조회수 중앙값 (게시 {TOPIC_MIN_AGE_DAYS}일 이상 지난 "
                     f"{len(grown)}개, 절대 조회수):")
        for label, g in _topic_groups(grown):
            if not g:
                continue
            mark = "" if len(g) >= MIN_TOPIC_N else "  (표본 부족, 판단 금지)"
            lines.append(f"- {label} ({len(g)}개): {_med([v['views'] for v in g]):,}{mark}")
        lines.append("")
        with_num = [v for v in grown if _NUM_RE.search(v["title"])]
        without = [v for v in grown if not _NUM_RE.search(v["title"])]
        if len(with_num) >= MIN_TOPIC_N and len(without) >= MIN_TOPIC_N:
            lines.append(f"제목에 단위 붙은 수치 있음 ({len(with_num)}개): "
                         f"{_med([v['views'] for v in with_num]):,}")
            lines.append(f"제목에 수치 없음 ({len(without)}개): "
                         f"{_med([v['views'] for v in without]):,}")
            lines.append("")

    # ── 기사 나이 ↔ 성적 (계측 코호트) ──────────────────────
    meta = _pick_meta()
    judged = [v for v in public
              if v["video_id"] in meta and v["age_hours"] >= MIN_AGE_H_FOR_VERDICT]
    lines.append(f"기사 나이 분석용 표본: {len(judged)}편 / 목표 {PICK_TARGET_N}편"
                 + (f" (앞으로 {PICK_TARGET_N - len(judged)}편)"
                    if len(judged) < PICK_TARGET_N else "  ← 목표 도달"))
    lines.append("")
    if judged:
        for v in sorted(judged, key=lambda v: v["published_at"]):
            m = meta[v["video_id"]]
            lines.append(f"- {v['published_kst'][5:10]} {v['views']:>5,}회 · "
                         f"기사 {float(m.get('age_days') or 0):.1f}일 전 · "
                         f"소재 {m.get('topic_score', '-')} + 신선도 "
                         f"{m.get('recency_score', '-')} · {m.get('source', '')}")
        lines.append("")
    if len(judged) >= PICK_TARGET_N:
        ages = [float(meta[v["video_id"]].get("age_days") or 0) for v in judged]
        vws = [v["views"] for v in judged]
        tsc = [meta[v["video_id"]].get("topic_score") or 0 for v in judged]
        lines.append(f"상관계수: 기사나이↔조회수 {_corr(ages, vws):+.2f} · "
                     f"소재점수↔조회수 {_corr(tsc, vws):+.2f}")
        lines.append(f"({PICK_TARGET_N}편은 방향을 정하기엔 적다. 2026-09-13에 표본 "
                     "20개로 정한 길이 방향이 38개에서 부호가 뒤집혔다. "
                     "여기서는 신호가 있는지만 보고, 바꾸려면 더 쌓을 것.)")
        lines.append("")


    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="Analytics 조회 구간(일)")
    ap.add_argument("--no-save", action="store_true", help="스냅샷 저장 생략")
    args = ap.parse_args()

    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    channel, videos = fetch_videos(youtube)
    print(f"[analyze] 영상 {len(videos)}개 수집", file=sys.stderr)

    end = datetime.now(KST).date()
    start = end - timedelta(days=args.days)
    ids = [v["video_id"] for v in videos if v["privacy"] == "public"]
    analytics = fetch_analytics(creds, ids, start.isoformat(), end.isoformat())
    if "error" in analytics:
        print(f"[analyze] Analytics 실패: {analytics['error']}", file=sys.stderr)
        traffic = {}
    else:
        traffic = fetch_traffic_sources(creds, start.isoformat(), end.isoformat())

    daily = fetch_daily(creds, start.isoformat(), end.isoformat())
    recent_ids = [v["video_id"] for v in videos if v["privacy"] == "public"][:10]
    tbv = fetch_traffic_by_video(creds, recent_ids, start.isoformat(), end.isoformat())
    if "error" in tbv:
        print(f"[analyze] 영상별 유입경로 실패: {tbv['error']}", file=sys.stderr)
    snapshots = load_snapshots()
    report = build_report(channel, videos, analytics, traffic, snapshots, args.days,
                          daily=daily, tbv=tbv)
    if not args.no_save:
        save_snapshot([v for v in videos if v["privacy"] == "public"], snapshots)

    print(report)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
