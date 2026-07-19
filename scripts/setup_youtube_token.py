"""YouTube OAuth 토큰 최초 발급 스크립트.

사용법:
  1. Google Cloud Console에서 OAuth 2.0 Client (Desktop) 생성
  2. client_secret.json을 config/ 에 저장
  3. 이 스크립트 실행: python scripts/setup_youtube_token.py
  4. 브라우저에서 Google 계정 인증
  5. 생성된 토큰을 GitHub Secrets에 등록

결과:
  - config/youtube_token.json 파일 생성 (로컬용)
  - 콘솔에 YOUTUBE_TOKEN_JSON 값 출력 (GitHub Secrets 등록용)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube"]
CLIENT_SECRET = Path(__file__).parent.parent / "config" / "client_secret.json"
TOKEN_PATH = Path(__file__).parent.parent / "config" / "youtube_token.json"


def main():
    if not CLIENT_SECRET.exists():
        print("config/client_secret.json 파일이 없습니다.")
        print()
        print("1. https://console.cloud.google.com/apis/credentials 에서")
        print("2. OAuth 2.0 Client ID 생성 (Desktop App)")
        print("3. JSON 다운로드 -> config/client_secret.json 으로 저장")
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0)

    # 로컬 파일 저장
    TOKEN_PATH.write_text(creds.to_json())
    print(f"\n[OK] 토큰 저장됨: {TOKEN_PATH}")

    # GitHub Secrets용 출력
    print("\n" + "=" * 60)
    print("아래 값을 GitHub Secrets 'YOUTUBE_TOKEN_JSON'에 등록하세요:")
    print("=" * 60)
    print(creds.to_json())
    print("=" * 60)


if __name__ == "__main__":
    main()
