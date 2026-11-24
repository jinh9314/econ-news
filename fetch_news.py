#!/usr/bin/env python3
"""경제뉴스 브리핑 생성기

여러 언론사의 경제 RSS에서 최신 기사 5개를 골라 index.html 한 장을 만듭니다.
표준 라이브러리만 쓰므로 따로 설치할 것이 없습니다. GitHub Actions가 매일 실행합니다.
"""
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import escape, unescape
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# 뉴스 출처(RSS). 주소가 바뀌었거나 안 열리면 여기만 고치면 됩니다.
FEEDS = {
    "연합뉴스": "https://www.yna.co.kr/rss/economy.xml",
    "한국경제": "https://www.hankyung.com/feed/economy",
    "매일경제": "https://www.mk.co.kr/rss/30100041/",
}

TOP_N = 5            # 보여줄 기사 수
SUMMARY_LIMIT = 110  # 요약 최대 글자 수
OUT = Path(__file__).with_name("index.html")


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def short_summary(text, limit=SUMMARY_LIMIT):
    """RSS 설명을 짧게 줄입니다. 가능하면 문장 끝에서 자릅니다."""
    text = clean(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    idx = max(cut.rfind("다."), cut.rfind(". "))
    if idx >= 40:
        end = idx + (2 if cut[idx:idx + 2] == "다." else 1)
        return cut[:end]
    return cut.rstrip() + "…"


def parse_time(value):
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception:
        return None


def fetch_feed(name, url):
    """RSS 하나를 읽어 기사 목록으로 돌려줍니다. 실패하면 빈 목록."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            root = ET.fromstring(resp.read())
    except Exception as e:
        print(f"[실패] {name}: {e}")
        return []
    items = []
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        items.append({
            "source": name,
            "title": title,
            "link": link,
            "summary": short_summary(it.findtext("description")),
            "ts": parse_time(it.findtext("pubDate") or ""),
        })
    print(f"[성공] {name}: {len(items)}건")
    return items


def pick_articles(feeds_items):
    """출처별로 번갈아 고르고, 같은 제목은 건너뜁니다."""
    picked, seen, i = [], set(), 0
    while len(picked) < TOP_N and any(i < len(items) for items in feeds_items):
        for items in feeds_items:
            if i < len(items) and len(picked) < TOP_N:
                a = items[i]
                key = re.sub(r"\W+", "", a["title"])
                if key not in seen:
                    seen.add(key)
                    picked.append(a)
        i += 1
    picked.sort(key=lambda a: a["ts"] or datetime.min.replace(tzinfo=KST), reverse=True)
    return picked


def story_html(a):
    when = a["ts"].strftime("%m.%d %H:%M") if a["ts"] else ""
    summary = f'<p class="sum">{escape(a["summary"])}</p>' if a["summary"] else ""
    link = escape(a["link"], quote=True)
    return f"""    <article class="story">
      <div class="meta"><span class="tag">{escape(a["source"])}</span><span class="when">{escape(when)}</span></div>
      <h2><a href="{link}" target="_blank" rel="noopener">{escape(a["title"])}</a></h2>
      {summary}
      <p class="src"><a href="{link}" target="_blank" rel="noopener">원문 보기</a></p>
    </article>"""


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>경제뉴스 브리핑</title>
<meta name="description" content="매일 아침 갱신되는 주요 금융·경제 뉴스 5가지">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+KR:wght@400;500;700&display=swap">
<style>
:root {
  color-scheme: light;
  --bg: #f1f4f7;
  --surface: #ffffff;
  --fg: #15202b;
  --muted: #566473;
  --line: #d9e0e7;
  --accent: #0d6b73;
  --font-body: "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg: #0e141a;
    --surface: #16202a;
    --fg: #e5ebf1;
    --muted: #93a1af;
    --line: #27333f;
    --accent: #5fc3c9;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: var(--font-body);
  font-size: 16px;
  line-height: 1.6;
  padding: 28px max(16px, env(safe-area-inset-right, 0px)) 40px max(16px, env(safe-area-inset-left, 0px));
  word-break: keep-all;
  overflow-wrap: break-word;
}
.wrap { max-width: 720px; margin: 0 auto; }
.head { padding: 0 4px 20px; }
.date { margin: 0 0 10px; font: 500 13px/1 var(--font-mono); letter-spacing: 0.04em; color: var(--muted); }
h1 { margin: 0 0 8px; font-size: 30px; line-height: 1.25; font-weight: 700; letter-spacing: -0.01em; text-wrap: balance; }
.lede { margin: 0; color: var(--muted); font-size: 15px; line-height: 1.7; max-width: 36em; }
.sheet { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 4px 22px; }
.story { padding: 22px 0; border-top: 1px solid var(--line); }
.story:first-child { border-top: 0; }
.meta { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.tag { font-size: 13px; font-weight: 700; letter-spacing: 0.06em; color: var(--accent); }
.when { font: 400 12px/1 var(--font-mono); color: var(--muted); white-space: nowrap; }
h2 { margin: 10px 0 8px; font-size: 19px; line-height: 1.45; font-weight: 700; letter-spacing: -0.01em; text-wrap: balance; }
h2 a { color: inherit; text-decoration: none; }
h2 a:hover { text-decoration: underline; text-underline-offset: 3px; }
.sum { margin: 0; font-size: 15px; line-height: 1.8; max-width: 38em; }
.src { margin: 12px 0 0; font-size: 13px; }
.src a { color: var(--accent); text-underline-offset: 3px; }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 2px; }
.foot { margin: 18px 4px 0; font-size: 13px; color: var(--muted); line-height: 1.7; }
@media (max-width: 520px) {
  h1 { font-size: 26px; }
  h2 { font-size: 17px; }
  .sheet { padding: 2px 16px; }
}
</style>
</head>
<body>
<div class="wrap">
  <header class="head">
    <p class="date">__DATE__</p>
    <h1>경제뉴스 브리핑</h1>
    <p class="lede">주요 언론사의 최신 경제 기사를 매일 아침 모아 보여줍니다.</p>
  </header>
  <main class="sheet">
__STORIES__
  </main>
  <p class="foot">각 언론사 RSS가 제공하는 제목과 짧은 설명을 모은 것입니다. 전체 내용은 원문 링크에서 확인하세요. 투자 조언이 아닙니다.</p>
</div>
</body>
</html>
"""


def build_page(articles, now):
    date = "갱신 " + now.strftime("%Y.%m.%d %a %H:%M").upper()
    stories = "\n".join(story_html(a) for a in articles)
    return TEMPLATE.replace("__DATE__", escape(date)).replace("__STORIES__", stories)


def main():
    feeds_items = [fetch_feed(name, url) for name, url in FEEDS.items()]
    articles = pick_articles(feeds_items)
    if not articles:
        # 기존 페이지를 지우지 않고 실패로 끝냅니다.
        print("가져온 기사가 없어 페이지를 만들지 않았습니다.")
        sys.exit(1)
    OUT.write_text(build_page(articles, datetime.now(KST)), encoding="utf-8")
    print(f"{OUT.name} 생성 완료 ({len(articles)}건)")


if __name__ == "__main__":
    main()
