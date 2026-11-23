import httpx
from typing import TYPE_CHECKING
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

GET_WEATHER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Tra cứu thời tiết thực tế hôm nay và dự báo các ngày tới tại bất kỳ tỉnh/thành phố nào (ví dụ: Hà Nội, TP.HCM, Đà Nẵng...). Luôn gọi công cụ này khi người dùng hỏi về thời tiết, nhiệt độ, mưa nắng.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Tên thành phố hoặc tỉnh (ví dụ: 'Hà Nội', 'Hồ Chí Minh', 'Đà Nẵng'). Mặc định là 'Hà Nội' nếu người dùng không nêu rõ.",
                },
            },
            "required": [],
        },
    },
}

WMO_VI = {
    0: "trời quang đãng",
    1: "trời nắng nhẹ",
    2: "có mây rải rác",
    3: "trời nhiều mây",
    45: "có sương mù",
    48: "sương mù dày",
    51: "mưa phùn nhẹ",
    53: "mưa phùn vừa",
    55: "mưa phùn dày",
    61: "mưa nhỏ",
    63: "mưa vừa",
    65: "mưa to",
    80: "mưa rào nhẹ",
    81: "mưa rào vừa",
    82: "mưa rào lớn",
    95: "có dông",
    96: "dông kèm mưa lớn",
}

VN_CITY_COORDS = {
    "hà nội": ("Hà Nội", 21.0285, 105.8542),
    "ha noi": ("Hà Nội", 21.0285, 105.8542),
    "hồ chí minh": ("TP. Hồ Chí Minh", 10.8231, 106.6297),
    "sài gòn": ("TP. Hồ Chí Minh", 10.8231, 106.6297),
    "tp hcm": ("TP. Hồ Chí Minh", 10.8231, 106.6297),
    "đà nẵng": ("Đà Nẵng", 16.0544, 108.2022),
    "hải phòng": ("Hải Phòng", 20.8449, 106.6881),
    "cần thơ": ("Cần Thơ", 10.0452, 105.7469),
    "huế": ("Huế", 16.4637, 107.5909),
    "nha trang": ("Nha Trang", 12.2388, 109.1967),
    "đà lạt": ("Đà Lạt", 11.9404, 108.4583),
    "vũng tàu": ("Vũng Tàu", 10.3460, 107.0843),
    "비nh dương": ("Bình Dương", 10.9804, 106.6519),
    "đồng nai": ("Đồng Nai", 10.9574, 106.8426),
    "quảng ninh": ("Quảng Ninh", 20.9599, 107.0425),
    "thanh hóa": ("Thanh Hóa", 19.8067, 105.7852),
    "nghệ an": ("Nghệ An", 18.6796, 105.6813),
    "vinh": ("TP. Vinh", 18.6796, 105.6813),
    "bắc ninh": ("Bắc Ninh", 21.1861, 106.0763),
    "hải dương": ("Hải Dương", 20.9373, 106.3146),
    "thái nguyên": ("Thái Nguyên", 21.5942, 105.8482),
    "nam định": ("Nam Định", 20.4388, 106.1621),
    "thái bình": ("Thái Bình", 20.4463, 106.3366),
}


async def _resolve_coords(client: httpx.AsyncClient, location: str):
    loc_clean = (location or "Hà Nội").strip()
    low = loc_clean.lower()
    for k, val in VN_CITY_COORDS.items():
        if k in low:
            return val
    try:
        r = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": loc_clean, "count": 1, "language": "vi", "format": "json"},
        )
        results = (r.json() or {}).get("results") or []
        if results:
            item = results[0]
            return item.get("name", loc_clean), item["latitude"], item["longitude"]
    except Exception:
        pass
    return "Hà Nội", 21.0285, 105.8542


@register_function("get_weather", GET_WEATHER_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def get_weather(conn: "ConnectionHandler", location: str = "Hà Nội", lang: str = "vi"):
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            city_name, lat, lon = await _resolve_coords(client, location)
            r = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "Asia/Bangkok",
                    "forecast_days": 2,
                },
            )
            data = r.json()
            cur = data.get("current", {})
            daily = data.get("daily", {})

            temp = round(cur.get("temperature_2m", 25))
            feels = round(cur.get("apparent_temperature", temp))
            hum = round(cur.get("relative_humidity_2m", 75))
            wind = round(cur.get("wind_speed_10m", 10))
            desc = WMO_VI.get(cur.get("weather_code", 1), "trời mát mẻ")

            max_t = round((daily.get("temperature_2m_max") or [temp])[0])
            min_t = round((daily.get("temperature_2m_min") or [temp])[0])
            rain_prob = (daily.get("precipitation_probability_max") or [0])[0]

            speech = (
                f"Thời tiết tại {city_name} hiện tại là {temp} độ C, {desc}. "
                f"Cảm giác thực tế khoảng {feels} độ, độ ẩm {hum} phần trăm, gió {wind} ki lô mét trên giờ. "
                f"Trong ngày hôm nay nhiệt độ dao động từ {min_t} đến {max_t} độ C, khả năng có mưa là {rain_prob} phần trăm."
            )
            return ActionResponse(action=Action.RESPONSE, result=speech, response=speech)
    except Exception as e:
        logger.bind(tag=TAG).error(f"get_weather error: {e}")
        return ActionResponse(
            action=Action.RESPONSE,
            result=str(e),
            response="Xin lỗi, mình chưa kết nối được đài khí tượng lúc này. Bạn hỏi lại sau giây lát nhé.",
        )
