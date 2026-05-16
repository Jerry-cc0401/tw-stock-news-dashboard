#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股題材新聞儀表板 v3

功能：
1. 依題材自動抓 Google News RSS。
2. 自動分成：焦點大事、AI半導體、散熱、光通訊、PCB/CCL、被動元件、電源、記憶體、金融、生技。
3. 自動產生：
   - 今日焦點結論
   - 題材熱度分數
   - 持股／關注股影響表
   - 來源可信度分級
   - 盤前操作提醒
4. 產生 tw_stock_news_dashboard_v3.html。

執行：
python fetch_news.py

可選：
python fetch_news.py --days 2 --max 12
"""

from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import argparse
import json
import re
import time
import html as html_lib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "news_topics.json"
DATA_PATH = ROOT / "news_auto_data.json"
TEMPLATE_PATH = ROOT / "dashboard_template.html"
HTML_OUT = ROOT / "tw_stock_news_dashboard_v3.html"

USER_AGENT = "Mozilla/5.0 (compatible; TWStockNewsDashboardV3/3.0; local-script)"

POSITIVE_WORDS = [
    "大漲", "上漲", "創高", "續強", "利多", "看好", "成長", "爆發", "供不應求",
    "缺貨", "漲價", "上修", "旺", "接單", "擴產", "法說報喜", "營收創高",
    "需求強", "買超", "增持", "調升"
]
NEGATIVE_WORDS = [
    "下跌", "重挫", "殺低", "利空", "看淡", "衰退", "賣超", "匯出", "降評",
    "下修", "處置", "爆量不漲", "開高走低", "獲利了結", "庫存", "需求放緩",
    "貶", "震盪", "轉弱"
]
RISK_WORDS = ["處置", "漲多", "爆量不漲", "開高走低", "外資賣超", "台幣貶", "降息延後", "關稅", "地緣", "停利", "獲利了結"]

def now_taipei() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))

def strip_tags(text: str) -> str:
    text = html_lib.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def normalize_title(title: str) -> str:
    title = strip_tags(title)
    title = re.sub(r"\s-\s.*$", "", title)
    title = re.sub(r"[^\w\u4e00-\u9fff]+", "", title.lower())
    return title[:120]

def fetch_url(url: str, timeout: int = 18) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()

def google_news_rss_url(query: str, locale: dict) -> str:
    params = {
        "q": query,
        "hl": locale.get("hl", "zh-TW"),
        "gl": locale.get("gl", "TW"),
        "ceid": locale.get("ceid", "TW:zh-Hant")
    }
    return "https://news.google.com/rss/search?" + urlencode(params)

def parse_rss(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    items = []
    for item in root.findall(".//item"):
        title = strip_tags(item.findtext("title", ""))
        link = item.findtext("link", "") or ""
        pub_date_raw = item.findtext("pubDate", "") or ""
        source_el = item.find("source")
        source = strip_tags(source_el.text if source_el is not None else "")
        desc = strip_tags(item.findtext("description", ""))
        try:
            published = parsedate_to_datetime(pub_date_raw).astimezone(timezone(timedelta(hours=8))).isoformat()
        except Exception:
            published = ""
        items.append({
            "title": title,
            "link": link,
            "source": source,
            "published": published,
            "description": desc
        })
    return items

def count_words(text: str, words: list[str]) -> int:
    t = text.lower()
    return sum(1 for w in words if w.lower() in t)

def keyword_score(text: str, keywords: list[str]) -> int:
    score = 0
    t = text.lower()
    for kw in keywords:
        if kw.lower() in t:
            score += 3 if len(kw) >= 3 else 1
    return score

def source_grade(source: str, rules: dict) -> str:
    s = source or ""
    for grade in ["A_官方", "B_主流財經"]:
        for kw in rules.get(grade, []):
            if kw and kw.lower() in s.lower():
                return grade
    return "C_參考"

def detect_stocks(text: str, stocks: dict) -> list[str]:
    out = []
    for code, name in stocks.items():
        if code in text or name in text:
            out.append(code)
    return out

def make_article_view(topic_name: str, item: dict, related_stocks: list[str], stock_names: dict) -> str:
    related_text = ""
    if related_stocks:
        related_text = " 關聯標的：" + "、".join([f"{c} {stock_names.get(c, '')}" for c in related_stocks]) + "。"
    templates = {
        "焦點大事": "先判斷它對台指期、台幣、台積電與外資風險偏好的影響，再看開盤後是否有承接。",
        "AI 半導體／先進封裝": "重點看台積電、先進封裝、ASIC、HBM、測試設備是否延續訂單能見度。",
        "散熱": "散熱仍看 AI 伺服器功耗升級，但短線要防法說利多出盡或開高爆量不漲。",
        "光通訊／CPO": "光通訊與 CPO 偏中長線成長題材，短線需分辨實際出貨進度與純概念拉抬。",
        "PCB／CCL": "PCB／CCL 仍是 AI 伺服器升級主線，觀察高階材料與伺服器板是否有量價同步。",
        "被動元件": "被動元件要看 MLCC、電阻、電感報價與 AI／車用需求是否同步改善。",
        "電源／電力管理": "AI 資料中心用電需求上升，電源與電力管理偏中長線，但仍需看法人承接。",
        "記憶體": "記憶體看報價、缺貨、HBM 與庫存循環；急漲後波動通常較大。",
        "金融": "金融通常是防守與撐盤角色，電子股震盪時才更容易被資金短線關注。",
        "生技": "生技偏個股題材，需確認藥證、授權、臨床或營收事件是否有延續性。"
    }
    return templates.get(topic_name, "觀察是否有基本面與資金面延續。") + related_text

def topic_view_from_heat(name: str, heat: int, pos: int, neg: int) -> str:
    if heat >= 80 and pos >= neg:
        return f"{name} 今日新聞熱度高且偏正向，可列為盤前第一層觀察；但若開高急拉，要避免追在情緒高點。"
    if heat >= 65:
        return f"{name} 題材熱度偏高，適合觀察是否有族群同步與量價延續，不宜只看單一個股急拉。"
    if heat >= 45:
        return f"{name} 有題材但不是最強主線，適合等資金輪動或出現明確營收／法說訊號。"
    return f"{name} 今日新聞熱度較低，除非個股有重大消息，否則先列為次要觀察。"

def impact_label(score: int, pos: int, neg: int) -> str:
    if score >= 8 and pos >= neg:
        return "偏多"
    if score >= 4 and pos >= neg:
        return "中性偏多"
    if neg > pos and score >= 4:
        return "偏空／風險升高"
    return "中性"

def market_bias(all_articles: list[dict], topic_reports: list[dict]) -> dict:
    text = " ".join([a.get("title", "") + " " + a.get("description", "") for a in all_articles])
    pos = count_words(text, POSITIVE_WORDS)
    neg = count_words(text, NEGATIVE_WORDS)
    avg_heat = round(sum(t.get("heat_score", 0) for t in topic_reports) / max(1, len(topic_reports)))
    if pos >= neg + 3 and avg_heat >= 60:
        label = "偏多，但需防高檔震盪"
        reason = "AI 與科技題材新聞偏正向，題材熱度也維持高檔。"
    elif neg >= pos + 3:
        label = "偏空／震盪防守"
        reason = "負面詞與風險詞偏多，盤前需優先看台指期、台幣與權值股承接。"
    else:
        label = "震盪輪動"
        reason = "正負訊號接近，盤面可能以族群輪動為主，不適合無差別追高。"
    return {
        "label": label,
        "reason": reason,
        "positive_signals": pos,
        "negative_signals": neg,
        "average_topic_heat": avg_heat
    }

def build_report(days: int, max_items: int) -> dict:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    settings = config.get("settings", {})
    locale = settings.get("google_news_locale", {})
    source_rules = settings.get("source_grade_rules", {})
    holdings = settings.get("my_holdings", {})
    watchlist = settings.get("watchlist", {})
    all_stock_names = {**holdings, **watchlist}
    topics = config.get("topics", [])
    cutoff = now_taipei() - timedelta(days=days)

    seen_global = {}
    topic_reports = []
    all_articles = []
    fetch_errors = []

    for topic in topics:
        topic_items = []
        for query in topic.get("queries", []):
            try:
                raw = fetch_url(google_news_rss_url(query, locale))
                rss_items = parse_rss(raw)
            except Exception as exc:
                fetch_errors.append(f"{topic.get('name')}｜{query}｜{exc}")
                continue

            for item in rss_items:
                if item.get("published"):
                    try:
                        if datetime.fromisoformat(item["published"]) < cutoff:
                            continue
                    except Exception:
                        pass

                title_key = normalize_title(item.get("title", ""))
                if not title_key:
                    continue

                text = item.get("title", "") + " " + item.get("description", "")
                rel_score = keyword_score(text, topic.get("keywords", []))
                pos = count_words(text, POSITIVE_WORDS)
                neg = count_words(text, NEGATIVE_WORDS)
                risks = [w for w in RISK_WORDS if w in text]
                related_stocks = detect_stocks(text, all_stock_names)
                grade = source_grade(item.get("source", ""), source_rules)

                article = {
                    **item,
                    "event": item.get("title", ""),
                    "view": make_article_view(topic.get("name", ""), item, related_stocks, all_stock_names),
                    "topic_id": topic.get("id"),
                    "topic_name": topic.get("name"),
                    "source_grade": grade,
                    "relevance_score": rel_score + pos - min(neg, 2),
                    "positive_count": pos,
                    "negative_count": neg,
                    "risk_flags": risks,
                    "stocks": related_stocks
                }

                if title_key in seen_global:
                    existing = seen_global[title_key]
                    if topic.get("name") not in existing["matched_topics"]:
                        existing["matched_topics"].append(topic.get("name"))
                    existing["relevance_score"] += max(1, rel_score)
                    continue

                article["matched_topics"] = [topic.get("name")]
                seen_global[title_key] = article
                topic_items.append(article)
                all_articles.append(article)

            time.sleep(0.25)

        # add core stocks if topic-level
        topic_text = " ".join([i.get("title", "") + " " + i.get("description", "") for i in topic_items])
        topic_pos = count_words(topic_text, POSITIVE_WORDS)
        topic_neg = count_words(topic_text, NEGATIVE_WORDS)
        topic_risks = sorted(list(set([r for i in topic_items for r in i.get("risk_flags", [])])))
        # heat formula: article count + relevance + positive/negative balance
        count_score = min(45, len(topic_items) * 6)
        relevance = min(30, sum(max(0, i.get("relevance_score", 0)) for i in topic_items) // 3)
        sentiment = max(-15, min(25, topic_pos * 4 - topic_neg * 3))
        heat = max(0, min(100, 25 + count_score + relevance + sentiment))

        topic_items = sorted(
            topic_items,
            key=lambda x: (x.get("source_grade") == "A_官方", x.get("source_grade") == "B_主流財經", x.get("relevance_score", 0), x.get("published", "")),
            reverse=True
        )[:max_items]

        topic_reports.append({
            "id": topic.get("id"),
            "name": topic.get("name"),
            "description": topic.get("description"),
            "core_stocks": topic.get("core_stocks", []),
            "heat_score": int(heat),
            "positive_count": topic_pos,
            "negative_count": topic_neg,
            "risk_flags": topic_risks,
            "topic_view": topic_view_from_heat(topic.get("name", ""), int(heat), topic_pos, topic_neg),
            "items": topic_items
        })

    # focus articles
    focus_candidates = sorted(
        list(seen_global.values()),
        key=lambda x: (x.get("source_grade") == "A_官方", x.get("source_grade") == "B_主流財經", x.get("relevance_score", 0), x.get("published", "")),
        reverse=True
    )[:10]

    # stock impact table
    stock_impacts = []
    for code, name in all_stock_names.items():
        stock_articles = []
        related_topics = []
        score = 0
        pos = 0
        neg = 0
        risks = []
        for t in topic_reports:
            topic_related = code in t.get("core_stocks", [])
            article_related = [a for a in t.get("items", []) if code in a.get("stocks", []) or name in (a.get("title","") + a.get("description",""))]
            if topic_related or article_related:
                related_topics.append(t.get("name"))
                score += int(t.get("heat_score", 0) / 12)
                pos += t.get("positive_count", 0)
                neg += t.get("negative_count", 0)
                risks.extend(t.get("risk_flags", []))
                stock_articles.extend(article_related[:3])
        stock_impacts.append({
            "code": code,
            "name": name,
            "type": "持有股" if code in holdings else "關注股",
            "impact": impact_label(score, pos, neg),
            "impact_score": min(100, score * 8),
            "related_topics": sorted(list(set(related_topics))),
            "risk_flags": sorted(list(set(risks)))[:6],
            "watch_point": stock_watch_point(code, name, sorted(list(set(related_topics))), sorted(list(set(risks)))),
            "articles": stock_articles[:4]
        })

    stock_impacts.sort(key=lambda x: (x["type"] == "持有股", x["impact_score"]), reverse=True)

    bias = market_bias(all_articles, [t for t in topic_reports if t["id"] != "focus"])
    strategy = make_strategy(bias, topic_reports)

    report = {
        "generated_at": now_taipei().strftime("%Y-%m-%d %H:%M"),
        "title": "台股題材新聞儀表板 v3",
        "settings": settings,
        "market_bias": bias,
        "strategy": strategy,
        "market_note": {
            "event": "新聞由 Google News RSS 依題材自動抓取並初步分類。",
            "view": "台指期夜盤、8:30 試撮、台幣匯率、即時股價與成交量請以期交所、央行、證交所與券商 App 交叉確認。"
        },
        "focus": focus_candidates,
        "topics": topic_reports,
        "stock_impacts": stock_impacts,
        "fetch_errors": fetch_errors,
        "disclaimer": "僅供參考，不構成投資建議。"
    }
    return report

def stock_watch_point(code: str, name: str, topics: list[str], risks: list[str]) -> str:
    topic_text = "、".join(topics[:3]) if topics else "目前未被主要題材明顯帶動"
    risk_text = "；風險：" + "、".join(risks[:3]) if risks else ""
    specific = {
        "2330": "先看台積電是否穩住指數與先進製程／封裝消息，若 ADR 或台幣偏弱，開盤承接更重要。",
        "2308": "看 AI 資料中心、電源與 BBU 題材是否延續，以及外資是否續買。",
        "3017": "看散熱法說、液冷需求與高檔是否爆量不漲。",
        "3324": "看散熱族群是否同步，若奇鋐強而雙鴻弱，要注意族群分化。",
        "3044": "看 PCB／伺服器板是否補漲，以及量增是否能守住短均。",
        "2327": "看被動元件是否有漲價、AI 伺服器或車用需求回溫消息。",
        "7769": "看 AI／HPC／ASIC 測試設備需求與高價股估值壓力。",
        "2360": "看測試設備與半導體資本支出是否延續。",
        "4958": "看 PCB 族群與 AI 伺服器板需求是否擴散到臻鼎。",
        "5347": "看成熟製程是否補漲、產能利用率與晶圓代工情緒。"
    }
    return specific.get(code, f"主要關聯題材：{topic_text}{risk_text}")

def make_strategy(bias: dict, topics: list[dict]) -> dict:
    ranked = sorted([t for t in topics if t["id"] != "focus"], key=lambda x: x.get("heat_score", 0), reverse=True)
    top3 = ranked[:3]
    risk_topics = [t for t in ranked if t.get("negative_count", 0) > t.get("positive_count", 0) or t.get("risk_flags")]
    if "偏多" in bias.get("label", ""):
        action = "偏多觀察，但不追開盤第一根"
        note = "先等 9:30 後確認台積電、台達電、散熱與 PCB 是否有承接，再決定是否加碼。"
    elif "偏空" in bias.get("label", ""):
        action = "防守優先"
        note = "若台指期、台幣、台積電同步轉弱，先避免加碼，觀察是否急殺收腳。"
    else:
        action = "震盪輪動，挑族群不挑全部"
        note = "優先看題材熱度最高且有持股關聯的族群，避免追短線爆量不漲個股。"
    return {
        "action": action,
        "note": note,
        "top_topics": [{"name": t["name"], "heat_score": t["heat_score"], "view": t["topic_view"]} for t in top3],
        "risk_topics": [{"name": t["name"], "risk_flags": t.get("risk_flags", [])[:4]} for t in risk_topics[:4]]
    }

def inject_html(report: dict) -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    HTML_OUT.write_text(template.replace("__REPORT_DATA__", json.dumps(report, ensure_ascii=False, indent=2)), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--max", type=int, default=None)
    args = parser.parse_args()

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    settings = cfg.get("settings", {})
    days = args.days or int(settings.get("days", 2))
    max_items = args.max or int(settings.get("max_items_per_topic", 12))

    report = build_report(days=days, max_items=max_items)
    DATA_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    inject_html(report)
    print(f"完成：{HTML_OUT}")
    print(f"資料：{DATA_PATH}")
    if report.get("fetch_errors"):
        print("部分新聞抓取失敗：")
        for e in report["fetch_errors"][:8]:
            print("-", e)

if __name__ == "__main__":
    main()
