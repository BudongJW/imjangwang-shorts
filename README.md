# 임장왕 쇼츠

부동산 뉴스 한 건을 골라 1분 안쪽 유튜브 쇼츠로 만들어 올리는 파이프라인이다.
매일 GitHub Actions에서 돈다.

## 흐름

```
뉴스 수집 → 기사 선정 → 대본 생성 → 사실 대조 → TTS → 배경·기사 화면 → 합성 → 업로드
collector/news   script_gen/generator          tts/    collector/images   editor/   uploader/
```

| 단계 | 파일 | 하는 일 |
|---|---|---|
| 수집·선정 | `src/collector/news.py` | 언론사 RSS와 구글뉴스에서 후보를 모아 소재 점수와 신선도로 고른다. 칼럼·분양 홍보·자동 생성 기사는 뺀다 |
| 대본 | `src/script_gen/generator.py` | Gemini로 대본을 쓰고 `script_defects`로 기사와 대조한다. 안 맞는 문장은 다시 쓰게 하거나 지운다 |
| 음성 | `src/tts/` | Azure Speech(키가 있을 때), 없으면 edge-tts |
| 화면 | `src/editor/` | 타이틀카드(썸네일), 상단 배너, 숫자 카드, 비교 카드, 자막, ffmpeg 합성 |
| 업로드 | `src/uploader/youtube.py` | 비공개로 올려 두고 승인하면 공개(`REVIEW_MODE`) |
| 진입점 | `src/orchestrator/main.py` | 위 단계를 잇고 토픽 기록(`output/topic_history.json`)을 남긴다 |
| 실험 | `src/experiments.py` | 날짜별로 실험 팔을 정하고 성과로 판정한다. [docs/improvement-loop.md](docs/improvement-loop.md) |
| 분석 | `scripts/analyze_performance.py` | 조회수·시청 유지·유입경로 리포트. 실험 판정도 여기서 한다 |

## 매일 도는 순서

1. **정기 제작** (`.github/workflows/daily-short-v2.yml`, 크론): 초안을 만들어 비공개로 올린다.
2. **성과 분석** (`.github/workflows/analytics.yml`): 스냅샷과 실험 상태를 master에 남긴다.
3. **검수**: 초안을 기사와 대조해, 고칠 데가 있으면 지정 대본(`output/pinned_plan.json`)을
   넣고 `run/daily-force-*` 브랜치로 다시 만든다. 초안이 틀린 이유는 검사 코드에 더한다.
4. **관리** (`.github/workflows/youtube-admin.yml`): `output/admin_request.json` + `run/admin-*`
   브랜치로 승인·삭제·예약을 한다.

브랜치 이름으로 실행을 가른다: `run/daily-*`(제작), 이름에 `force`(당일 중복 가드 끔),
`verify`(업로드 없이 렌더만), `short`(짧은 대본), `run/analytics-*`, `run/admin-*`, `run/auth-*`.

## 지정 대본

```json
{
  "use_on": "2026-11-10",
  "article": {"title": "…", "source": "매일경제", "url": "…", "summary": "기사 본문",
              "published": "2026-11-09"},
  "plan": {"headline": ["서울 빌라 전월세", "집계 이래 최소"], "hook_word": "최소",
           "highlight_sentence": "기사에서 강조할 문장", "caption_script": "대본",
           "youtube_title": "제목", "hashtags": ["빌라"], "face": "none",
           "keep_len_arm": true}
}
```

`use_on`이 오늘(KST)일 때만 쓰이고 한 번 쓰면 지워진다. `article.published`(기사 날짜)를
적으면 기사 나이와 신선도 점수가 토픽 기록에 남는다. 그 밖의 선택 항목: `speech_script`,
`layout`, `publish_at_kst`, `face`(파일명 또는 `none`).

## 고친 뒤 확인

```bash
pip install -r requirements.txt pytest ruff
```

```bash
python -m pytest
```

```bash
python scripts/check_corpus.py
```

```bash
python scripts/preview_render.py path/to/pinned_plan.json --frames
```

- `pytest`: 자막·컷 계획·타이틀카드·실험 배정·기사 선정·사실 대조 단위 테스트. 키와 네트워크 없이 돈다.
- `check_corpus.py`: 커밋 기록에 남은 지정 대본 전부에 사실 검사를 돌려, 사람이 검수한
  대본이 새로 걸리지 않는지 본다. 의도한 변화면 `--update`로 기준선을 갱신한다.
- `preview_render.py`: 키 없이 화면 구성만 렌더한다(무음, 폴백 배경). `--arm article_pos=late`
  처럼 실험 팔을 고정해 볼 수 있다. ffmpeg가 필요하다.

같은 검사가 `.github/workflows/tests.yml`에서 코드가 바뀔 때마다 돈다.

사실 검사를 더할 때는 `script_defects` 한 곳에 넣고, 그 초안의 실패 문장을
`tests/test_fact_checks.py`의 `BAD`에, 고쳐 쓴 문장을 `GOOD`에 한 줄씩 더한다.

## 설정

키와 선택 항목은 [.env.example](.env.example), 기본값과 그 근거는 `config/settings.py`에 있다.
