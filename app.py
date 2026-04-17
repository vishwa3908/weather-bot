import os
import threading
from datetime import datetime, timezone, timedelta
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

WEATHER_API_KEY = os.environ["OPENWEATHER_API_KEY"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
DEFAULT_CITY = os.getenv("DEFAULT_CITY", "Delhi")

WEATHER_ICONS = {
    "clear": "☀️",
    "clouds": "☁️",
    "rain": "🌧️",
    "drizzle": "🌦️",
    "thunderstorm": "⛈️",
    "snow": "❄️",
    "mist": "🌫️",
    "fog": "🌫️",
}

AQI_LABELS = {
    1: "Good 🟢",
    2: "Fair 🟡",
    3: "Moderate 🟠",
    4: "Poor 🔴",
    5: "Very Poor 🟣",
}

# Maps IANA timezone → representative city for auto-detect
TIMEZONE_CITY_MAP = {
    "Asia/Kolkata": "Delhi",
    "Asia/Calcutta": "Delhi",
    "America/New_York": "New York",
    "America/Chicago": "Chicago",
    "America/Los_Angeles": "Los Angeles",
    "America/Denver": "Denver",
    "Europe/London": "London",
    "Europe/Paris": "Paris",
    "Europe/Berlin": "Berlin",
    "Asia/Tokyo": "Tokyo",
    "Asia/Shanghai": "Shanghai",
    "Asia/Singapore": "Singapore",
    "Asia/Dubai": "Dubai",
    "Australia/Sydney": "Sydney",
    "America/Sao_Paulo": "Sao Paulo",
}


def get_user_city(user_id: str) -> str:
    """Resolve Slack user's timezone to a city for auto-detection."""
    try:
        resp = requests.get(
            "https://slack.com/api/users.info",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            params={"user": user_id},
            timeout=5,
        )
        data = resp.json()
        tz = data.get("user", {}).get("tz", "")
        return TIMEZONE_CITY_MAP.get(tz, DEFAULT_CITY)
    except Exception:
        return DEFAULT_CITY


def fetch_current_weather(city: str) -> dict:
    url = "https://api.openweathermap.org/data/2.5/weather"
    resp = requests.get(url, params={"q": city, "appid": WEATHER_API_KEY, "units": "metric"}, timeout=5)
    resp.raise_for_status()
    return resp.json()


def fetch_aqi(lat: float, lon: float) -> str:
    url = "http://api.openweathermap.org/data/2.5/air_pollution"
    resp = requests.get(url, params={"lat": lat, "lon": lon, "appid": WEATHER_API_KEY}, timeout=5)
    resp.raise_for_status()
    aqi_index = resp.json()["list"][0]["main"]["aqi"]
    label = AQI_LABELS.get(aqi_index, "Unknown")
    return f"{label} (AQI: {aqi_index})"


def fetch_forecast(city: str) -> list[dict]:
    url = "https://api.openweathermap.org/data/2.5/forecast"
    resp = requests.get(url, params={"q": city, "appid": WEATHER_API_KEY, "units": "metric", "cnt": 40}, timeout=5)
    resp.raise_for_status()
    return resp.json()["list"]


def format_time(unix_ts: int, offset_seconds: int) -> str:
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.strftime("%I:%M %p")


def build_current_summary(data: dict) -> str:
    city = data["name"]
    country = data["sys"]["country"]
    condition = data["weather"][0]["main"].lower()
    description = data["weather"][0]["description"].capitalize()
    temp = round(data["main"]["temp"])
    feels_like = round(data["main"]["feels_like"])
    humidity = data["main"]["humidity"]
    wind_speed = round(data["wind"]["speed"] * 3.6)
    icon = WEATHER_ICONS.get(condition, "🌡️")
    offset = data["timezone"]
    sunrise = format_time(data["sys"]["sunrise"], offset)
    sunset = format_time(data["sys"]["sunset"], offset)

    lat = data["coord"]["lat"]
    lon = data["coord"]["lon"]
    try:
        aqi = fetch_aqi(lat, lon)
    except Exception:
        aqi = "Unavailable"

    return (
        f"{icon} *Today's Weather in {city}, {country}*\n"
        f"• Condition: {description}\n"
        f"• Temperature: {temp}°C (feels like {feels_like}°C)\n"
        f"• Humidity: {humidity}%\n"
        f"• Wind: {wind_speed} km/h\n"
        f"• Sunrise: {sunrise}  |  Sunset: {sunset}\n"
        f"• Air Quality: {aqi}"
    )


def build_forecast_summary(city: str, entries: list[dict]) -> str:
    # One entry per day at ~noon
    seen_dates = {}
    for entry in entries:
        dt = datetime.utcfromtimestamp(entry["dt"])
        date_str = dt.strftime("%a, %d %b")
        hour = dt.hour
        if date_str not in seen_dates or abs(hour - 12) < abs(seen_dates[date_str]["hour"] - 12):
            seen_dates[date_str] = {"hour": hour, "entry": entry}

    lines = [f"📅 *5-Day Forecast for {city}*"]
    for date_str, val in list(seen_dates.items())[:5]:
        e = val["entry"]
        condition = e["weather"][0]["main"].lower()
        desc = e["weather"][0]["description"].capitalize()
        temp_min = round(e["main"]["temp_min"])
        temp_max = round(e["main"]["temp_max"])
        icon = WEATHER_ICONS.get(condition, "🌡️")
        lines.append(f"{icon} *{date_str}* — {desc}, {temp_min}°C – {temp_max}°C")

    return "\n".join(lines)


def handle_weather_async(city: str, user_id: str, mode: str, response_url: str) -> None:
    """Fetch weather data and post result back to Slack."""
    # Auto-detect city from user timezone if not specified
    if not city:
        city = get_user_city(user_id)

    try:
        if mode == "forecast":
            entries = fetch_forecast(city)
            text = build_forecast_summary(city.title(), entries)
        else:
            data = fetch_current_weather(city)
            text = build_current_summary(data)
        payload = {"response_type": "in_channel", "text": text}
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            payload = {"response_type": "ephemeral", "text": f"❌ City *{city}* not found. Try `/weather London`."}
        else:
            payload = {"response_type": "ephemeral", "text": "❌ Weather service error. Please try again."}
    except Exception:
        payload = {"response_type": "ephemeral", "text": "❌ Unexpected error. Please try again."}

    requests.post(response_url, json=payload, timeout=5)


@app.route("/weather", methods=["POST"])
def weather_command():
    text = request.form.get("text", "").strip()
    user_id = request.form.get("user_id", "")
    response_url = request.form.get("response_url")

    # Parse mode and city: "/weather forecast Mumbai" or "/weather Delhi" or "/weather"
    parts = text.split(maxsplit=1)
    if parts and parts[0].lower() == "forecast":
        mode = "forecast"
        city = parts[1] if len(parts) > 1 else ""
    else:
        mode = "current"
        city = text

    thread = threading.Thread(target=handle_weather_async, args=(city, user_id, mode, response_url))
    thread.start()

    label = city if city else "your location"
    return jsonify({"response_type": "ephemeral", "text": f"⏳ Fetching weather for *{label}*..."})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
