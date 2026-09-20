"""업로드된 영상을 삭제하거나 제목을 고친다.

파이프라인이 올린 뒤 제목만 바꾸고 싶은 경우가 잦은데, 그때마다 사람이
Studio에 들어가는 것은 낭비다. Actions에서 처리할 수 있게 한다.

사용(환경변수):
    ACTION=delete VIDEO_ID=xxxx
    ACTION=retitle VIDEO_ID=xxxx TITLE="새 제목"
    ACTION=show VIDEO_ID=xxxx          # 올라간 제목·설명 원문 확인
    ACTION=edit VIDEO_ID=xxxx TITLE="새 제목" DESCRIPTION="새 설명"
        (TITLE/DESCRIPTION은 준 것만 바뀐다)
    ACTION=striplinks [DRY_RUN=1]      # 전체 영상 설명에서 외부 링크 제거
    ACTION=approve VIDEO_ID=xxxx       # 검토 대기(비공개) 영상을 공개로 전환
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from googleapiclient.errors import HttpError

from src.uploader.youtube import get_youtube_service
from src.utils.text import strip_links


def _summary(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


def delete(video_id: str) -> int:
    yt = get_youtube_service()
    try:
        yt.videos().delete(id=video_id).execute()
    except HttpError as e:
        status = getattr(e.resp, "status", "?")
        if status == 404:
            _summary(f"이미 없는 영상입니다: `{video_id}`")
            return 0          # 지우려던 것이 없으면 목적은 달성된 상태다
        _summary(f"## 삭제 실패 (status={status})\n```\n"
                 f"{(e.content or b'').decode('utf-8', 'replace')[:300]}\n```")
        return 1
    _summary(f"영상을 삭제했습니다: `{video_id}`")
    return 0


def retitle(video_id: str, title: str) -> int:
    """제목만 바꾼다. snippet은 부분 수정이 안 되므로 기존 값을 읽어 유지한다."""
    yt = get_youtube_service()
    res = yt.videos().list(part="snippet", id=video_id).execute()
    items = res.get("items", [])
    if not items:
        _summary(f"## 영상을 찾을 수 없습니다: `{video_id}`")
        return 1
    sn = items[0]["snippet"]
    old = sn.get("title", "")
    sn["title"] = title[:100]
    try:
        yt.videos().update(part="snippet", body={"id": video_id, "snippet": sn}).execute()
    except HttpError as e:
        status = getattr(e.resp, "status", "?")
        _summary(f"## 제목 변경 실패 (status={status})\n```\n"
                 f"{(e.content or b'').decode('utf-8', 'replace')[:300]}\n```")
        return 1
    _summary("## 제목 변경 완료")
    _summary(f"- 전: {old}")
    _summary(f"- 후: {sn['title']}")
    _summary(f"- https://youtube.com/shorts/{video_id}")
    return 0


def edit(video_id: str, title: str = "", description: str = "") -> int:
    """제목·설명을 고친다. 준 항목만 바뀌고 나머지는 그대로 둔다.

    snippet은 부분 수정이 안 되므로 기존 값을 읽어 유지한다. 올라간 뒤
    문구가 잘못된 것을 발견했을 때, 조회수를 버리는 재업로드 대신 쓴다.
    """
    if not title and not description:
        _summary("TITLE과 DESCRIPTION이 모두 비어 있습니다.")
        return 1
    yt = get_youtube_service()
    res = yt.videos().list(part="snippet", id=video_id).execute()
    items = res.get("items", [])
    if not items:
        _summary(f"## 영상을 찾을 수 없습니다: `{video_id}`")
        return 1
    sn = items[0]["snippet"]
    changed = []
    if title:
        changed.append(("제목", sn.get("title", ""), title[:100]))
        sn["title"] = title[:100]
    if description:
        changed.append(("설명", "(생략)", "(아래 참조)"))
        sn["description"] = description[:5000]
    try:
        yt.videos().update(part="snippet", body={"id": video_id, "snippet": sn}).execute()
    except HttpError as e:
        status = getattr(e.resp, "status", "?")
        _summary(f"## 수정 실패 (status={status})\n```\n"
                 f"{(e.content or b'').decode('utf-8', 'replace')[:300]}\n```")
        return 1
    _summary("## 수정 완료")
    for what, before, after in changed:
        _summary(f"- {what} 전: {before}")
        _summary(f"- {what} 후: {after}")
    if description:
        _summary("\n### 새 설명\n```\n" + sn["description"] + "\n```")
    _summary(f"- https://youtube.com/shorts/{video_id}")
    return 0


def approve(video_id: str) -> int:
    """검토 대기 중인 비공개 영상을 공개로 바꾼다.

    REVIEW_MODE에서 파이프라인은 비공개로만 올린다. 사람이 대본을 읽고
    이 액션을 돌려야 공개된다. 편집 판단이 실제로 사람한테 있다는 뜻이고,
    그 기록이 커밋과 Actions 로그에 남는다.
    """
    yt = get_youtube_service()
    res = yt.videos().list(part="status,snippet", id=video_id).execute()
    items = res.get("items", [])
    if not items:
        _summary(f"## 영상을 찾을 수 없습니다: `{video_id}`")
        return 1
    st = items[0]["status"]
    title = items[0]["snippet"].get("title", "")
    if st.get("privacyStatus") == "public":
        _summary(f"## 이미 공개 상태입니다: `{video_id}` — {title}")
        return 0
    st["privacyStatus"] = "public"
    st.pop("publishAt", None)     # 예약이 걸려 있으면 즉시 공개와 충돌한다
    try:
        yt.videos().update(part="status", body={"id": video_id, "status": st}).execute()
    except HttpError as e:
        status = getattr(e.resp, "status", "?")
        _summary(f"## 공개 전환 실패 (status={status})\n```\n"
                 f"{(e.content or b'').decode('utf-8', 'replace')[:300]}\n```")
        return 1
    _summary(f"## 공개 완료 — {title}")
    _summary(f"- https://youtube.com/shorts/{video_id}")
    return 0


def show(video_id: str) -> int:
    """올라간 영상의 제목·설명을 그대로 출력한다.

    시청자가 내용을 문제 삼을 때, 영상에 실제로 어떤 문장이 실렸는지
    원문으로 확인해야 한다. 유튜브 페이지는 봇 차단이 걸려 밖에서
    긁을 수 없으므로 API로 읽는다.
    """
    yt = get_youtube_service()
    res = yt.videos().list(part="snippet,status", id=video_id).execute()
    items = res.get("items", [])
    if not items:
        _summary(f"## 영상을 찾을 수 없습니다: `{video_id}`")
        return 1
    sn = items[0]["snippet"]
    st = items[0].get("status", {})
    _summary(f"## {video_id}")
    _summary(f"- 게시: {sn.get('publishedAt', '')}")
    # 예약 공개가 실제로 걸렸는지는 status.publishAt 을 봐야 확인된다.
    # snippet.publishedAt 은 업로드 시각이라 예약을 안 보여 준다.
    _summary(f"- 공개 상태: {st.get('privacyStatus', '?')}")
    if st.get("publishAt"):
        _summary(f"- **예약 공개: {st['publishAt']}**")
    _summary(f"- 제목: {sn.get('title', '')}")
    _summary(f"- 태그: {', '.join(sn.get('tags', []) or [])}")
    _summary("\n### 설명 원문\n```\n" + (sn.get("description", "") or "") + "\n```")
    return 0


def striplinks(dry_run: bool = True) -> int:
    """채널 전체 영상 설명에서 외부 링크를 걷어낸다.

    2026-09-19 유튜브 조치: 09-19 업로드 설명에 있던 뉴시스 기사 URL이
    "스팸, 현혹 행위, 사기에 대한 정책" 위반으로 삭제됐다. 채널 경고는
    없었지만, 이 채널은 160편 전부가 같은 자리에 외부 기사 링크를 달고
    있다. 파이프라인만 고치면 앞으로 올라갈 것만 깨끗해지고, 이미 올라간
    것들은 링크 스팸 신호로 계속 남는다.

    설명의 나머지(제목·대본·출처 매체명·해시태그·고지)는 그대로 두고
    URL만 뺀다. DRY_RUN=1이면 바꿀 목록만 찍고 실제로 쓰지 않는다.
    """
    yt = get_youtube_service()
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    if not ch.get("items"):
        _summary("채널을 찾을 수 없습니다."); return 1
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    ids, page = [], None
    while True:
        pl = yt.playlistItems().list(part="contentDetails", playlistId=uploads,
                                     maxResults=50, pageToken=page).execute()
        ids += [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
        page = pl.get("nextPageToken")
        if not page:
            break

    targets = []
    for i in range(0, len(ids), 50):
        res = yt.videos().list(part="snippet", id=",".join(ids[i:i + 50])).execute()
        for it in res.get("items", []):
            sn = it["snippet"]
            before = sn.get("description", "") or ""
            after = strip_links(before)
            if after != before:
                targets.append((it["id"], sn, before, after))

    _summary(f"## 설명에 외부 링크가 있는 영상: {len(targets)}편 / 전체 {len(ids)}편")
    _summary("")
    if not targets:
        return 0
    if dry_run:
        _summary("DRY_RUN — 실제로 바꾸지 않았습니다. 바꾸려면 DRY_RUN=0.")
        _summary("")
        for vid_, sn, before, after in targets[:5]:
            _summary(f"### `{vid_}` {sn.get('title','')[:40]}")
            _summary("```\n- " + "\n- ".join(
                ln for ln in before.split("\n") if "http" in ln or "www." in ln) + "\n```")
        return 0

    ok = fail = 0
    for vid_, sn, _before, after in targets:
        sn["description"] = after[:5000]
        try:
            yt.videos().update(part="snippet",
                               body={"id": vid_, "snippet": sn}).execute()
            ok += 1
        except HttpError as e:
            fail += 1
            status = getattr(e.resp, "status", "?")
            _summary(f"- 실패 `{vid_}` (status={status})")
            if status in (403, 429):      # 할당량 소진이면 더 돌려도 의미 없다
                _summary("**할당량이 소진된 것 같습니다. 내일 이어서 돌리세요.**")
                break
    _summary(f"\n**{ok}편 수정 완료, {fail}편 실패.**")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    action = os.getenv("ACTION", "").strip()
    if action == "striplinks":
        raise SystemExit(striplinks(os.getenv("DRY_RUN", "1") != "0"))
    vid = os.getenv("VIDEO_ID", "").strip()
    if not vid:
        _summary("VIDEO_ID가 비어 있습니다."); raise SystemExit(1)
    if action == "show":
        raise SystemExit(show(vid))
    if action == "approve":
        raise SystemExit(approve(vid))
    if action == "edit":
        raise SystemExit(edit(vid, os.getenv("TITLE", "").strip(),
                             os.getenv("DESCRIPTION", "")))
    if action == "delete":
        raise SystemExit(delete(vid))
    if action == "retitle":
        t = os.getenv("TITLE", "").strip()
        if not t:
            _summary("TITLE이 비어 있습니다."); raise SystemExit(1)
        raise SystemExit(retitle(vid, t))
    _summary(f"알 수 없는 ACTION: {action!r} (show | edit | delete | retitle | striplinks | approve)")
    raise SystemExit(1)
