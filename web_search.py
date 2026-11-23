import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup
from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

WEB_SEARCH_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Tra cứu thông tin chính xác trên Internet: giá xăng dầu hôm nay, giá vàng, tin tức thời sự, "
            "và ĐỌC NGUYÊN VĂN bài thơ Việt Nam (như Qua Đèo Ngang, Bếp Lửa, Sóng, Tây Tiến, Truyện Kiều...). "
            "BẮT BUỘC gọi công cụ này khi người dùng hỏi giá xăng/vàng/tin tức hoặc yêu cầu đọc bài thơ để không bao giờ bịa sai lời."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Từ khóa cần tra cứu bằng tiếng Việt (ví dụ: 'giá xăng hôm nay', 'Qua Đèo Ngang', 'Bếp Lửa').",
                }
            },
            "required": ["query"],
        },
    },
}

HEADERS = {
    "User-Agent": "XiaoZhiRobot/1.0 (https://github.com/kieninvent-cloud/xiaozhi-robot-server)"
}
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


async def _fetch_petrol_price(client: httpx.AsyncClient) -> str:
    """Fetch live Petrolimex retail fuel prices in Vietnam."""
    try:
        r = await client.get("https://giaxanghomnay.com", headers=BROWSER_HEADERS, follow_redirects=True)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            txt = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            e5_m = re.search(r"Xăng E5 RON 92 Mức 2\s+[+-]?[\d,]+\s+[+-]?[\d,]+\s+([\d,]+)", txt)
            r95_m = re.search(r"Xăng (?:E10 )?RON 95 Mức 3\s+[+-]?[\d,]+\s+[+-]?[\d,]+\s+([\d,]+)", txt)
            do_m = re.search(r"DO 0,05S Mức 2\s+[+-]?[\d,]+\s+[+-]?[\d,]+\s+([\d,]+)", txt)
            parts = []
            if r95_m:
                p95 = r95_m.group(1).replace(",", "")
                parts.append(f"xăng RON 95 là {p95} đồng một lít")
            if e5_m:
                pe5 = e5_m.group(1).replace(",", "")
                parts.append(f"xăng E5 RON 92 là {pe5} đồng một lít")
            if do_m:
                pdo = do_m.group(1).replace(",", "")
                parts.append(f"dầu đi ê zen là {pdo} đồng một lít")
            if parts:
                return "Theo bảng giá bán lẻ Petrolimex mới nhất hôm nay: " + ", ".join(parts) + "."
    except Exception as e:
        logger.bind(tag=TAG).warning(f"Petrol scrape fallback: {e}")

    try:
        r2 = await client.get("https://webtygia.com/gia-xang-dau.html", headers=BROWSER_HEADERS, follow_redirects=True)
        if r2.status_code == 200:
            soup2 = BeautifulSoup(r2.text, "html.parser")
            txt2 = re.sub(r"\s+", " ", soup2.get_text(" ", strip=True))
            m95 = re.search(r"RON 95-III\s+([\d.,]+)", txt2)
            me5 = re.search(r"E5 RON 92-II\s+([\d.,]+)", txt2)
            parts = []
            if m95:
                parts.append(f"xăng RON 95-III là {m95.group(1).replace('.', '').replace(',', '')} đồng một lít")
            if me5:
                parts.append(f"xăng E5 RON 92 là {me5.group(1).replace('.', '').replace(',', '')} đồng một lít")
            if parts:
                return "Giá xăng bán lẻ Petrolimex hôm nay: " + ", ".join(parts) + "."
    except Exception:
        pass
    return ""


def _clean_wikisource_poem(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select(".ws-header, .headertemplate, .footertemplate, .mw-editsection, table, style, script, .noprint, sup, .reference"):
        tag.decompose()
    poem_el = soup.select_one(".poem") or soup
    # Replace <br> with newline and extract clean lines
    for br in poem_el.find_all("br"):
        br.replace_with("\n")
    raw = poem_el.get_text(" ", strip=False)
    lines = []
    for ln in raw.splitlines():
        s = re.sub(r"\s+", " ", ln).strip()
        s = re.sub(r"\s+([,.!?;:])", r"\1", s)
        if s and not s.startswith("↑") and not s.startswith("["):
            lines.append(s)
    return lines


async def _fetch_poem_wikisource_vn(client: httpx.AsyncClient, query: str) -> str:
    """Fetch exact Vietnamese poem text directly from vi.wikisource.org (zero hallucination, zero extra LLM tokens!)."""
    clean_q = re.sub(r"(?i)\b(đọc|ngâm|bài thơ|thơ|của|cho mình nghe|cho tôi nghe)\b", "", query).strip()
    if not clean_q:
        clean_q = query.strip()

    try:
        r = await client.get(
            "https://vi.wikisource.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": clean_q,
                "format": "json",
                "srlimit": 5,
            },
            headers=HEADERS,
        )
        hits = (r.json() or {}).get("query", {}).get("search", [])
        if not hits:
            return ""

        for hit in hits:
            title = hit.get("title", "")
            r2 = await client.get(
                "https://vi.wikisource.org/w/api.php",
                params={"action": "parse", "page": title, "prop": "text", "format": "json"},
                headers=HEADERS,
            )
            html = ((r2.json() or {}).get("parse", {}).get("text", {}) or {}).get("*", "")
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            raw_check = soup.get_text(" ", strip=True).lower()
            if "có thể là:" in raw_check or "định hướng" in raw_check:
                for a in soup.select("ul li a[href^='/wiki/']"):
                    sub_title = a.get("title") or a.get_text(strip=True)
                    if sub_title and not sub_title.startswith("Tác giả:"):
                        r3 = await client.get(
                            "https://vi.wikisource.org/w/api.php",
                            params={"action": "parse", "page": sub_title, "prop": "text", "format": "json"},
                            headers=HEADERS,
                        )
                        html3 = ((r3.json() or {}).get("parse", {}).get("text", {}) or {}).get("*", "")
                        if html3:
                            lines3 = _clean_wikisource_poem(html3)
                            if len(lines3) >= 4:
                                body = ". ".join(lines3[:16])
                                return f"Bài thơ {sub_title}. {body}."
                continue

            lines = _clean_wikisource_poem(html)
            if len(lines) >= 4:
                body = ". ".join(lines[:16])
                return f"Bài thơ {title}. {body}."
    except Exception as e:
        logger.bind(tag=TAG).warning(f"Wikisource lookup error: {e}")
    return ""


