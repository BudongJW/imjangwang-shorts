"""업로드된 영상을 삭제하거나 제목을 고친다.

파이프라인이 올린 뒤 제목만 바꾸고 싶은 경우가 잦은데, 그때마다 사람이
Studio에 들어가는 것은 낭비다. Actions에서 처리할 수 있게 한다.

사용(환경변수):
    ACTION=delete VIDEO_ID=xxxx
    ACTION=retitle VIDEO_ID=xxxx TITLE="새 제목"
    ACTION=show VIDEO_ID=xxxx          # 올라간 제목·설명 원문 확인
    ACTION=edit VIDEO_ID=xxxx TITLE="새 제목" DESCRIPTION="새 설명"
        (TITLE/DESCRIPTION은 준 것만 바뀐다)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from googleapiclient.errors import HttpError

from src.uploader.youtube import get_youtube_service


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


def show(video_id: str) -> int:
    """올라간 영상의 제목·설명을 그대로 출력한다.

    시청자가 내용을 문제 삼을 때, 영상에 실제로 어떤 문장이 실렸는지
    원문으로 확인해야 한다. 유튜브 페이지는 봇 차단이 걸려 밖에서
    긁을 수 없으므로 API로 읽는다.
    """
    yt = get_youtube_service()
    res = yt.videos().list(part="snippet", id=video_id).execute()
    items = res.get("items", [])
    if not items:
        _summary(f"## 영상을 찾을 수 없습니다: `{video_id}`")
        return 1
    sn = items[0]["snippet"]
    _summary(f"## {video_id}")
    _summary(f"- 게시: {sn.get('publishedAt', '')}")
    _summary(f"- 제목: {sn.get('title', '')}")
    _summary(f"- 태그: {', '.join(sn.get('tags', []) or [])}")
    _summary("\n### 설명 원문\n```\n" + (sn.get("description", "") or "") + "\n```")
    return 0


if __name__ == "__main__":
    action = os.getenv("ACTION", "").strip()
    vid = os.getenv("VIDEO_ID", "").strip()
    if not vid:
        _summary("VIDEO_ID가 비어 있습니다."); raise SystemExit(1)
    if action == "show":
        raise SystemExit(show(vid))
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
    _summary(f"알 수 없는 ACTION: {action!r} (show | edit | delete | retitle)")
    raise SystemExit(1)
