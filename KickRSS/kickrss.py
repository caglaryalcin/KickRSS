import datetime
import gzip
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import format_datetime

from cachetools import TTLCache, cached
from flask import Flask, abort, request


VERSION = "0.1.1"
CHANNEL_FILTER = re.compile(r"^[0-9a-zA-Z_][0-9a-zA-Z_-]{2,38}$")
RESERVED_CHANNELS = {
    "about",
    "api",
    "categories",
    "category",
    "community-guidelines",
    "dashboard",
    "directory",
    "following",
    "jobs",
    "privacy",
    "search",
    "signin",
    "signup",
    "terms",
}

CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "600"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "10"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

CHANNEL_URL_TEMPLATE = "https://kick.com/api/v2/channels/%s"
VIDEO_URL_TEMPLATES = (
    "https://kick.com/api/v2/channels/%s/videos",
    "https://kick.com/api/v1/channels/%s/videos",
    "https://kick.com/api/v2/channels/%s/previous-livestreams",
)

logging.basicConfig(level=logging.DEBUG if os.environ.get("DEBUG") else logging.INFO)

app = Flask(__name__, static_folder="")


@app.route("/", methods=["GET"])
def index():
    return app.send_static_file("index.html")


@app.route("/healthz", methods=["GET"])
def healthz():
    return {"ok": True, "version": VERSION}


@app.route("/vod/<string:channel>", methods=["GET", "HEAD"])
def vod(channel):
    return get_feed(channel, add_live=True)


@app.route("/vodonly/<string:channel>", methods=["GET", "HEAD"])
def vodonly(channel):
    return get_feed(channel, add_live=False)


def get_feed(channel, add_live=True):
    channel = channel.lower().strip()
    if not is_channel_slug(channel):
        abort(404)

    channel_json = fetch_channel(channel)
    if not isinstance(channel_json, dict):
        abort(404)

    videos_json = fetch_videos(channel)
    items = []
    if add_live:
        live_item = build_live_item(channel, channel_json)
        if live_item is not None:
            items.append(live_item)

    for video in extract_list(videos_json):
        item = build_video_item(channel, video, channel_json)
        if item is not None:
            items.append(item)

    xml = render_rss(channel, channel_json, items, add_live)
    body = xml.encode("utf-8")
    headers = {
        "Content-Type": "application/rss+xml; charset=utf-8",
        "Cache-Control": "public, max-age=300",
    }

    if "gzip" in request.headers.get("Accept-Encoding", ""):
        headers["Content-Encoding"] = "gzip"
        body = gzip.compress(body)

    return body, headers


def is_channel_slug(channel):
    return bool(CHANNEL_FILTER.match(channel)) and channel not in RESERVED_CHANNELS


@cached(cache=TTLCache(maxsize=2000, ttl=CACHE_TTL_SECONDS))
def fetch_channel(channel):
    return fetch_json(CHANNEL_URL_TEMPLATE % urllib.parse.quote(channel))


@cached(cache=TTLCache(maxsize=2000, ttl=CACHE_TTL_SECONDS))
def fetch_videos(channel):
    for template in VIDEO_URL_TEMPLATES:
        payload = fetch_json(template % urllib.parse.quote(channel))
        if extract_list(payload):
            return payload
    return {}


