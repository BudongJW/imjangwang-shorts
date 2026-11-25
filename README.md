# 임장왕 Shorts

每天挑选一篇房地产新闻，做成 1 分钟以内的 YouTube Shorts 并上传的流水线。
每天在 GitHub Actions 上运行。

频道名“임장왕”意为“看房王”。视频内容、代码注释和 `docs/` 下的文档都是韩文。

## 流程

```
新闻采集 → 选稿 → 生成脚本 → 事实核对 → TTS → 背景·报道画面 → 合成 → 上传
collector/news   script_gen/generator          tts/    collector/images   editor/   uploader/
```

| 阶段 | 文件 | 做什么 |
|---|---|---|
| 采集·选稿 | `src/collector/news.py` | 从各媒体 RSS 和 Google 新闻收集候选，按题材分数和新鲜度挑选。专栏、楼盘推广稿、自动生成的稿件会被排除 |
| 脚本 | `src/script_gen/generator.py` | 用 Gemini 写脚本，再用 `script_defects` 与报道原文核对。对不上的句子会要求重写或直接删除 |
| 语音 | `src/tts/` | Azure Speech（有密钥时），没有则用 edge-tts |
| 画面 | `src/editor/` | 标题卡（缩略图）、顶部横幅、数字卡、对比卡、字幕、ffmpeg 合成。各元素如何出现和运动写在 `motion.py`（动效包）里 |
| 上传 | `src/uploader/youtube.py` | 先以私享（private）状态上传，批准后才公开（`REVIEW_MODE`） |
| 入口 | `src/orchestrator/main.py` | 串起以上各阶段，并留下选题记录（`output/topic_history.json`） |
| 实验 | `src/experiments.py` | 按日期分配实验分组，并根据表现判定。见 [docs/improvement-loop.md](docs/improvement-loop.md) |
| 分析 | `scripts/analyze_performance.py` | 播放量、观看留存、流量来源报告。实验判定也在这里做 |

## 每天的运行顺序

1. **定时制作**（`.github/workflows/daily-short-v2.yml`，cron）：生成草稿并以私享状态上传。
2. **表现分析**（`.github/workflows/analytics.yml`）：把快照和实验状态写回 master。
3. **审核**：把草稿与报道原文核对。有要改的地方，就放入指定脚本（`output/pinned_plan.json`），
   再用 `run/daily-force-*` 分支重新制作。草稿出错的原因要补进检查代码。
4. **管理**（`.github/workflows/youtube-admin.yml`）：通过 `output/admin_request.json` 和
   `run/admin-*` 分支进行批准、删除、定时发布。

用分支名区分运行方式：`run/daily-*`（制作），名字里带 `force`（关闭当日重复上传保护），
带 `verify`（不上传，只渲染），带 `short`（短脚本），以及 `run/analytics-*`、`run/admin-*`、`run/auth-*`。

## 指定脚本

```json
{
  "use_on": "2026-11-10",
  "article": {"title": "…", "source": "매일경제", "url": "…", "summary": "报道正文",
              "published": "2026-11-09"},
  "plan": {"headline": ["서울 빌라 전월세", "집계 이래 최소"], "hook_word": "최소",
           "highlight_sentence": "报道中要强调的句子", "caption_script": "脚本",
           "youtube_title": "标题", "hashtags": ["빌라"], "face": "none",
           "keep_len_arm": true}
}
```

上面的中文是占位说明，实际值都用韩文填写（`headline`、`hook_word` 等保留了韩文示例）。

只有 `use_on` 等于今天（KST）时才会使用，用过一次就会被删除。填写 `article.published`（报道日期）后，
报道发布至今的天数和新鲜度分数会记入选题记录。其他可选项：`speech_script`、`layout`、
`publish_at_kst`、`face`（文件名或 `none`）。

## 旁白停顿压缩

2026-11-26 加入。TTS 在每句话结尾都会停很久：11-25 的验证视频（Azure，51.6 秒）里，句与句之间
七处各停 1.43~1.51 秒，结尾还有 1.1 秒，静音合计 12.5 秒，占整条视频的四分之一。
现在只留下换气的时间，把静音的中间部分剪掉（`src/editor/composer.py` 的 `_pause_cuts`）。

| 位置 | 之前 | 现在 |
|---|---|---|
| 句与句之间 | 约 1.45 秒 | 0.25 秒 |
| 逗号处（不到 0.35 秒） | 0.25~0.3 秒 | 不变 |
| 结尾 | 约 1.1 秒 | 0.2 秒（播完立刻回到开头） |

