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
            "Tra cứu thông tin thực tế mới nhất trên Internet: giá xăng dầu hôm nay, giá vàng hôm nay, "
            "tỷ giá ngoại tệ, tin tức thời sự mới nhất, kết quả bóng đá, sự kiện hiện tại. "
            "BẮT BUỘC gọi công cụ này khi người dùng hỏi về giá xăng, giá vàng hoặc thông tin thực tế theo ngày."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Từ khóa cần tra cứu bằng tiếng Việt (ví dụ: 'giá xăng hôm nay', 'giá vàng SJC hôm nay', 'tin tức mới nhất').",
                }
            },
            "required": ["query"],
        },
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


async def _fetch_petrol_price(client: httpx.AsyncClient) -> str:
    """Fetch live Petrolimex retail fuel prices in Vietnam."""
    try:
        r = await client.get("https://giaxanghomnay.com", headers=HEADERS, follow_redirects=True)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            txt = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            # Extract key prices (e.g. E5 RON 92, RON 95, DO)
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
        r2 = await client.get("https://webtygia.com/gia-xang-dau.html", headers=HEADERS, follow_redirects=True)
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


async def _fetch_google_news_vn(client: httpx.AsyncClient, query: str) -> str:
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi"
    r = await client.get(url, headers=HEADERS)
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
            if "xăng" in q_low or "dầu" in q_low or "petrolimex" in q_low or "ron 95" in q_low:
                direct_speech = await _fetch_petrol_price(client)
                if direct_speech:
                    return ActionResponse(action=Action.RESPONSE, result=direct_speech, response=direct_speech)

            news_context = await _fetch_google_news_vn(client, query)
            return ActionResponse(action=Action.REQLLM, result=news_context, response=None)
    except Exception as e:
        logger.bind(tag=TAG).error(f"web_search error: {e}")
        return ActionResponse(
            action=Action.RESPONSE,
            result=str(e),
            response="Xin lỗi, mình chưa tra cứu được thông tin này ngay lúc này.",
        )