async def _fetch_wikipedia_vn(client: httpx.AsyncClient, query: str) -> str:
    try:
        r = await client.get(
            "https://vi.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 2},
            headers=HEADERS,
        )
        hits = (r.json() or {}).get("query", {}).get("search", [])
        if not hits:
            return ""
        title = hits[0].get("title", "")
        r2 = await client.get(
            "https://vi.wikipedia.org/w/api.php",
            params={"action": "query", "prop": "extracts", "explaintext": 1, "exchars": 500, "titles": title, "format": "json"},
            headers=HEADERS,
        )
        pages = (r2.json() or {}).get("query", {}).get("pages", {})
        for p in pages.values():
            ext = (p.get("extract") or "").strip()
            if ext:
                return f"Thông tin chính xác từ Wikipedia ({title}): {ext}"
    except Exception:
        pass
    return ""


async def _fetch_google_news_vn(client: httpx.AsyncClient, query: str) -> str:
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi"
    r = await client.get(url, headers=BROWSER_HEADERS)
    root = ET.fromstring(r.text)
    headlines = []
    for item in root.findall(".//item")[:4]:
        title = (item.findtext("title") or "").strip()
        if title:
            headlines.append(title)
    if not headlines:
        return "Không tìm thấy tin tức mới liên quan."
    return "Kết quả tra cứu thời gian thực mới nhất:\n- " + "\n- ".join(headlines)


@register_function("web_search", WEB_SEARCH_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def web_search(conn: "ConnectionHandler", query: str = ""):
    q_low = (query or "").strip().lower()
    if not q_low:
        return ActionResponse(Action.RESPONSE, "Bạn muốn tra cứu thông tin gì?", "Bạn muốn mình tra cứu thông tin gì nào?")

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            # 1. Petrol prices -> Direct response (0 extra LLM tokens)
            if "xăng" in q_low or "dầu" in q_low or "petrolimex" in q_low or "ron 95" in q_low:
                direct_speech = await _fetch_petrol_price(client)
                if direct_speech:
                    return ActionResponse(action=Action.RESPONSE, result=direct_speech, response=direct_speech)

            # 2. Vietnamese Poems -> Direct exact text from Wikisource VI (0 extra LLM tokens, 100% accurate!)
            if any(k in q_low for k in ("thơ", "đèo ngang", "bếp lửa", "truyện kiều", "tây tiến", "việt bắc", "lượm", "đồng chí")):
                poem_text = await _fetch_poem_wikisource_vn(client, query)
                if poem_text:
                    return ActionResponse(action=Action.RESPONSE, result=poem_text, response=poem_text)

            # 3. Real-time news / gold / football -> Google News RSS Vietnam
            if any(k in q_low for k in ("tin tức", "thời sự", "hôm nay", "mới nhất", "giá vàng", "tỷ giá", "bóng đá")):
                news_context = await _fetch_google_news_vn(client, query)
                return ActionResponse(action=Action.REQLLM, result=news_context, response=None)

            # 4. General knowledge -> Wikipedia VI, fallback to Google News
            wiki_ctx = await _fetch_wikipedia_vn(client, query)
            if wiki_ctx:
                return ActionResponse(action=Action.REQLLM, result=wiki_ctx, response=None)

            news_context = await _fetch_google_news_vn(client, query)
            return ActionResponse(action=Action.REQLLM, result=news_context, response=None)
    except Exception as e:
        logger.bind(tag=TAG).error(f"web_search error: {e}")
        return ActionResponse(
            action=Action.RESPONSE,
            result=str(e),
            response="Xin lỗi, mình chưa tra cứu được thông tin này ngay lúc này.",
        )
