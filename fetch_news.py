#!/usr/bin/env python3
"""경제뉴스 브리핑 생성기

여러 언론사의 경제 RSS에서 기사를 모은 뒤, 동정·행사 기사는 빼고 금융경제 핵심어와
경제 용어가 많은 기사부터 5개를 골라 index.html 한 장을 만듭니다.
기사마다 헤드라인과 핵심 문장 하나를 보여주고, 그 안에 나온 경제 용어는 옆에 쉬운 풀이를 붙입니다.
용어 풀이는 같은 폴더의 glossary.json에서 읽습니다. 표준 라이브러리만 쓰고, GitHub Actions가 매일 실행합니다.
"""
import json
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
HERE = Path(__file__).parent

# 뉴스 출처(RSS). 한 출처에 주소를 여러 개 적으면 앞에서부터 열리는 첫 주소를 씁니다.
# 주소가 바뀌었거나 안 열리면 여기만 고치면 됩니다.
FEEDS = {
    "연합뉴스": ["https://www.yna.co.kr/rss/economy.xml"],
    "한국경제": ["https://www.hankyung.com/feed/economy"],
    "매일경제": [
        "https://www.mk.co.kr/rss/30100041/",
        "https://www.mk.co.kr/rss/30000001/",
    ],
}

TOP_N = 5             # 보여줄 기사 수
PER_FEED = 20         # 출처마다 살펴볼 최신 기사 수
MAX_PER_SOURCE = 4    # 한 출처에서 뽑을 최대 기사 수 (한 곳에 쏠리는 것만 막습니다)
SENTENCE_LIMIT = 150  # 핵심 문장 최대 글자 수
MAX_TERMS = 3         # 기사 하나에 붙일 용어 풀이 수
TERM_BONUS = 2        # 용어가 하나 있을 때마다 더해 주는 점수
OUT = HERE / "index.html"
GLOSSARY = HERE / "glossary.json"

# 핵심 단어: 제목에 있으면 +3, 요약에 있으면 +2. 더하거나 빼도 됩니다.
CORE = [
    "코스피", "코스닥", "증시", "환율", "기준금리", "금리", "물가", "성장률", "한국은행", "연준",
]
# 일반 단어: 제목에 있으면 +2, 요약에 있으면 +1.
BOOST = [
    "주가", "달러", "인플레이션", "금융위", "금감원", "재정", "예산", "세금", "관세", "무역",
    "수출", "수입", "경기", "고용", "실업", "부동산", "집값", "대출", "가계부채",
    "채권", "국채", "유가", "원유", "반도체", "삼성전자", "SK하이닉스", "엔비디아", "AI",
    "비트코인", "가상자산", "스테이블코인", "은행", "보험", "증권", "투자", "실적", "영업이익",
]
# 제목에 이 단어가 있으면 뽑지 않습니다.
SKIP = [
    "동정", "인사", "부고", "부음", "결혼", "별세", "포토", "사진", "화보", "행사", "기념",
    "창립", "개최", "참석", "간담회", "토크콘서트", "업무협약", "MOU", "캠페인", "기부",
    "봉사", "후원", "이벤트", "프로모션", "시상", "수상", "기증",
]


# ---------- 용어집 ----------

