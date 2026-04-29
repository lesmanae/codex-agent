# Web Scraping & Automation — Skill

**Trigger phrases**: scrape, scraping, crawler, spider, curl, wget, httpie,
http request, http get, http post, headers, cookies, session, csrf, login form,
captcha, recaptcha, cloudflare, ddos protection, anti-bot, user agent,
playwright, puppeteer, selenium, headless browser, dom, xpath, css selector,
beautifulsoup, lxml, parsel, scrapy, requests, httpx, aiohttp, fetch, axios,
har, mitmproxy, charles, burp, devtools, network tab, api reverse engineer,
endpoint discovery, json api, graphql, websocket, rate limit, proxy, rotating
proxy, residential proxy, parallel fetch, throttle, robots.txt.

Use when the user asks to extract data from a website, replay an HTTP API,
or automate a browser-based workflow. Audience: dev who knows what they
want; be terse and pragmatic.

---

## Legal / ethical guardrails

Before scraping, always:
- Check `robots.txt` — disallowed paths are off-limits unless you have
  explicit written permission.
- Respect rate limits. Default ≤1 req/sec, bursty ≤5 req/sec, total <
  what the site clearly serves.
- Don't bypass paywalls, login walls, or anti-bot to harvest paid content.
- Don't scrape personal data (emails, phone, addresses) at scale.

If the user wants to bypass anti-bot measures or pull paid content
without authorization, decline with a one-line reason.

---

## Decision tree

1. **Is there an official API / RSS / JSON?** Use it. Stop here.
2. **Static HTML?** `httpx`/`requests` + `selectolax`/`lxml`/`beautifulsoup`.
3. **JS-rendered content?** Inspect Network tab — usually a JSON XHR
   you can hit directly. **Reverse-engineer the API first**, browser
   automation last.
4. **Truly only renders client-side?** Headless browser (Playwright >
   Puppeteer > Selenium).
5. **Behind Cloudflare/PerimeterX?** Use a real browser session
   (Playwright with persistent context) or stop.

---

## httpx (Python, async-friendly)

```python
import httpx, asyncio

HEADERS = {"user-agent": "Mozilla/5.0 ..."}

async def fetch_one(client: httpx.AsyncClient, url: str) -> dict:
    r = await client.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

async def main(urls: list[str]) -> list[dict]:
    limits = httpx.Limits(max_connections=10)
    async with httpx.AsyncClient(http2=True, limits=limits) as c:
        sem = asyncio.Semaphore(5)
        async def bounded(u):
            async with sem: return await fetch_one(c, u)
        return await asyncio.gather(*[bounded(u) for u in urls])
```

## Login + session (cookies)

```python
import httpx
with httpx.Client() as c:
    c.post("https://site/login", data={"u": user, "p": password})
    r = c.get("https://site/me")
    print(r.json())
```

## CSRF flow

```python
r = c.get("https://site/login")
# parse hidden token from form
import re
token = re.search(r'name="csrf" value="([^"]+)"', r.text)[1]
c.post("https://site/login", data={"u": user, "p": pw, "csrf": token})
```

## Parsing HTML

```python
from selectolax.parser import HTMLParser
tree = HTMLParser(html)
for a in tree.css("a.product-link"):
    print(a.attributes["href"], a.text(strip=True))
```

```python
from lxml import html as lxh
t = lxh.fromstring(html)
prices = t.xpath('//span[@class="price"]/text()')
```

---

## Reverse-engineering an API from the browser

1. Open DevTools → Network → XHR/Fetch.
2. Reproduce the action; find the JSON request.
3. Right-click → Copy → "Copy as cURL".
4. Paste into shell, trim noisy headers, then translate to your client:
   ```bash
   curl 'https://site/api/items?page=1' \
     -H 'cookie: session=...' \
     -H 'user-agent: ...'
   ```
5. Identify what's required: cookie? bearer? csrf? referer? signed
   header (`x-sig`)? Reproduce minimally.

For dynamic signatures (`x-sig=hmac(...)`), find the JS that computes it
in DevTools → Sources → search the param name. Re-implement in your
language.

---

## Playwright (when JS rendering is unavoidable)

```bash
pip install playwright
playwright install chromium
```

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent="Mozilla/5.0 ...")
    page = ctx.new_page()
    page.goto("https://site/product/123", wait_until="networkidle")
    title = page.locator("h1.title").inner_text()
    print(title)
```

For persistent login, use `chromium.launch_persistent_context(user_data_dir=...)`.

This bot's host has a Chrome on CDP at `localhost:29229`:

```python
browser = p.chromium.connect_over_cdp("http://localhost:29229")
ctx = browser.contexts[0]                # reuses existing profile
page = ctx.new_page()
```

---

## Throttling & politeness

```python
import asyncio, random
async def polite_get(client, url):
    await asyncio.sleep(random.uniform(0.8, 2.0))   # jitter
    return await client.get(url)
```

For very large crawls: use Scrapy (built-in throttling, retries, dedup).

## Pitfalls

- `requests` without `timeout=` will hang forever on a flaky server.
- Reusing the same `User-Agent` and IP at high rate → instant block.
  Rotate UA + back off on 429/503.
- Forgetting `Accept-Language` or `Referer` — many APIs reject without.
- HTML changes often. Pin selectors that are stable (data-attrs >
  classes > nth-child).
