"""업로드된 영상을 삭제하거나 제목을 고친다.

파이프라인이 올린 뒤 제목만 바꾸고 싶은 경우가 잦은데, 그때마다 사람이
Studio에 들어가는 것은 낭비다. Actions에서 처리할 수 있게 한다.

사용(환경변수):
    ACTION=delete VIDEO_ID=xxxx
    ACTION=retitle VIDEO_ID=xxxx TITLE="새 제목"
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


if __name__ == "__main__":
    action = os.getenv("ACTION", "").strip()
    vid = os.getenv("VIDEO_ID", "").strip()
    if not vid:
        _summary("VIDEO_ID가 비어 있습니다."); raise SystemExit(1)
    if action == "delete":
        raise SystemExit(delete(vid))
    if action == "retitle":
        t = os.getenv("TITLE", "").strip()
        if not t:
            _summary("TITLE이 비어 있습니다."); raise SystemExit(1)
        raise SystemExit(retitle(vid, t))
    _summary(f"알 수 없는 ACTION: {action!r} (delete | retitle)")
    raise SystemExit(1)