def load_glossary():
    """glossary.json을 읽어 (용어 목록, 검색어 색인)을 돌려줍니다. 없으면 용어 없이 만듭니다."""
    try:
        data = json.loads(GLOSSARY.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[용어집 없음] {e}")
        return [], []
    index = []
    for i, entry in enumerate(data):
        for key in entry.get("keys", []):
            key = key.strip()
            if len(key) >= 2:
                index.append((key, i))
    index.sort(key=lambda x: len(x[0]), reverse=True)  # 긴 검색어부터 찾습니다.
    return data, index


def find_spans(text, index):
    """text 안에서 용어집 검색어가 나온 자리를 (시작, 끝, 용어번호) 목록으로 돌려줍니다."""
    taken = [False] * len(text)
    spans = []
    for key, i in index:
        start = 0
        while True:
            p = text.find(key, start)
            if p < 0:
                break
            start = p + 1
            end = p + len(key)
            if any(taken[p:end]):
                continue
            if key.isascii():  # 영문 약자는 다른 단어의 일부면 건너뜁니다.
                before = text[p - 1] if p > 0 else ""
                after = text[end] if end < len(text) else ""
                if (before.isascii() and before.isalnum()) or (after.isascii() and after.isalnum()):
                    continue
            for j in range(p, end):
                taken[j] = True
            spans.append((p, end, i))
    spans.sort()
    return spans


def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def cut_sentence(s, limit=SENTENCE_LIMIT):
    if len(s) <= limit:
        return s
    return s[:limit].rstrip(" ,") + "…"


def enrich(a, data, index):
    """기사에 핵심 문장과 설명할 용어를 붙입니다.

    용어가 가장 많이 나오는 문장을 핵심 문장으로 고르고(같으면 앞쪽 문장),
    제목과 그 문장에 나온 용어 중 최대 MAX_TERMS개만 풀이합니다.
    """
    a["term_ids"], a["spans_title"], a["spans_sent"], a["sentence"] = [], [], [], ""
    sentences = split_sentences(a["desc"])
    best, best_hits = "", -1
    for s in sentences:
        hits = len({i for _, _, i in find_spans(cut_sentence(s), index)}) if index else 0
        if hits > best_hits:
            best, best_hits = s, hits
    a["sentence"] = cut_sentence(best)
    if not index:
        return
    title_spans = find_spans(a["title"], index)
    sent_spans = find_spans(a["sentence"], index)
    ids = []
    for _, _, i in title_spans + sent_spans:
        if i not in ids:
            ids.append(i)
    a["term_ids"] = ids[:MAX_TERMS]
    a["spans_title"] = [s for s in title_spans if s[2] in a["term_ids"]]
    a["spans_sent"] = [s for s in sent_spans if s[2] in a["term_ids"]]


def highlight(text, spans):
    """설명이 붙은 용어에 점선 밑줄을 칩니다."""
    out, pos = [], 0
    for start, end, _ in spans:
        out.append(escape(text[pos:start]))
        out.append(f'<span class="t">{escape(text[start:end])}</span>')
        pos = end
    out.append(escape(text[pos:]))
    return "".join(out)


# ---------- 기사 수집 ----------

def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def parse_time(value):
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception:
        return None


def read_feed(name, url):
    """RSS 하나를 읽어 기사 목록으로 돌려줍니다. 실패하면 None."""
    last_error = None
    for _ in range(2):  # 한 번 더 시도합니다.
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                root = ET.fromstring(resp.read())
            break
        except Exception as e:
            last_error = e
    else:
        print(f"[실패] {name} {url}: {last_error}")
        return None
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
            "desc": clean(it.findtext("description"))[:1500],
            "ts": parse_time(it.findtext("pubDate") or ""),
        })
        if len(items) >= PER_FEED:
            break
    return items


def fetch_feed(name, urls):
    for url in urls:
        items = read_feed(name, url)
        if items:
            print(f"[성공] {name}: {len(items)}건")
            return items
    return []


def is_noise(a):
    return any(w in a["title"] for w in SKIP)


def score(a):
    """핵심어가 많을수록 높은 점수. '한국은행'과 '은행'처럼 겹치는 단어는 한 번만 셉니다."""
    words = [(w, 3, 2) for w in CORE] + [(w, 2, 1) for w in BOOST]
    words.sort(key=lambda x: len(x[0]), reverse=True)  # 긴 단어부터 찾습니다.
    title, desc, s = a["title"], a["desc"], 0
    for w, in_title, in_desc in words:
        if w in title:
            s += in_title
            title = title.replace(w, " ")
        if w in desc:
            s += in_desc
            desc = desc.replace(w, " ")
    return s + TERM_BONUS * len(a.get("term_ids", []))


def pick_articles(all_items, data, index):
    """동정·행사 기사를 빼고, 점수가 높은 순(같으면 최신 순)으로 고릅니다."""
    seen, pool = set(), []
    for a in all_items:
        key = re.sub(r"\W+", "", a["title"])
        if key in seen or is_noise(a):
            continue
        seen.add(key)
        enrich(a, data, index)
        pool.append(a)
    oldest = datetime.min.replace(tzinfo=KST)
    pool.sort(key=lambda a: (score(a), a["ts"] or oldest), reverse=True)

    picked, count = [], {}
    for a in pool:  # 한 출처에 몰리지 않게 먼저 제한을 둡니다.
        if len(picked) >= TOP_N:
            break
        if count.get(a["source"], 0) < MAX_PER_SOURCE:
            picked.append(a)
            count[a["source"]] = count.get(a["source"], 0) + 1
    for a in pool:  # 모자라면 제한을 풀고 채웁니다.
        if len(picked) >= TOP_N:
            break
        if a not in picked:
            picked.append(a)
    picked.sort(key=lambda a: a["ts"] or oldest, reverse=True)
    return picked


# ---------- 페이지 ----------

