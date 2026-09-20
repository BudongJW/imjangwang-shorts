"""기사 비주얼 — 모바일 뷰 기사를 세로로 긴 이미지로 확보(composer가 스크롤 연출).

PC 뷰 캡처는 9:16에 담으면 글자만 크게 잘려 내용이 안 보인다. 그래서:
  1. Playwright '모바일 뷰포트'로 기사를 캡처 → 세로로 긴 모바일 기사 이미지
  2. 실패 시, 모바일 기사 스타일의 '세로로 긴 카드'를 직접 렌더
반환 이미지는 폭 1080 기준의 '세로로 긴' PNG. composer가 위→아래로 천천히 스크롤한다.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config.settings import VIDEO_DIR, ARTICLE_HIGHLIGHT, SHORTS_WIDTH, SHORTS_HEIGHT
from src.editor.fonts import font_bold, font_regular
from src.utils.buildnotes import note
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


# 기사 캡처에서 걷어낼 것들. 2026-09-16 실측에서 뉴시스 기사 캡처에
# "발기부전 옛날! '이것' 한알이면 밤새3번", "70代 남성! 부부관계 매일
# '2시간' 비결이" 같은 광고가 본문 첫 문단 바로 뒤에 붙어 8초간 화면에
# 박혀 나갔다. 부동산 채널에 성인·의료 광고가 뜨는 것이라 수익화와 신뢰
# 양쪽에 직접 타격이다.
#
# 원인: 제거 목록에 광고 컨테이너가 아예 없었다. 뉴시스 기사에 iframe이
# 27개, 매일경제에 taboola·adshufflenative·adbox가 있다.
#
# class*=ad 처럼 넓게 잡으면 안 된다. "header"에도 'ad'가 들어 있어
# 헤드라인이 통째로 날아간다. 경계를 붙인 패턴만 쓴다.
_STRIP_JS = """() => {
  const sels = [
    // 스크립트·외부 프레임. 기사 본문은 iframe 안에 없다.
    'iframe','ins','script','noscript','object','embed',
    // 광고 컨테이너 (경계를 붙여 header/read/shadow 오탐을 피한다)
    '[id^=ad]','[id*=adpos]','[id*="ad-"]','[id*="ad_"]',
    '[class^=ad]','[class*=" ad"]','[class*=adbox]','[class*=adshuffle]',
    '[class*=advert]','[class*=adarea]','[class*=adwrap]',
    // 외부 광고·추천 네트워크
    '[class*=dable]','[id*=dable]','[class*=taboola]','[id*=taboola]',
    '[class*=outbrain]','[class*=powerlink]','[class*=sponsor]','[class*=promo]',
    // 추천·관련기사 블록 (본문이 아니라 링크 목록이라 읽을 게 없다)
    '[class*=recommend]','[id*=recommend]','[class*=related]','[id*=related]',
    '[class*=bannergroup]',
    // 매체 자체 UI 위젯. 광고는 아니지만 기사 카드 위에 겹쳐 내용을 가린다.
    // 2026-09-16 실측: 매일경제 캡처 우하단에 AI 챗봇 캐릭터가 말풍선과 함께
    // 표를 덮고 있었고, 상단에는 구글 검색 위젯과 AI 요약 버튼이 찍혔다.
    '[class*=ai_], [id*=ai_]','[class*=news_summary]','[class*=floating]',
    '[class*=mascot]','[class*=btn_top]','[class*=gotop]','[class*=app_down]',
    '[class*=share]','[class*=sns]','[class*=font_size]','[class*=tooltip]',
    // 기존 목록
    '[class*=cookie]','[class*=consent]','[id*=cookie]','[class*=paywall]',
    '[class*=subscribe]','[class*=modal]','[class*=popup]','[class*=layer]',
    '[class*=banner]','header[class*=fixed]','[class*=sticky]'
  ];
  sels.forEach(s => {
    try { document.querySelectorAll(s).forEach(e => { try { e.remove(); } catch(_){} }); }
    catch(_){}
  });
  // 클래스명이 무작위인 네트워크 주입 위젯은 위 선택자로 안 걸린다.
  // 본문에 나올 리 없는 광고 문구로 한 번 더 훑는다. 짧은 링크 묶음만
  // 지워 본문 문단은 건드리지 않는다.
  const AD_TEXT = /발기|불끈|한알|부부관계|정력|비아그라|탈모|다이어트 성공|주름이|시력 회복|당뇨 완치|무료 상담 신청|나만의 AI|AI 비서|구독하기|앱 다운로드/;
  document.querySelectorAll('div,ul,section,aside,p,a,span').forEach(e => {
    try {
      const t = (e.innerText || '');
      if (t.length < 300 && AD_TEXT.test(t)) e.remove();
    } catch(_){}
  });
  // 떠 있는 요소는 구조로 잡는다. 클래스명 기반 제거는 매체마다 새로
  // 뚫린다 — 2026-09-16에 매일경제 'AI 비서' 위젯이 선택자 두 벌을
  // 통과했다. 화면에 고정된 작은 요소는 기사 본문일 수 없다.
  document.querySelectorAll('body *').forEach(e => {
    try {
      const st = getComputedStyle(e);
      if (st.position !== 'fixed' && st.position !== 'sticky') return;
      const r = e.getBoundingClientRect();
      if (r.height < 500 && r.width < 500) e.remove();
    } catch(_){}
  });
  try { document.body.style.overflow = 'visible'; } catch(_){}
}"""


def _is_blank(im: Image.Image) -> bool:
    """이미지가 사실상 백지(균일한 흰 화면)인지 판별."""
    small = im.resize((48, max(1, int(48 * im.height / im.width)))).convert("L")
    px = list(small.getdata())
    if not px:
        return True
    white = sum(1 for p in px if p > 235) / len(px)
    mean = sum(px) / len(px)
    var = sum((p - mean) ** 2 for p in px) / len(px)
    return white > 0.9 or var < 70


# 언론사 사진을 걷어낸다. 광고 제거와는 다른 이유다 — 이쪽은 저작권이다.
#
# 뉴스 사진은 기사 본문과 별개의 사진저작물이고 보호가 두텁다. 저작권법
# 제28조의 인용은 "정당한 범위 + 공정한 관행"을 요구하는데, 해설의 근거로
# 필요한 것은 본문의 수치와 문장이지 사진이 아니다. 화면에 8초 띄우면서
# 굳이 사진까지 들고 갈 이유가 없다.
#
# 반대로 그래프·표는 남긴다. 한국부동산원·HUG 같은 기관 자료는 공공저작물
# 성격이 강하고, 무엇보다 대본 수치의 근거로 화면에 있을 이유가 있다.
# 2026-09-18 실측에서 전세가격 추이 그래프가 대본 수치와 맞아떨어졌다.
#
# 구분은 캡션으로 한다. 한국 기사의 보도사진은 거의 예외 없이 "[서울=뉴시스]
# … 기자", "사진 제공", "ⓒ" 같은 캡션을 달고 있다. 캡션이 없으면 건드리지
# 않는다 — 과하게 지워 카드가 백지가 되는 쪽이 더 나쁘다.
_STRIP_PHOTO_JS = """() => {
  const PRESS = /사진|뉴시스|연합뉴스|뉴스1|제공|기자|촬영|자료사진|ⓒ|©|게티|AP|AFP|로이터/;
  const KEEP  = /그래프|차트|추이|통계|지수|자료\\s*[:：]|출처\\s*[:：]|단위\\s*[:：]/;
  const out = [];
  document.querySelectorAll('img').forEach(img => {
    try {
      if (!img.isConnected) return;
      const r = img.getBoundingClientRect();
      const w = r.width || img.naturalWidth || 0;
      const h = r.height || img.naturalHeight || 0;
      if (w < 200 || h < 150) return;        // 아이콘·로고·목록 썸네일은 둔다

      // 캡션 위치는 매체마다 다르다. figure/figcaption인 곳도 있고,
      // 한국일보처럼 <div class=editor-img-box> 안의 형제 <div class=caption>
      // 인 곳도 있다. 클래스명을 맞히는 대신 조상을 세 단계 올라가며
      // 주변 텍스트를 모은다. 다른 이미지가 끼면 거기서 멈춘다.
      // 조상을 올라가며 캡션을 찾되, 텍스트가 캡션 길이를 넘으면 멈춘다.
      // 안 그러면 본문을 통째로 감싼 컨테이너까지 올라가 기사 텍스트가
      // 같이 지워진다. 실측에서 한국일보 본문 수치가 전부 사라졌다.
      let node = img;
      let text = ((img.alt || '') + ' ' + (img.title || '')).trim();
      for (let i = 0; i < 3 && node.parentElement; i++) {
        const par = node.parentElement;
        if (par.querySelectorAll('img').length > 1) break;
        const t = (par.innerText || '').replace(/\\s+/g, ' ').trim();
        if (t.length > 200) break;          // 여기부터는 본문이다
        node = par;
        if (t) text = t;
      }
      if (KEEP.test(text)) return;          // 그래프·표는 근거라서 남긴다
      // 출처 표시가 없는 이미지는 건드리지 않는다. 사이트 아이콘·로고까지
      // 지우다 화면을 통째로 비우는 쪽이 더 나쁘다(실측 167건 오삭제).
      if (!PRESS.test(text)) return;
      out.push(text.slice(0, 40));
      node.remove();
    } catch(_){}
  });
  return out;
}"""


# 클래스명으로는 못 잡는 매체 UI를 '텍스트 모양'으로 잡는다.
#
# 한국일보는 Tailwind 유틸리티 클래스를 쓴다. 실측으로 남은 것이
#   UL.space-y-16 max-sm:py-8 gtm-article-write "신지후 기자 hoo@... 구독"
# 인데, 광고·위젯 선택자에 걸릴 이름이 아예 없다. 보도사진 제거와 같은
# 문제다 — 클래스명을 맞히는 방식은 매체가 바뀌면 그냥 안 걸린다.
#
# 대신 '무엇이 쓰여 있는가'로 판단한다. 기자 바이라인·구독 버튼·공유
# 버튼은 텍스트 모양이 정해져 있고 짧다. 80자를 넘으면 본문으로 보고
# 건드리지 않는다.
_STRIP_WIDGET_JS = """() => {
  const HIT = [
    /\\S{1,8}\\s*기자\\s*\\S+@\\S+/,          // 신지후 기자 hoo@hankookilbo.com
    /^구독\\s*[+＋]?(하기)?$/,
    /^기자\\s*제보$/,
    /^(공유|스크랩|프린트|글씨\\s*크기|댓글|좋아요)$/,
    /^이미지\\s*확대보기$/,
    /^(카카오톡|페이스북|트위터|네이버|밴드)$/,
  ];
  const out = [];
  document.querySelectorAll('body *').forEach(e => {
    try {
      if (!e.isConnected) return;
      if (e.children.length > 6) return;
      const t = (e.innerText || '').replace(/\\s+/g, ' ').trim();
      if (!t || t.length > 80) return;        // 본문 문단은 건드리지 않는다
      if (!HIT.some(re => re.test(t))) return;
      // 같은 텍스트만 감싼 가장 바깥 요소까지 올라가 한 번에 지운다.
      // 텍스트가 달라지는 순간 멈추므로 본문을 삼킬 수 없다.
      let node = e, par = e.parentElement;
      for (let i = 0; i < 3 && par; i++) {
        const pt = (par.innerText || '').replace(/\\s+/g, ' ').trim();
        if (pt !== t) break;
        node = par; par = par.parentElement;
      }
      out.push(t.slice(0, 34));
      node.remove();
    } catch(_){}
  });
  return out;
}"""


# 제거 후 남은 의심 요소를 찍는다. 태그·class·id·텍스트 앞부분을 돌려주므로
# 다음 실행에서 정확히 무엇을 지워야 하는지 바로 보인다.
_LEFTOVER_JS = """() => {
  const SUS = /발기|불끈|한알|부부관계|AI 비서|나만의 AI|광고|추천|관련 ?기사|구독|앱 다운/;
  const out = [];
  document.querySelectorAll('body *').forEach(e => {
    try {
      if (e.children.length > 4) return;
      const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
      if (!t || t.length > 80 || !SUS.test(t)) return;
      out.push(e.tagName + '.' + (e.className || '').toString().slice(0, 40)
               + '#' + (e.id || '').slice(0, 24) + ' "' + t.slice(0, 30) + '"');
    } catch(_){}
  });
  return out.slice(0, 8);
}"""


def _trim_blank_bottom(im: Image.Image, keep: int = 24) -> Image.Image:
    """캡처 아래쪽의 내용 없는 영역을 잘라낸다.

    기사 캡처는 화면에서 위→아래로 스크롤되는데, 끝점이 (높이 - 화면높이)로
    고정돼 있다. 캡처 하단이 비어 있으면 그 8초의 뒷부분이 흰 화면이 된다
    (2026-09-16 검증 12초 프레임에서 아래 절반이 백지였다). 광고·추천 블록을
    걷어내면 페이지가 짧아져 이 여백이 더 커진다.

    행 단위로 아래에서 위로 올라가며, 그 행의 색이 거의 균일하면 비어 있는
    것으로 본다. 내용이 나오면 여유 몇 줄만 남기고 자른다.
    """
    g = im.convert("L")
    w, h = g.size
    step = 4          # 4픽셀마다 본다. 1픽셀씩 보면 느리고 얻는 게 없다.
    last = 0
    for y in range(h - 1, -1, -step):
        row = g.crop((0, y, w, y + 1)).getdata()
        lo, hi = min(row), max(row)
        if hi - lo > 12:        # 글자나 그림이 있으면 대비가 생긴다
            last = y
            break
    if last == 0:               # 전부 균일 → 판단 보류, 원본 그대로
        return im
    cut = min(h, last + keep)
    if h - cut < 40:            # 잘라 봐야 몇 줄 → 그대로 둔다
        return im
    log.info(f"  기사 캡처 하단 여백 {h - cut}px 제거 ({h} → {cut})")
    return im.crop((0, 0, w, cut))


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
            page.evaluate(_STRIP_JS)
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
            # 한 번 더 걷어낸다. 플로팅 위젯은 지연 삽입되는 것이 있어
            # 첫 제거 뒤에 다시 나타난다. 2026-09-16 실측: 매일경제 AI 비서
            # 캐릭터가 제거 후에도 캡처에 남아 표를 덮었다.
            page.evaluate(_STRIP_JS)
            # 보도사진 제거는 캡처 직전에 한 번만 한다. 지연 로딩되는
            # 이미지가 있어 너무 일찍 돌리면 캡션이 아직 안 붙어 있다.
            try:
                removed = page.evaluate(_STRIP_PHOTO_JS) or []
                if removed:
                    note(f"기사 캡처: 사진 {len(removed)}건 제거(저작권) — "
                         + " | ".join(removed[:4]))
                wid = page.evaluate(_STRIP_WIDGET_JS) or []
                if wid:
                    note(f"기사 캡처: 매체 위젯 {len(wid)}건 제거 — "
                         + " | ".join(wid[:4]))
            except Exception as e:
                note(f"기사 캡처: 보도사진 제거 실패(무시) {str(e)[:80]}")
            page.wait_for_timeout(200)
            # 무엇이 남았는지 남긴다. 클래스명 추측을 반복하지 않으려면
            # 실제로 남은 요소의 마크업을 봐야 한다.
            try:
                left = page.evaluate(_LEFTOVER_JS)
                if left:
                    note(f"기사 캡처 잔여 위젯 {len(left)}건: " + " | ".join(left[:4]))
            except Exception:
                pass
            png = out.with_suffix(".png")
            page.screenshot(path=str(png), full_page=True)
            browser.close()
            # 폭 1080으로 리사이즈 + 세로 상한 크롭
            im = Image.open(png).convert("RGB")
            if im.width != CARD_W:
                nh = int(im.height * CARD_W / im.width)
                im = im.resize((CARD_W, nh), Image.LANCZOS)
            im = _trim_blank_bottom(im)
            if im.height > CAPTURE_MAX_H:
                im = im.crop((0, 0, CARD_W, CAPTURE_MAX_H))
            # 백지/차단 페이지 검증 — 균일한 흰 화면이면 실패로 간주(→ 카드 폴백)
            if im.height < 2000 or _is_blank(im):
                log.info("  기사 캡처가 백지/짧음 → 카드 폴백")
                return None
            im.save(png)
            log.info(f"  기사 모바일 캡처 성공 ({im.width}x{im.height})")
            note(f"기사: Playwright 캡처 성공 {im.width}x{im.height}")
            return png
    except Exception as e:
        log.info(f"  Playwright 캡처 실패 → 카드 폴백: {e}")
        note(f"기사: Playwright 캡처 실패 → 카드 폴백 ({type(e).__name__}: {str(e)[:120]})")
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
    # 본문은 넉넉히(스크롤 가치) — 문장 단위로 이어붙여 최대 18줄.
    body_lines = _wrap(lead, f_body, inner)[:18] if lead and len(lead) >= 20 else []
    # 본문이 없으면 강조 문구를 큰 활자로 앉힌다. 예전에는 본문용 40px로
    # 한 줄만 찍어서, 프레임을 채운 카드의 70%가 빈 흰 바탕이 됐다
    # (2026-09-14 검증 프레임 12초 지점). 이 구간은 8초나 머문다.
    pull_lines: list[str] = []
    f_pull = ImageFont.truetype(font_bold(), 64)
    if not body_lines and highlight:
        pull_lines = _wrap(highlight, f_pull, inner)[:4]
    log.info(f"  기사 카드 본문: lead {len(lead or '')}자 → 본문 {len(body_lines)}줄"
             + (f", 강조 {len(pull_lines)}줄" if pull_lines else ""))
    note(f"기사 카드: lead {len(lead or '')}자 · 헤드라인 {len(head_lines)}줄 · "
         f"본문 {len(body_lines)}줄 · 강조 {len(pull_lines)}줄")

    y = pad
    y += 44 + 24                    # 언론사 바
    head_top = y
    y += len(head_lines) * 72 + 30  # 헤드라인
    y += 2 + 30                     # 구분선
    body_top = y
    y += len(body_lines) * 58 + len(pull_lines) * 86 + pad
    # 최소 높이를 프레임 높이(1920)로 잡는다. 900으로 두면 1080x900 카드가
    # 1080x1920 프레임에 패딩돼 위아래로 검은 띠가 절반 가까이 남는다
    # (2026-09-14 검증 프레임 12초 지점에서 실측). 프레임을 꽉 채우고,
    # 내용이 짧으면 위아래 여백을 나눠 가운데로 내린다 — 흰 여백이
    # 아래쪽에만 몰려 카드가 미완성처럼 보이던 것도 같이 해결된다.
    content_h = y
    height = max(content_h, SHORTS_HEIGHT)
    offset = max(0, (height - content_h) // 2)
    head_top += offset
    body_top += offset

    card = Image.new("RGB", (CARD_W, height), PAPER)
    d = ImageDraw.Draw(card)
    d.rectangle([0, 0, CARD_W, 10], fill=RED)

    yy = pad + offset
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
    for ln in pull_lines:
        w = d.textlength(ln, font=f_pull)
        if ARTICLE_HIGHLIGHT:
            d.rectangle([pad - 8, yy + 8, pad + w + 12, yy + 78], fill=HL)
        d.text((pad, yy), ln, font=f_pull, fill=INK)
        yy += 86

    png = out.with_suffix(".png")
    card.save(png)
    log.info(f"  기사 카드 렌더 ({CARD_W}x{height}, 내용 {content_h}px, 상하여백 {offset}px)")
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
    # 폭 1080 보장(세로 패딩은 하지 않음 — 흰 여백 스크롤 방지). 높이는 composer가 판단.
    try:
        im = Image.open(shot).convert("RGB")
        if im.width != CARD_W:
            im = im.resize((CARD_W, int(im.height * CARD_W / im.width)), Image.LANCZOS)
            im.save(shot)
    except Exception:
        pass
    return shot
