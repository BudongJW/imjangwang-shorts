"""기사 비주얼 — 모바일 뷰 기사를 세로로 긴 이미지로 확보(composer가 스크롤 연출).

PC 뷰 캡처는 9:16에 담으면 글자만 크게 잘려 내용이 안 보인다. 그래서:
  1. Playwright '모바일 뷰포트'로 기사를 캡처 → 세로로 긴 모바일 기사 이미지
  2. 실패 시, 모바일 기사 스타일의 '세로로 긴 카드'를 직접 렌더
반환 이미지는 폭 1080 기준의 '세로로 긴' PNG. composer가 위→아래로 천천히 스크롤한다.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config.settings import VIDEO_DIR, ARTICLE_HIGHLIGHT, SHORTS_WIDTH
from src.editor.fonts import font_bold, font_regular
from src.utils.logger import setup_logger

log = setup_logger("article_capture")

CARD_W = SHORTS_WIDTH        # 프레임 폭을 꽉 채움(1080)
HL = (255, 232, 74)          # 형광펜 노랑
INK = (24, 26, 32)
GRAY = (110, 116, 128)
PAPER = (252, 252, 250)
RED = (208, 42, 42)

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1")
CAPTURE_MAX_H = 3400         # 캡처 세로 최대(너무 길면 스크롤이 빨라짐)


def _capture_with_playwright(url: str, highlight: str, out: Path) -> Path | None:
    """모바일 뷰포트로 기사 상단부(헤드라인+본문+사진)를 세로로 길게 캡처."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            ctx = browser.new_context(
                viewport={"width": 430, "height": 932},
                device_scale_factor=2, is_mobile=True, has_touch=True,
                user_agent=MOBILE_UA,
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1600)
            # 쿠키/구독/모달 배너 best-effort 제거
            page.evaluate(
                """() => {
                    const sels = ['[class*=cookie]','[class*=consent]','[id*=cookie]',
                        '[class*=paywall]','[class*=subscribe]','[class*=modal]',
                        '[class*=popup]','[class*=banner]','header[class*=fixed]','[class*=sticky]'];
                    sels.forEach(s => document.querySelectorAll(s).forEach(e => { try { e.remove(); } catch(_){} }));
                    try { document.body.style.overflow='visible'; } catch(_){}
                }"""
            )
            # 핵심 문장/헤드라인 형광펜 (모든 접근에 null 가드)
            if highlight:
                page.evaluate(
                    """(kw) => {
                        try {
                            const h = document.querySelector('h1');
                            if (h) { h.style.background='#ffe84a'; h.style.padding='2px 4px'; }
                            const key = (kw||'').slice(0, 12);
                            if (!key) return;
                            const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                            let n;
                            while ((n = w.nextNode())) {
                                if (n.nodeValue && n.nodeValue.includes(key) && n.parentElement) {
                                    n.parentElement.style.background='#fff59d';
                                    n.parentElement.scrollIntoView({block:'start'});
                                    break;
                                }
                            }
                            window.scrollTo(0, 0);
                        } catch(_){}
                    }""",
                    highlight,
                )
            page.wait_for_timeout(400)
            png = out.with_suffix(".png")
            page.screenshot(path=str(png), full_page=True)
            browser.close()
            # 폭 1080으로 리사이즈 + 세로 상한 크롭
            im = Image.open(png).convert("RGB")
            if im.width != CARD_W:
                nh = int(im.height * CARD_W / im.width)
                im = im.resize((CARD_W, nh), Image.LANCZOS)
            if im.height > CAPTURE_MAX_H:
                im = im.crop((0, 0, CARD_W, CAPTURE_MAX_H))
            im.save(png)
            log.info(f"  기사 모바일 캡처 성공 ({im.width}x{im.height})")
            return png
    except Exception as e:
        log.info(f"  Playwright 캡처 실패 → 카드 폴백: {e}")
        return None


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _render_news_card(title: str, source: str, published: str,
                      lead: str, highlight: str, out: Path) -> Path:
    """모바일 기사 스타일의 '세로로 긴' 카드를 렌더(캡처 실패 시)."""
    pad = 60
    inner = CARD_W - pad * 2
    f_src = ImageFont.truetype(font_bold(), 34)
    f_head = ImageFont.truetype(font_bold(), 58)
    f_meta = ImageFont.truetype(font_regular(), 30)
    f_body = ImageFont.truetype(font_regular(), 40)

    head_lines = _wrap(title, f_head, inner)
    # 본문은 넉넉히(스크롤 가치) — 문장 단위로 이어붙여 최대 18줄
    body_lines = _wrap(lead, f_body, inner)[:18] if lead else []

    y = pad
    y += 44 + 24                    # 언론사 바
    head_top = y
    y += len(head_lines) * 72 + 30  # 헤드라인
    y += 2 + 30                     # 구분선
    body_top = y
    y += len(body_lines) * 58 + pad
    height = max(y, 2400)           # 최소 세로(스크롤 확보)

    card = Image.new("RGB", (CARD_W, height), PAPER)
    d = ImageDraw.Draw(card)
    d.rectangle([0, 0, CARD_W, 10], fill=RED)

    yy = pad
    d.text((pad, yy), (source or "부동산 뉴스"), font=f_src, fill=RED)
    if published:
        pub = published[:16]
        d.text((CARD_W - pad - d.textlength(pub, font=f_meta), yy + 6), pub, font=f_meta, fill=GRAY)
    yy = head_top
    hl_key = (highlight or "")[:10]
    for i, ln in enumerate(head_lines):
        if i == 0 and ARTICLE_HIGHLIGHT:
            w = d.textlength(ln, font=f_head)
            d.rectangle([pad - 6, yy + 10, pad + w + 10, yy + 64], fill=HL)
        d.text((pad, yy), ln, font=f_head, fill=INK)
        yy += 72
    yy += 30
    d.line([pad, yy, CARD_W - pad, yy], fill=(222, 222, 218), width=2)
    yy = body_top
    for ln in body_lines:
        if ARTICLE_HIGHLIGHT and hl_key and hl_key in ln:
            w = d.textlength(ln, font=f_body)
            d.rectangle([pad - 4, yy + 6, pad + w + 6, yy + 52], fill=HL)
        d.text((pad, yy), ln, font=f_body, fill=(48, 50, 56))
        yy += 58

    png = out.with_suffix(".png")
    card.save(png)
    log.info(f"  기사 카드 렌더 ({CARD_W}x{height})")
    return png


def build_article_visual(art, highlight: str = "") -> Path:
    """모바일 기사 비주얼(세로로 긴 이미지)을 만든다. 실제 캡처 우선, 실패 시 카드."""
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    out = VIDEO_DIR / "article"
    shot = None
    if getattr(art, "url", ""):
        shot = _capture_with_playwright(art.url, highlight, out)
    if not shot:
        shot = _render_news_card(
            title=getattr(art, "title", ""),
            source=getattr(art, "source", ""),
            published=getattr(art, "published", ""),
            lead=getattr(art, "summary", ""),
            highlight=highlight,
            out=out,
        )
    # 스크롤 연출을 위해 폭 1080·최소 세로 2100 보장
    try:
        im = Image.open(shot).convert("RGB")
        changed = False
        if im.width != CARD_W:
            im = im.resize((CARD_W, int(im.height * CARD_W / im.width)), Image.LANCZOS)
            changed = True
        if im.height < 2100:
            canvas = Image.new("RGB", (CARD_W, 2100), PAPER)
            canvas.paste(im, (0, 0))
            im = canvas
            changed = True
        if changed:
            im.save(shot)
    except Exception:
        pass
    return shot