- 不改语速，也不动说话的部分。剪掉多少，字幕、数字卡、名牌的时间就整体提前多少。
- 同一份脚本会变短：字数标准（310~350 字）不变，原来“48~55 秒”的视频现在大约 40~45 秒。
  平均观看百分比会因此看起来更高，不能和 11-25 之前的数字直接比较。
- 开关是 `PAUSE_TRIM`（默认开）。仓库变量 `PAUSE_TRIM=0` 即恢复 TTS 原样。
  要把画面和时长都恢复到 2026-11-10 版，需要同时设 `MOTION_FX=0` 和 `PAUSE_TRIM=0`。

## 画面动效（动效包）

2026-11-25 加入。之前的画面是同一个版式固定 50 秒，看起来像幻灯片。内容和布局不变，
只改变已有元素出现和运动的方式（`src/editor/motion.py`）。

| 元素 | 之前 | 现在 |
|---|---|---|
| 字幕 | 整句出现又消失 | 短语弹出，正在读的词依次变亮；数字的黄色保持不变 |
| 数字卡 | 直接出现又消失 | 弹跳放大后淡出；信息画面分组则从侧面滑入 |
| 对比卡 | 柱子一开始就是完整高度 | 柱子在 0.5 秒内从底部长出（数值文字从一开始就是脚本里的值） |
| 背景镜头 | 只有居中缩放 | 混入左右、上下平移；切镜头的瞬间短促推近再放开 |
| 报道画面 | 4~5 秒，硬切 | 3~3.5 秒，从下方滑入再滑出 |
| 标题横幅 | 强调词一开始就是黄色 | 0.45 秒时强调词变黄（标题从第一帧就在） |
| 报道画面的引文 | 整段一次出现 | 画面滑入后，强调句一行一行从左到右显现 |
| 名牌（2026-11-26 加入） | 没有 | 读到转述某人发言的句子时显示“발언｜姓名·头衔”，读到“△△에 따르면”时显示“출처｜来源”，读到提醒观众要确认的事项或主持人自己的解读时显示“임장왕 체크”“임장왕의 해석” |

- 开关是 `MOTION_FX`（默认开）。把仓库变量设为 `MOTION_FX=0`，不用改代码就能回到 2026-11-10 版画面。
  关闭时的渲染结果与改动前逐像素相同。
- 名牌（`src/editor/lower_third.py`）上的文字全部原样取自脚本，不新造句子；拿不准是谁说的就不显示。
  哪个名牌在什么时间出现，会记在 `build_notes.txt` 的 `이름표` 行里，审核时一并核对。
  只想关掉名牌时，把仓库变量设为 `NAME_TAGS=0`。
- 没有加入的：音效、进度条、数字滚动计数、更快的切镜头。数字滚动会让报道里没有的数字出现在画面上，
  所以不做。
- 动效包对所有实验分组在同一天同样生效，不会让分组之间的比较产生偏差。画面版本（`LAYOUT_VERSION`）
  随动效包升为 `2026-11-25`，加入名牌后升为 `2026-11-26`，分析报告按版本分开统计各时间段的观看留存。
- 参考依据、没有加入的理由、与实验的关系，见
  [docs/improvement-loop.md](docs/improvement-loop.md#화면-구성-판)。

## 修改后的检查

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

- `pytest`：字幕、镜头规划、标题卡、实验分配、选稿、事实核对、动效、名牌、停顿压缩的单元测试。不需要密钥和网络。
- `check_corpus.py`：对提交历史里留下的全部指定脚本跑一遍事实检查，确认人工审核过的脚本不会被新规则拦下。
  如果是有意的变化，用 `--update` 更新基线。
- `preview_render.py`：不需要密钥，只渲染画面构成（无声、后备背景）。可以像 `--arm article_pos=late`
  这样固定实验分组。加上 `--no-motion` 会得到关闭动效包的画面，方便和开启时并排比较。需要 ffmpeg。

同样的检查会在 `.github/workflows/tests.yml` 中随每次代码变更运行。

本地预览没有真实照片和语音（旁白是静音，所以停顿压缩在本地预览里不会生效）。要看真实渲染，推送一个名字里带 `verify` 的分支
（例如 `run/daily-verify-motion-1125`）：CI 只渲染、不上传，也不写选题记录，成片和抽帧作为
`verify-frames` 构件保留 7 天。暗色的后备背景上看不出来的问题（例如亮色照片上的字幕对比度）
要在这里确认。

新增事实检查时，统一加在 `script_defects` 一处，并把该草稿里没通过的句子加到
`tests/test_fact_checks.py` 的 `BAD`，把改写后的句子加到 `GOOD`，各一行。

## 设置

密钥和可选项见 [.env.example](.env.example)，默认值及其依据写在 `config/settings.py`。
