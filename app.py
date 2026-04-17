import os
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

WEATHER_API_KEY = os.environ["OPENWEATHER_API_KEY"]
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


def fetch_weather(city: str) -> dict:
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": WEATHER_API_KEY,
        "units": "metric",
    }
    response = requests.get(url, params=params, timeout=5)
    response.raise_for_status()
    return response.json()


def build_summary(data: dict) -> str:
    city = data["name"]
    country = data["sys"]["country"]
    condition = data["weather"][0]["main"].lower()
    description = data["weather"][0]["description"].capitalize()
    temp = round(data["main"]["temp"])
    feels_like = round(data["main"]["feels_like"])
    humidity = data["main"]["humidity"]
    wind_speed = round(data["wind"]["speed"] * 3.6)  # m/s → km/h
    icon = WEATHER_ICONS.get(condition, "🌡️")

    return (
        f"{icon} *Today's Weather in {city}, {country}*\n"
        f"• Condition: {description}\n"
        f"• Temperature: {temp}°C (feels like {feels_like}°C)\n"
        f"• Humidity: {humidity}%\n"
        f"• Wind: {wind_speed} km/h"
    )


@app.route("/weather", methods=["POST"])
def weather_command():
    text = request.form.get("text", "").strip()
    city = text if text else DEFAULT_CITY

    try:
        data = fetch_weather(city)
        summary = build_summary(data)
        return jsonify({"response_type": "in_channel", "text": summary})
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return jsonify({
                "response_type": "ephemeral",
                "text": f"❌ City *{city}* not found. Try `/weather London`."
            })
        return jsonify({"response_type": "ephemeral", "text": "❌ Weather service error. Please try again."})
    except Exception:
        return jsonify({"response_type": "ephemeral", "text": "❌ Unexpected error. Please try again."})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
