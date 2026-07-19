"""YouTube Data API v3를 통한 영상 업로드."""

import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/youtube"]
TOKEN_PATH = Path(__file__).parent.parent.parent / "config" / "youtube_token.json"
CLIENT_SECRET_PATH = Path(__file__).parent.parent.parent / "config" / "client_secret.json"
# CI에서 refresh된 토큰을 여기에 쓰면 workflow가 이 파일을 읽어 secret을 갱신함
REFRESHED_TOKEN_OUT = os.getenv("YOUTUBE_TOKEN_REFRESH_OUT")


def _check_stored_token_age(token_path: Path):
    """파일에 저장된 토큰의 mtime 기반으로 refresh 토큰 노후화 경고.

    OAuth 'Testing' 모드에서는 refresh 토큰이 ~7일 미사용 시 만료된다.
    access_token의 expiry는 항상 1시간이라 신호가 안 되므로 파일 수정시간 사용.
    로컬 환경에서만 의미 있음 (CI는 env var에서 로드).
    """
    if not token_path.exists():
        return
    age_days = (datetime.utcnow().timestamp() - token_path.stat().st_mtime) / 86400
    if age_days > 5:
        print(
            f"[youtube] WARN: token file last refreshed {age_days:.1f} days ago. "
            "OAuth Testing 모드면 7일 후 만료됨 — 곧 재인증 필요할 수 있음."
        )


def get_youtube_service():
    """YouTube API 서비스 객체를 생성한다.

    토큰 소스 우선순위:
    1. YOUTUBE_TOKEN_JSON 환경변수 (GitHub Actions용, JSON 문자열)
    2. config/youtube_token.json 파일 (로컬 개발용)
    3. OAuth 브라우저 플로우 (최초 인증용)
    """
    creds = None
    token_json = os.getenv("YOUTUBE_TOKEN_JSON")

    if token_json:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)

    if not creds and TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                raise RuntimeError(
                    f"YouTube OAuth refresh 실패: {e}. "
                    "로컬에서 scripts/youtube_auth.py 재인증 후 "
                    "`gh secret set YOUTUBE_TOKEN_JSON --body \"$(cat config/youtube_token.json)\"` 실행."
                ) from e
            # 갱신된 토큰 저장 (로컬 파일 + CI용 drop 위치)
            refreshed = creds.to_json()
            if not token_json:
                TOKEN_PATH.write_text(refreshed)
            if REFRESHED_TOKEN_OUT:
                Path(REFRESHED_TOKEN_OUT).write_text(refreshed)
                print(f"[youtube] refreshed token dumped to {REFRESHED_TOKEN_OUT}")
        else:
            if os.getenv("CI"):
                raise RuntimeError(
                    "YouTube 토큰이 만료되었거나 없습니다. "
                    "로컬에서 인증 후 YOUTUBE_TOKEN_JSON 시크릿을 업데이트하세요."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
            TOKEN_PATH.write_text(creds.to_json())

    _check_stored_token_age(TOKEN_PATH)
    return build("youtube", "v3", credentials=creds)


def upload(
    video_path: Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category_id: str = "22",  # People & Blogs
) -> str:
    """YouTube에 영상을 업로드한다.

    Args:
        video_path: 업로드할 MP4 파일 경로
        title: 영상 제목
        description: 영상 설명
        tags: 태그 리스트
        category_id: YouTube 카테고리 ID

    Returns:
        업로드된 영상의 video ID
    """
    youtube = get_youtube_service()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    try:
        response = request.execute()
    except HttpError as e:
        # e.error_details / e.reason 은 API 버전마다 다르므로 status + content 로그
        status = getattr(e.resp, "status", "?")
        content = e.content.decode("utf-8", errors="replace") if e.content else ""
        print(f"[youtube] upload HttpError status={status}: {content[:500]}")
        raise
    video_id = response["id"]
    print(f"Upload complete: https://www.youtube.com/shorts/{video_id}")
    return video_id
