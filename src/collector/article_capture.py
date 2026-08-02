"""기사 비주얼 확보 — 개선안 ②(맨 텍스트 대신 실제 기사 캡처 + 형광펜 하이라이트).

우선순위:
  1. Playwright로 원문 페이지의 헤드라인 영역을 실제 캡처하고, 핵심 문장에
     노란 형광펜 하이라이트를 입힌다.
  2. 캡처 실패(차단/페이월/타임아웃) 시, 기사 메타데이터로 '뉴스 카드'를 직접 렌더한다.
둘 다 1080px 폭의 세로 배치용 PNG를 반환한다.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config.settings import VIDEO_DIR, ARTICLE_HIGHLIGHT, SHORTS_WIDTH, SHORTS_HEIGHT
from src.editor.fonts import font_bold, font_regular
from src.utils.logger import setup_logger

log = setup_logger("article_capture")

CARD_W = 1000
HL = (255, 232, 74)          # 형광펜 노랑
INK = (24, 26, 32)
GRAY = (110, 116, 128)
PAPER = (250, 250, 248)
RED = (208, 42, 42)


def _capture_with_playwright(url: str, highlight: str, out: Path) -> Path | None:
    """실제 기사 페이지 상단(헤드라인 영역)을 캡처. 실패 시 None."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 900, "height": 1200},
                                    device_scale_factor=2)
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)
            # 쿠키/구독 배너 best-effort 제거
            page.evaluate(
                """() => {
                    const kill = ['[class*=cookie]','[class*=consent]','[id*=cookie]',
                        '[class*=paywall]','[class*=subscribe]','[class*=modal]','[class*=popup]'];
                    kill.forEach(s => document.querySelectorAll(s).forEach(e => e.remove()));
                    document.body.style.overflow = 'visible';
                }"""
            )
            # 핵심 문장 하이라이트 + 헤드라인을 뷰포트 상단으로
            if highlight:
                page.evaluate(
                    """(kw) => {
                        const h = document.querySelector('h1');
                        if (h) { h.style.background='#ffe84a'; h.style.padding='4px 6px';
                                 h.scrollIntoView({block:'center'}); }
                        const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                        let n; const key = kw.slice(0, 12);
                        while ((n = walk.nextNode())) {
                            if (n.nodeValue && n.nodeValue.includes(key) && n.parentElement) {
                                n.parentElement.style.background='#fff59d'; break;
                            }
                        }
                    }""",
                    highlight,
                )
            page.wait_for_timeout(400)
            png = out.with_suffix(".png")
            page.screenshot(path=str(png))
            browser.close()
            # 상단 60%만 크롭(헤드라인 위주)
            im = Image.open(png).convert("RGB")
            im = im.crop((0, 0, im.width, int(im.height * 0.62)))
            im.save(png)
            log.info("  기사 실제 캡처 성공")
            return png
    except Exception as e:
        log.info(f"  Playwright 캡처 실패 → 카드 폴백: {e}")
        return None


def _render_news_card(title: str, source: str, published: str,
                      lead: str, highlight: str, out: Path) -> Path:
    """기사 메타로 신문 클리핑 스타일 카드를 렌더(폴백/보조)."""
    f_head = ImageFont.truetype(font_bold(), 46)
    f_meta = ImageFont.truetype(font_regular(), 26)
    f_body = ImageFont.truetype(font_regular(), 30)

    pad = 48
    head_lines = _wrap(title, f_head, CARD_W - pad * 2)
    lead_lines = _wrap(lead, f_body, CARD_W - pad * 2)[:6] if lead else []

    h = pad + 34 + 18 + len(head_lines) * 58 + 20 + 40 + (len(lead_lines) * 44) + pad
    card = Image.new("RGB", (CARD_W, h), PAPER)
    d = ImageDraw.Draw(card)

    # 상단 언론사 바
    d.rectangle([0, 0, CARD_W, 8], fill=RED)
    y = pad
    d.text((pad, y), (source or "부동산 뉴스").upper(), font=f_meta, fill=RED)
    if published:
        pub = published[:16]
        w = d.textlength(pub, font=f_meta)
        d.text((CARD_W - pad - w, y), pub, font=f_meta, fill=GRAY)
    y += 34 + 18

    # 헤드라인 (첫 줄 형광펜)
    for i, ln in enumerate(head_lines):
        if i == 0 and ARTICLE_HIGHLIGHT:
            w = d.textlength(ln, font=f_head)
            d.rectangle([pad - 4, y + 8, pad + w + 8, y + 52], fill=HL)
        d.text((pad, y), ln, font=f_head, fill=INK)
        y += 58
    y += 20
    d.line([pad, y, CARD_W - pad, y], fill=(220, 220, 216), width=2)
    y += 20

    # 본문 발췌 (핵심 문장 형광펜)
    hl_key = (highlight or "")[:10]
    for ln in lead_lines:
        if ARTICLE_HIGHLIGHT and hl_key and hl_key in ln:
            w = d.textlength(ln, font=f_body)
            d.rectangle([pad - 2, y + 4, pad + w + 4, y + 40], fill=HL)
        d.text((pad, y), ln, font=f_body, fill=(50, 52, 58))
        y += 44

    png = out.with_suffix(".png")
    card.save(png)
    return png


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


def _fit_to_frame(card_path: Path) -> Path:
    """카드/스크린샷을 1080x1920 프레임 중앙에 '전체가 보이게' 담는다.

    카드는 가로 1000px의 별도 이미지라, 그대로 배경처럼 쓰면 합성기가 9:16로
    커버-크롭하며 과도하게 확대·잘린다. 어두운 캔버스에 fit 배치해 이를 막는다.
    """
    W, H = SHORTS_WIDTH, SHORTS_HEIGHT
    canvas = Image.new("RGB", (W, H), (16, 22, 38))
    try:
        card = Image.open(card_path).convert("RGB")
    except Exception:
        canvas.save(card_path)
        return card_path
    # 배너(상단)·자막(하단) 피해 안전영역 안에 맞춤. 켄번즈 줌 여유로 다소 작게.
    max_w, max_h = int(W * 0.86), int(H * 0.56)
    ratio = min(max_w / card.width, max_h / card.height)
    nw, nh = max(1, int(card.width * ratio)), max(1, int(card.height * ratio))
    card = card.resize((nw, nh), Image.LANCZOS)
    cx, cy = W // 2, int(H * 0.47)
    # 카드 뒤 옅은 그림자/테두리
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([cx - nw // 2 - 8, cy - nh // 2 - 8, cx + nw // 2 + 8, cy + nh // 2 + 8],
                        radius=18, fill=(8, 10, 16))
    canvas.paste(card, (cx - nw // 2, cy - nh // 2))
    canvas.save(card_path)
    return card_path


def build_article_visual(art, highlight: str = "") -> Path:
    """기사 비주얼을 1080x1920 프레임으로 만든다. 실제 캡처 우선, 실패 시 카드 렌더."""
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
    return _fit_to_frame(shot)
