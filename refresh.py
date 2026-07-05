#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aiper EU GTM 每小时价格记录器。三种运行模式（GTM_MODE 环境变量）：
  cloud  - GitHub Actions 跑，抓 aiper.store / eRobot / Irripiscine / pool-systems（机房 IP 不受限）
  local  - 本地 Mac cron 跑，抓 Amazon / Boulanger（这两家会拦截机房 IP，只能用住宅网络抓）
  apify  - GitHub Actions 跑，通过 Apify 云端浏览器代理抓 Hornbach / MyPiscine / Leroy Merlin
           （这三家的反爬墙需要真实浏览器渲染才能过，Apify 的机房里也是这么做的，
           所以这一档实际上也不依赖本地网络，可以放进云端 workflow）
Idealo / Cdiscount 仍需人工核价，写入 aiper-gtm-feed-manual.js（refresh.py 不碰这个文件）。
"""
import json, os, re, ssl, time, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")   # 本地 Mac 与 GitHub Actions 统一用巴黎时间
MODE = os.environ.get("GTM_MODE", "local")   # cloud | local | apify
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")

HOME = Path(__file__).resolve().parent
FEED_FILE = {"cloud": "aiper-gtm-feed.js", "local": "aiper-gtm-feed-local.js", "apify": "aiper-gtm-feed-apify.js"}[MODE]
FEED_VAR = {"cloud": "GTM_FEED", "local": "GTM_FEED_LOCAL", "apify": "GTM_FEED_APIFY"}[MODE]
FEED = HOME / FEED_FILE

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
CTX = ssl.create_default_context()

def fetch(url, lang="en", tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": lang,
                "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception as e:
            last = e
            time.sleep(4 * (i + 1))     # 数据中心 IP 偶发被拦，退避重试
    raise last

def apify_fetch(url, tries=2):
    """通过 Apify 的 apify/web-scraper Actor 用真实浏览器渲染页面，返回渲染后的完整 HTML。
    Actor 本身跑在 Apify 的机房+代理里，所以这里发起调用的机器（GitHub Actions）不需要住宅 IP。"""
    page_function = (
        "async function pageFunction(context) {"
        " return { url: context.request.url, html: document.documentElement.outerHTML };"
        "}"
    )
    payload = json.dumps({
        "startUrls": [{"url": url}],
        "globs": [], "linkSelector": "",
        "pageFunction": page_function,
        "proxyConfiguration": {"useApifyProxy": True},
        "useChrome": True, "headless": True,
        "waitUntil": ["networkidle2"],
        "closeCookieModals": True,
        "downloadMedia": False, "downloadCss": False,
        "injectJQuery": False, "runMode": "PRODUCTION",
    }).encode()
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                f"https://api.apify.com/v2/acts/apify~web-scraper/run-sync-get-dataset-items"
                f"?token={APIFY_TOKEN}&timeout=90",
                data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=110, context=CTX) as r:
                items = json.loads(r.read().decode("utf-8", errors="ignore"))
            return items[0]["html"] if items else ""
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise last

def p_jsonld(t):      # schema.org Offer price（含 aiper.store 转义 JSON-LD，也兼容普通 <script type=ld+json>）
    m = re.search(r'\\?"price\\?"\s*:\s*\\?"?([\d]+(?:\.[\d]+)?)', t)
    return float(m.group(1)) if m else None

def p_amazon(t):
    m = re.search(r'"priceAmount":([\d.]+)', t)
    if m: return float(m.group(1))
    m = re.search(r'class="a-price-whole">([\d.,]+)<', t)
    f = re.search(r'class="a-price-fraction">(\d+)', t)
    if m:
        whole = m.group(1).replace(".", "").replace(",", "")
        return float(whole) + (float("0." + f.group(1)) if f else 0)
    return None

def p_meta(t):        # PrestaShop og product:price:amount（含税）
    m = re.search(r'property="product:price:amount"\s+content="([\d.]+)"', t) or \
        re.search(r'content="([\d.]+)"\s+property="product:price:amount"', t)
    return float(m.group(1)) if m else None

def money_to_float(s):
    s = s.replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    return float(s)

def p_price_text(t, labels=()):
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
    patterns = [rf"([0-9]{{1,4}}(?:[ .][0-9]{{3}})*[,.][0-9]{{2}})\s*€\s*{label}" for label in labels]
    patterns += [r"([0-9]{1,4}(?:[ .][0-9]{3})*[,.][0-9]{2})\s*€"]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return money_to_float(m.group(1))
    return None

def p_mypiscine(t):
    # MyPiscine 的 JSON-LD 曾返回法国不含税价（HT），例如 549 TTC 被抓成 457.50。
    return p_price_text(t, ("TTC",)) or p_meta(t) or p_jsonld(t)

def p_boulanger(t):
    # Boulanger 页面可能混入 marketplace/旧价字段，先尽量读可见主价，再交给防呆过滤。
    return p_price_text(t, ("TTC",)) or p_jsonld(t)

def p_generic(t):     # Apify 渲染后的通用兜底：JSON-LD → meta → 正文里的 "NN,NN €" / "NN.NN €"
    v = p_jsonld(t)
    if v: return v
    v = p_meta(t)
    if v: return v
    m = re.search(r'([0-9]{1,4}[.,][0-9]{2})\s*€', t)
    return float(m.group(1).replace(",", ".")) if m else None

# (key, url, parser, lang) — key 格式 pid|cc|channel
TARGETS = [
    # Aiper 官网 —— 必须用各国分站页（泛欧 /eu 页价格会滞后，2026-07 曾显示 €499 vs 分站 €521）
    ("s1|de|aiper",    "https://aiper.store/de/products/aiper-scuba-s1-kabelloser-robotischer-poolreiniger", p_jsonld, "de"),
    ("s1|fr|aiper",    "https://aiper.store/fr/products/scuba-s1-nettoyeur-de-piscine-robotis%C3%A9-sans-fil", p_jsonld, "fr"),
    ("s1|es|aiper",    "https://aiper.store/es/products/aiper-scuba-s1-limpiador-de-piscina-rob%C3%B3tico-sin-cables", p_jsonld, "es"),
    ("v3|de|aiper",    "https://aiper.store/de/products/aiper-scuba-v3", p_jsonld, "de"),
    ("v3|fr|aiper",    "https://aiper.store/fr/products/aiper-scuba-v3", p_jsonld, "fr"),
    ("v3|es|aiper",    "https://aiper.store/es/products/aiper-scuba-v3", p_jsonld, "es"),
    ("irri2|de|aiper", "https://aiper.store/de/products/aiper-irrisense-2", p_jsonld, "de"),
    ("irri2|fr|aiper", "https://aiper.store/fr/products/aiper-irrisense-2", p_jsonld, "fr"),
    ("irri2|es|aiper", "https://aiper.store/es/products/aiper-irrisense-2", p_jsonld, "es"),
    # Amazon 官方店（cloud 机房 IP 会被拦，划入 local）
    ("s1|de|amazon",    "https://www.amazon.de/dp/B0DL46SD6W", p_amazon, "de-DE,de;q=0.9"),
    ("v3|de|amazon",    "https://www.amazon.de/dp/B0GFW5VBDQ", p_amazon, "de-DE,de;q=0.9"),
    ("irri2|de|amazon", "https://www.amazon.de/dp/B0GTZGSH9G", p_amazon, "de-DE,de;q=0.9"),
    ("s1|fr|amazon",    "https://www.amazon.fr/dp/B0CPSQBFNG", p_amazon, "fr-FR,fr;q=0.9"),
    ("v3|fr|amazon",    "https://www.amazon.fr/dp/B0GFW5VBDQ", p_amazon, "fr-FR,fr;q=0.9"),
    ("irri2|fr|amazon", "https://www.amazon.fr/dp/B0GLNNKX2R", p_amazon, "fr-FR,fr;q=0.9"),
    ("s1|es|amazon",    "https://www.amazon.es/dp/B0FD9K27GT", p_amazon, "es-ES,es;q=0.9"),
    ("v3|es|amazon",    "https://www.amazon.es/dp/B0GFW5VBDQ", p_amazon, "es-ES,es;q=0.9"),
    # 德国其他零售商
    ("s1|de|poolsystems", "https://www.pool-systems.de/Poolroboter/Aiper-Scuba-S1.html", p_jsonld, "de-DE"),
    # 法国其他零售商（可自动抓取的）
    ("s1|fr|erobot",      "https://www.erobot-piscine.fr/robot/robot-piscine-sans-fil-/robot-sans-fil-aiper-scuba-s1", p_meta, "fr-FR"),
    ("v3|fr|erobot",      "https://www.erobot-piscine.fr/robot/robot-piscine-sans-fil-/robot-piscine-sans-fil-aiper-scuba-v3", p_meta, "fr-FR"),
    ("s1|fr|boulanger",   "https://www.boulanger.com/ref/1220192", p_boulanger, "fr-FR,fr;q=0.9"),      # cloud 机房 IP 超时，划入 local
    ("v3|fr|boulanger",   "https://www.boulanger.com/ref/1235250", p_boulanger, "fr-FR,fr;q=0.9"),
    ("s1|fr|irripiscine", "https://www.irripiscine.fr/produit/robot-de-piscine-aiper-scuba-s1-sans-fil", p_jsonld, "fr-FR"),
    ("v3|fr|irripiscine", "https://www.irripiscine.fr/produit/robot-piscine-sans-fil-aiper-scuba-v3", p_jsonld, "fr-FR"),
]

# 需要 Apify 浏览器渲染才能绕过反爬的渠道（普通 curl 会拿到验证页 / 403 / 503-无内容）
# 实测（2026-07-05）：Hornbach / MyPiscine 用 Apify 默认代理即可绕过；
# Idealo（503）/ Leroy Merlin（403，重试 4 次仍被拦）防护更强，Apify 免费代理过不去，继续人工核价。
APIFY_TARGETS = [
    ("s1|de|hornbach", "https://www.hornbach.de/p/aiper-scuba-s1-2026-upgrade-poolroboter-fuer-pools-bis-zu-150-m-kabellose-reinigung-von-boden-waenden-und-wasserlinie-15-900-l-h-durchflussrate-180-minuten-akkulaufzeit/12407504/", p_generic),
    ("v3|de|hornbach", "https://www.hornbach.de/p/aiper-scuba-v3-poolroboter-fuer-pools-bis-zu-150-m-kabellose-reinigung-von-boden-und-waenden-18-000-l-h-durchflussrate-180-minuten-akkulaufzeit/12695324/", p_generic),
    ("s1|fr|mypiscine", "https://www.mypiscine.com/robot-piscine-sans-fil/26107-robot-piscine-sans-fil-aiper-scuba-s1-2025-6977676340140.html", p_mypiscine),
    ("v3|fr|mypiscine", "https://www.mypiscine.com/robot-piscine-sans-fil/26313-robot-piscine-sans-fil-aiper-scuba-v3-6977676345015.html", p_mypiscine),
]
# Idealo/Cdiscount/LeroyMerlin：继续人工核价（aiper-gtm-feed-manual.js，refresh.py 不碰这个文件）

MAX_PRICE_BY_PID = {"s1": 599, "v3": 999}

def validate_price(key, price):
    pid = key.split("|", 1)[0]
    max_price = MAX_PRICE_BY_PID.get(pid)
    if max_price is not None and price > max_price:
        return False, f"above max guard {price:g} > {max_price:g}"
    return True, ""

def scrub_prices(prices):
    return {k: v for k, v in prices.items() if validate_price(k, v)[0]}

CLOUD_BLOCKED = ("amazon", "boulanger")   # 机房 IP 抓不到，划给 local 模式
def my_targets():
    if MODE == "cloud":
        return [t for t in TARGETS if t[0].split("|")[2] not in CLOUD_BLOCKED]
    if MODE == "local":
        return [t for t in TARGETS if t[0].split("|")[2] in CLOUD_BLOCKED]
    return []   # apify 模式走 APIFY_TARGETS，见 main()

def load_feed():
    if not FEED.exists(): return []
    m = re.search(r'window\.\w+\s*=\s*(\[.*\]);?\s*$', FEED.read_text(), re.S)
    if not m: return []
    try: return json.loads(m.group(1))
    except Exception: return []

def main():
    now = datetime.now(TZ)
    ts = now.strftime("%Y-%m-%d %H:00")
    prices, errors = {}, []

    for key, url, parser, lang in my_targets():
        try:
            v = parser(fetch(url, lang))
            if v is None: errors.append(f"{key}: no price matched"); continue
            ok, reason = validate_price(key, v)
            if not ok: errors.append(f"{key}: rejected ({reason})"); continue
            prices[key] = v
        except Exception as e:
            errors.append(f"{key}: {e}")

    if MODE == "apify":
        if not APIFY_TOKEN:
            errors.append("APIFY_TOKEN not set — skipping apify targets")
        else:
            for key, url, parser in APIFY_TARGETS:
                try:
                    html = apify_fetch(url)
                    v = parser(html)
                    if v is None: errors.append(f"{key}: no price matched (apify)"); continue
                    ok, reason = validate_price(key, v)
                    if not ok: errors.append(f"{key}: rejected ({reason})"); continue
                    prices[key] = v
                except Exception as e:
                    errors.append(f"{key}: {e}")

    feed = [e for e in load_feed() if e.get("ts") != ts]      # 同一小时重跑则覆盖
    feed = [{**e, "prices": scrub_prices(e.get("prices", {}))} for e in feed]
    feed = [e for e in feed if e["prices"]]
    if prices: feed.append({"ts": ts, "prices": prices})
    cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:00")
    feed = sorted([e for e in feed if e["ts"] >= cutoff], key=lambda e: e["ts"])
    FEED.write_text(f"// Aiper GTM 小时级价格记录（refresh.py {MODE} 模式自动生成，勿手改）\n"
                    f"window.{FEED_VAR} = " + json.dumps(feed, ensure_ascii=False) + ";\n")
    print(f"[{ts}] mode={MODE} recorded {len(prices)} prices, {len(errors)} errors", *errors, sep="\n  ")

if __name__ == "__main__":
    main()
