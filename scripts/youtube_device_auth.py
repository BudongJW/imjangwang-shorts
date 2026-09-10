"""Actions에서 YouTube OAuth 토큰을 발급해 시크릿에 기록한다.

로컬 PC 없이 브라우저 한 번만 열면 되는 기기 흐름(device flow)을 쓴다.

저장소가 공개이므로 토큰은 로그·요약에 절대 출력하지 않는다.
발급된 토큰은 `gh secret set`으로 시크릿에만 쓴다.

사용:
    python scripts/youtube_device_auth.py request   # 코드 발급, 안내 출력
    python scripts/youtube_device_auth.py poll      # 승인 대기 후 시크릿 기록
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# 기기 흐름(device flow)이 허용하는 스코프만 쓴다. yt-analytics.readonly는
# 거부된다(invalid_scope: Invalid device flow scope, 2026-09-10 실측).
# 업로드에 필요한 것은 youtube 하나이므로 이것만 받는다.
# 성적 리포트용 Analytics 스코프는 기기 흐름으로 받을 수 없다 — 필요해지면
# 로컬 브라우저 흐름(scripts/setup_youtube_token.py)으로 따로 발급해야 한다.
SCOPES = "https://www.googleapis.com/auth/youtube"
STATE = "device_state.json"


def _post(url: str, data: dict) -> tuple[int, dict]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": "http_error", "raw": raw[:300]}


def _summary(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


def request() -> int:
    cid = os.environ["CID"]
    status, res = _post(DEVICE_URL, {"client_id": cid, "scope": SCOPES})
    if status != 200:
        err = res.get("error", "")
        _summary("## 기기 코드 발급 실패")
        if err in ("invalid_client", "unauthorized_client"):
            _summary("OAuth 클라이언트 유형이 맞지 않습니다.")
            _summary("Google Cloud Console에서 **'TV 및 입력 제한 기기'** 유형으로")
            _summary("새 클라이언트를 만들고 그 ID/보안비밀번호를 시크릿에 넣으세요.")
            _summary("데스크톱 앱 유형은 이 방식(기기 흐름)을 지원하지 않습니다.")
        _summary(f"```\n{err}: {res.get('error_description', res.get('raw',''))}\n```")
        return 1

    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(res, f)

    _summary("## YouTube 인증")
    _summary("")
    _summary(f"1. 이 주소를 여세요 → **{res['verification_url']}**")
    _summary(f"2. 코드를 입력하세요 → **`{res['user_code']}`**")
    _summary("3. 채널 계정으로 로그인하고 접근을 허용하세요")
    _summary("")
    _summary(f"유효시간 {int(res.get('expires_in', 1800)) // 60}분. "
             "허용하면 이 실행이 자동으로 시크릿을 갱신합니다.")
    _summary("")
    _summary("_토큰 값은 로그에 출력되지 않습니다._")
    return 0


def poll() -> int:
    cid, csec = os.environ["CID"], os.environ["CSEC"]
    with open(STATE, encoding="utf-8") as f:
        st = json.load(f)
    interval = max(int(st.get("interval", 5)), 5)
    deadline = time.time() + int(os.getenv("TIMEOUT_MIN", "10")) * 60

    while time.time() < deadline:
        status, res = _post(TOKEN_URL, {
            "client_id": cid, "client_secret": csec,
            "device_code": st["device_code"], "grant_type": GRANT,
        })
        if status == 200 and res.get("refresh_token"):
            token = {
                "token": res.get("access_token"),
                "refresh_token": res["refresh_token"],
                "token_uri": TOKEN_URL,
                "client_id": cid,
                "client_secret": csec,
                "scopes": SCOPES.split(),
            }
            return _write_secret(json.dumps(token))
        err = res.get("error", "")
        if err == "authorization_pending":
            time.sleep(interval)
            continue
        if err == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        if err == "access_denied":
            _summary("## 거부됨 — 사용자가 접근을 허용하지 않았습니다."); return 1
        if err == "expired_token":
            _summary("## 시간 초과 — 코드가 만료됐습니다. 다시 실행하세요."); return 1
        _summary(f"## 실패\n```\n{err}: {res.get('error_description','')}\n```")
        return 1

    _summary("## 시간 초과 — 대기 시간 안에 승인되지 않았습니다.")
    return 1


def _write_secret(value: str) -> int:
    """gh secret set으로 기록한다. 값은 stdin으로만 넘겨 프로세스 목록에도 안 남긴다."""
    repo = os.environ["REPO"]
    r = subprocess.run(
        ["gh", "secret", "set", "YOUTUBE_TOKEN_JSON", "--repo", repo, "--body-file", "-"],
        input=value, text=True, capture_output=True,
    )
    if r.returncode != 0:
        # stderr에 토큰이 섞일 일은 없지만 방어적으로 앞부분만
        _summary(f"## 시크릿 기록 실패\n```\n{r.stderr[:300]}\n```")
        _summary("PAT에 repo 권한이 있는지 확인하세요.")
        return 1
    _summary("## 완료")
    _summary("`YOUTUBE_TOKEN_JSON` 시크릿을 갱신했습니다.")
    _summary("이제 Daily Real-Estate Short를 실행하면 업로드됩니다.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "request"
    raise SystemExit(request() if cmd == "request" else poll())