def fetch_json(url):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
        "Referer": "https://kick.com/",
        "Origin": "https://kick.com",
        "User-Agent": f"Mozilla/5.0 (compatible; KickRSS/{VERSION}; +https://github.com/)",
    }
    req = urllib.request.Request(url, headers=headers)
    last_error = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read()
                if response.info().get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                logging.debug("Fetched %s with status %s", url, response.status)
                return json.loads(body.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            logging.warning("Fetch failed for %s: %s", url, exc)
            time.sleep(0.25)
    logging.warning("Fetch failed after retries for %s: %s", url, last_error)
    return None


def extract_list(payload):
    if not isinstance(payload, (dict, list)):
        return []
    if isinstance(payload, list):
        return payload

    candidates = (
        payload.get("data"),
        nested(payload, "videos", "data"),
        payload.get("videos"),
        nested(payload, "previous_livestreams", "data"),
        payload.get("previous_livestreams"),
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return candidate
    return []


def build_live_item(channel, channel_info):
    live = channel_info.get("livestream")
    if not isinstance(live, dict):
        return None

    title = first_string(
        live.get("session_title"),
        live.get("title"),
        "Live on Kick",
    )
    thumbnail = first_string(
        nested(live, "thumbnail", "url"),
        live.get("thumbnail"),
        nested(channel_info, "banner_image", "url"),
        nested(channel_info, "user", "profile_pic"),
    )
    category = first_string(
        nested(live, "categories", 0, "name"),
        nested(live, "category", "name"),
    )
    description = "Live stream"
    if category:
        description += f" - {category}"
    if live.get("viewer_count") is not None:
        description += f" - {int(live.get('viewer_count', 0))} viewers"

    live_id = first_string(live.get("id"), live.get("created_at"), str(int(time.time())))
    return {
        "title": f"LIVE: {title}",
        "link": f"https://kick.com/{urllib.parse.quote(channel)}",
        "guid": f"kick-live-{channel}-{live_id}",
        "pub_date": parse_date(first_string(live.get("created_at"), live.get("start_time"))),
        "description": description_html(description, thumbnail),
        "thumbnail": thumbnail,
        "author": channel_name(channel, channel_info),
        "category": "live",
    }


def build_video_item(channel, item, channel_info):
    if not isinstance(item, dict):
        return None
    livestream = item.get("livestream") if isinstance(item.get("livestream"), dict) else {}
    video = item.get("video") if isinstance(item.get("video"), dict) else {}

    title = first_string(
        item.get("title"),
        item.get("session_title"),
        livestream.get("session_title"),
        video.get("title"),
        "Kick video",
    )
    key = first_string(
        item.get("uuid"),
        video.get("uuid"),
        item.get("slug"),
        video.get("slug"),
        item.get("id"),
        video.get("id"),
    )
    if not key:
        key = str(abs(hash(json.dumps(item, sort_keys=True, default=str))))

    link = first_string(item.get("url"), video.get("url"))
    if not link.startswith("http"):
        link = f"https://kick.com/{urllib.parse.quote(channel)}/videos/{urllib.parse.quote(key)}"

    thumbnail = first_string(
        item.get("thumbnail"),
        item.get("thumb"),
        item.get("thumbnail_url"),
        video.get("thumbnail"),
        nested(livestream, "thumbnail", "url"),
        livestream.get("thumbnail"),
    )
    category = first_string(
        nested(livestream, "categories", 0, "name"),
        nested(livestream, "category", "name"),
    )
    parts = []
    if category:
        parts.append(f"Category: {category}")
    if item.get("views") is not None:
        parts.append(f"Views: {int(item.get('views', 0))}")
    if item.get("duration") is not None:
        parts.append(f"Duration: {format_duration(item.get('duration'))}")

    return {
        "title": title,
        "link": link,
        "guid": f"kick-video-{channel}-{key}",
        "pub_date": parse_date(
            first_string(
                item.get("created_at"),
                item.get("published_at"),
                video.get("created_at"),
                livestream.get("created_at"),
                livestream.get("start_time"),
            )
        ),
        "description": description_html(" - ".join(parts), thumbnail),
        "thumbnail": thumbnail,
        "author": channel_name(channel, channel_info),
        "category": category or "video",
    }


def render_rss(channel, channel_info, items, add_live):
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    ET.register_namespace("media", "http://search.yahoo.com/mrss/")

    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
        },
    )
    channel_el = ET.SubElement(rss, "channel")
    display_name = channel_name(channel, channel_info)
    self_url = public_url(f"/{'vod' if add_live else 'vodonly'}/{channel}")

    sub(channel_el, "title", f"Kick: {display_name}")
    sub(channel_el, "link", f"https://kick.com/{urllib.parse.quote(channel)}")
    sub(channel_el, "description", f"Kick channel feed for {display_name}")
    sub(channel_el, "language", "en")
    sub(channel_el, "lastBuildDate", format_datetime(datetime.datetime.now(datetime.timezone.utc)))
    ET.SubElement(
        channel_el,
        "{http://www.w3.org/2005/Atom}link",
        {"href": self_url, "rel": "self", "type": "application/rss+xml"},
    )

    for feed_item in items:
        item_el = ET.SubElement(channel_el, "item")
        sub(item_el, "title", feed_item["title"])
        sub(item_el, "link", feed_item["link"])
        guid = sub(item_el, "guid", feed_item["guid"])
        guid.set("isPermaLink", "false")
        sub(item_el, "pubDate", format_datetime(feed_item["pub_date"]))
        sub(item_el, "author", feed_item["author"])
        sub(item_el, "category", feed_item["category"])
        if feed_item["thumbnail"]:
            ET.SubElement(
                item_el,
                "{http://search.yahoo.com/mrss/}thumbnail",
                {"url": feed_item["thumbnail"]},
            )
        sub(item_el, "description", feed_item["description"])

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True).decode("utf-8")


def public_url(path):
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}{path}"
    return f"{request.url_root.rstrip('/')}{path}"


def sub(parent, tag, text):
    element = ET.SubElement(parent, tag)
    element.text = "" if text is None else str(text)
    return element


def channel_name(channel, channel_info):
    return first_string(
        nested(channel_info, "user", "username"),
        channel_info.get("slug") if isinstance(channel_info, dict) else None,
        channel,
    )


def description_html(text, thumbnail):
    parts = []
    if thumbnail:
        parts.append(f'<p><img src="{escape_attr(thumbnail)}" alt="" /></p>')
    if text:
        parts.append(f"<p>{escape_text(text)}</p>")
    return "\n".join(parts)


def escape_attr(value):
    return escape_text(value).replace('"', "&quot;")


def escape_text(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def parse_date(value):
    if not value:
        return datetime.datetime.now(datetime.timezone.utc)
    normalized = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc)
    except ValueError:
        return datetime.datetime.now(datetime.timezone.utc)


def first_string(*values):
    for value in values:
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def nested(value, *keys):
    current = value
    for key in keys:
        if isinstance(key, int) and isinstance(current, list) and len(current) > key:
            current = current[key]
        elif isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def format_duration(duration):
    if not isinstance(duration, (int, float)) and not str(duration).isdigit():
        return str(duration)
    seconds = int(duration)
    if seconds > 86400:
        seconds = seconds // 1000
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
