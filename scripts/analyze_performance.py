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


def _bar(value: float, peak: float, width: int = 18) -> str:
    if peak <= 0:
        return ""
    return "█" * max(1, round(value / peak * width)) if value > 0 else ""


def build_report(channel, videos, analytics, traffic, snapshots, days) -> str:
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
        verdict = "평균 이상" if latest["views_per_day"] >= peer_med_vpd else "평균 이하"
        lines.append(f"- 하루당 {latest['views_per_day']:.1f}회 vs 나머지 중앙값 "
                     f"{peer_med_vpd:.1f}회 → **{verdict}**")
        lines.append(f"- 전체 {len(public)}개 중 조회수 {rank}위")
    if latest["delta"]:
        gained, hours = latest["delta"]
        lines.append(f"- 지난 스냅샷 이후 {hours:.1f}시간 동안 +{gained:,}회")
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

    if traffic and "error" not in traffic:
        lines.append("## 유입경로")
        lines.append("")
        tot = sum(traffic.values()) or 1
        for src, val in sorted(traffic.items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"- {src}: {val:,}회 ({val / tot * 100:.1f}%)")
        lines.append("")

    # 업로드 시각 / 길이가 성적과 관계있는지
    lines.append("## 패턴")
    lines.append("")
    by_hour: dict[int, list[float]] = {}
    for v in public:
        hour = _parse_rfc3339(v["published_at"]).astimezone(KST).hour
        by_hour.setdefault(hour, []).append(v["views_per_day"])
    if len(by_hour) > 1:
        lines.append("게시 시각대별 하루당 조회수 중앙값:")
        for hour in sorted(by_hour):
            vals = sorted(by_hour[hour])
            lines.append(f"- {hour:02d}시대 ({len(vals)}개): {vals[len(vals) // 2]:.1f}")
        lines.append("")
    longer = [v for v in public if v["duration_s"] > 50]
    shorter = [v for v in public if v["duration_s"] <= 50]
    if longer and shorter:
        lm = sorted(v["views_per_day"] for v in longer)[len(longer) // 2]
        sm = sorted(v["views_per_day"] for v in shorter)[len(shorter) // 2]
        lines.append(f"길이별 하루당 조회수 중앙값: 50초 초과 {lm:.1f} ({len(longer)}개) "
                     f"vs 50초 이하 {sm:.1f} ({len(shorter)}개)")
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

    snapshots = load_snapshots()
    report = build_report(channel, videos, analytics, traffic, snapshots, args.days)
    if not args.no_save:
        save_snapshot([v for v in videos if v["privacy"] == "public"], snapshots)

    print(report)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