def story_html(a, data):
    when = a["ts"].strftime("%m.%d %H:%M") if a["ts"] else ""
    link = escape(a["link"], quote=True)
    title = highlight(a["title"], a["spans_title"])
    sentence = f'<p class="sum">{highlight(a["sentence"], a["spans_sent"])}</p>' if a["sentence"] else ""
    terms = ""
    if a["term_ids"]:
        rows = "\n".join(
            f'          <dt>{escape(data[i]["name"])}</dt>\n          <dd>{escape(data[i]["desc"])}</dd>'
            for i in a["term_ids"]
        )
        terms = f"""
      <aside class="terms" aria-label="용어 풀이">
        <p class="terms-h">용어 풀이</p>
        <dl>
{rows}
        </dl>
      </aside>"""
    solo = "" if terms else " solo"
    return f"""    <article class="story{solo}">
      <div class="main">
        <div class="meta"><span class="tag">{escape(a["source"])}</span><span class="when">{escape(when)}</span></div>
        <h2><a href="{link}" target="_blank" rel="noopener">{title}</a></h2>
        {sentence}
        <p class="src"><a href="{link}" target="_blank" rel="noopener">원문 보기</a></p>
      </div>{terms}
    </article>"""


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>경제뉴스 브리핑</title>
<meta name="description" content="매일 아침 갱신되는 주요 금융·경제 뉴스와 쉬운 용어 풀이">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+KR:wght@400;500;700&display=swap">
<style>
:root {
  color-scheme: light;
  --bg: #f1f4f7;
  --surface: #ffffff;
  --tint: #eef3f6;
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
    --tint: #1c2833;
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
.wrap { max-width: 980px; margin: 0 auto; }
.head { padding: 0 4px 20px; }
.date { margin: 0 0 10px; font: 500 13px/1 var(--font-mono); letter-spacing: 0.04em; color: var(--muted); }
h1 { margin: 0 0 8px; font-size: 30px; line-height: 1.25; font-weight: 700; letter-spacing: -0.01em; text-wrap: balance; }
.lede { margin: 0; color: var(--muted); font-size: 15px; line-height: 1.7; max-width: 38em; }
.sheet { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 4px 24px; }
.story {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
  gap: 16px 32px;
  padding: 24px 0;
  border-top: 1px solid var(--line);
}
.story:first-child { border-top: 0; }
.story.solo { grid-template-columns: minmax(0, 1fr); }
.main, .terms { min-width: 0; }
.meta { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.tag { font-size: 13px; font-weight: 700; letter-spacing: 0.06em; color: var(--accent); }
.when { font: 400 12px/1 var(--font-mono); color: var(--muted); white-space: nowrap; }
h2 { margin: 10px 0 8px; font-size: 19px; line-height: 1.45; font-weight: 700; letter-spacing: -0.01em; text-wrap: balance; }
h2 a { color: inherit; text-decoration: none; }
h2 a:hover { text-decoration: underline; text-underline-offset: 3px; }
.sum { margin: 0; font-size: 15px; line-height: 1.8; max-width: 38em; }
.t { border-bottom: 1.5px dotted var(--accent); }
.src { margin: 12px 0 0; font-size: 13px; }
.src a { color: var(--accent); text-underline-offset: 3px; }
.terms { align-self: start; background: var(--tint); border-radius: 8px; padding: 14px 16px 6px; }
.terms-h { margin: 0 0 10px; font: 500 12px/1 var(--font-mono); letter-spacing: 0.04em; color: var(--muted); }
.terms dl { margin: 0; }
.terms dt { font-size: 14px; font-weight: 700; color: var(--accent); }
.terms dd { margin: 2px 0 12px; font-size: 14px; line-height: 1.7; }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 2px; }
.foot { margin: 18px 4px 0; font-size: 13px; color: var(--muted); line-height: 1.7; }
@media (max-width: 720px) {
  .story { grid-template-columns: minmax(0, 1fr); }
}
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
    <p class="lede">주요 언론사의 금융경제 기사를 매일 아침 모아 보여줍니다. 점선이 그어진 말은 기사 옆에서 쉽게 풀어 드립니다.</p>
  </header>
  <main class="sheet">
__STORIES__
  </main>
  <p class="foot">제목과 핵심 문장은 각 언론사 RSS가 제공하는 내용이고, 전체 기사는 원문 링크에서 볼 수 있습니다. 용어 풀이는 한국은행 경제금융용어 700선의 용어를 바탕으로 쉬운 말로 새로 쓴 것이라 정확한 정의는 한국은행 자료를 확인하세요. 투자 조언이 아닙니다.</p>
</div>
</body>
</html>
"""


def build_page(articles, data, now):
    date = "갱신 " + now.strftime("%Y.%m.%d %a %H:%M").upper()
    stories = "\n".join(story_html(a, data) for a in articles)
    return TEMPLATE.replace("__DATE__", escape(date)).replace("__STORIES__", stories)


def main():
    data, index = load_glossary()
    all_items = []
    for name, urls in FEEDS.items():
        all_items.extend(fetch_feed(name, urls))
    articles = pick_articles(all_items, data, index)
    if not articles:
        # 기존 페이지를 지우지 않고 실패로 끝냅니다.
        print("가져온 기사가 없어 페이지를 만들지 않았습니다.")
        sys.exit(1)
    OUT.write_text(build_page(articles, data, datetime.now(KST)), encoding="utf-8")
    print(f"{OUT.name} 생성 완료 ({len(articles)}건, 용어집 {len(data)}개)")
    for a in articles:
        names = ", ".join(data[i]["name"] for i in a["term_ids"]) or "-"
        print(f"  [{score(a)}점] {a['source']} | {a['title']} | 용어: {names}")


if __name__ == "__main__":
    main()
