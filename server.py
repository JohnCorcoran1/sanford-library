#!/usr/bin/env python3
"""Local static server and catalog editor for Sanford Library."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import threading
import unicodedata
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "movies.json"
SKINS_PATH = ROOT / "skins.json"
POSTER_DIR = ROOT / "posters"
MAX_REQUEST_BYTES = 3 * 1024 * 1024
MAX_POSTER_BYTES = 2 * 1024 * 1024
CATALOG_LOCK = threading.Lock()
SKINS_LOCK = threading.Lock()
JPEG_DATA_PREFIX = "data:image/jpeg;base64,"
SKIN_COLOR_FIELDS = (
    "background",
    "surface",
    "surfaceAlt",
    "text",
    "muted",
    "border",
    "accent",
    "accentText",
    "dialogBackdrop",
    "scrollbarTrack",
    "scrollbarThumb",
)
SKIN_OPTION_FIELDS = {
    "fontFamily": {"system", "serif", "monospace", "rounded"},
    "shadowStyle": {"none", "soft", "deep"},
    "backgroundSize": {"cover", "contain", "auto"},
    "backgroundPosition": {"center", "top", "bottom"},
    "backgroundAttachment": {"scroll", "fixed"},
}
SKIN_NUMBER_FIELDS = {
    "fontSize": (12, 20),
    "fontWeight": (300, 800),
    "spacing": (8, 32),
    "contentWidth": (900, 2400),
    "controlHeight": (30, 56),
    "posterSize": (120, 320),
    "gridGap": (4, 40),
    "cornerRadius": (0, 24),
    "backdropOpacity": (0, 100),
    "scrollbarWidth": (6, 20),
}
SKIN_URL_FIELDS = ("headerImage", "familyIcon", "backgroundImage")


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding existing environment variables."""
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


load_dotenv(ROOT / ".env")
TMDB_READ_TOKEN = os.environ.get("TMDB_READ_TOKEN", "").strip()
TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
MAX_TMDB_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TMDB_POSTER_BYTES = 4 * 1024 * 1024
SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


class RequestError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def clean_text(value: Any, field: str, *, required: bool, maximum: int = 200) -> str:
    if not isinstance(value, str):
        raise RequestError(HTTPStatus.BAD_REQUEST, f"{field} must be text.")
    value = value.strip()
    if required and not value:
        raise RequestError(HTTPStatus.BAD_REQUEST, f"{field} is required.")
    if len(value) > maximum:
        raise RequestError(HTTPStatus.BAD_REQUEST, f"{field} is too long.")
    return value


def slugify(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-") or "title"


def tmdb_request(path: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    if not TMDB_READ_TOKEN:
        raise RequestError(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "TMDB search is not configured. Set TMDB_READ_TOKEN before starting the server.",
        )
    query = f"?{urlencode(parameters)}" if parameters else ""
    request = Request(
        f"{TMDB_API_BASE}{path}{query}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {TMDB_READ_TOKEN}",
            "User-Agent": "Sanford-Library/1.0",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            data = response.read(MAX_TMDB_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code == HTTPStatus.UNAUTHORIZED:
            message = "TMDB rejected the configured read token."
        elif error.code == HTTPStatus.TOO_MANY_REQUESTS:
            message = "TMDB is temporarily rate limiting requests."
        else:
            message = "TMDB could not complete the request."
        raise RequestError(HTTPStatus.BAD_GATEWAY, message) from error
    except (URLError, TimeoutError) as error:
        raise RequestError(HTTPStatus.BAD_GATEWAY, "TMDB is currently unreachable.") from error

    if len(data) > MAX_TMDB_RESPONSE_BYTES:
        raise RequestError(HTTPStatus.BAD_GATEWAY, "TMDB returned an unexpectedly large response.")
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RequestError(HTTPStatus.BAD_GATEWAY, "TMDB returned an invalid response.") from error
    if not isinstance(payload, dict):
        raise RequestError(HTTPStatus.BAD_GATEWAY, "TMDB returned an invalid response.")
    return payload


def tmdb_poster_path(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_-]+\.(?:jpe?g|png|webp)", value):
        return None
    return value


def tmdb_search(query: str) -> list[dict[str, Any]]:
    query = query.strip()
    if len(query) < 2:
        raise RequestError(HTTPStatus.BAD_REQUEST, "Enter at least two characters to search TMDB.")
    if len(query) > 100:
        raise RequestError(HTTPStatus.BAD_REQUEST, "The TMDB search is too long.")
    payload = tmdb_request(
        "/search/multi",
        {"query": query, "include_adult": "false", "language": "en-US", "page": 1},
    )
    results: list[dict[str, Any]] = []
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        return results
    for item in raw_results:
        if not isinstance(item, dict) or item.get("media_type") not in {"movie", "tv"}:
            continue
        tmdb_id = item.get("id")
        if not isinstance(tmdb_id, int) or isinstance(tmdb_id, bool) or tmdb_id <= 0:
            continue
        media_type = item["media_type"]
        title = item.get("title") if media_type == "movie" else item.get("name")
        date = item.get("release_date") if media_type == "movie" else item.get("first_air_date")
        if not isinstance(title, str) or not title.strip():
            continue
        results.append(
            {
                "id": tmdb_id,
                "mediaType": media_type,
                "title": title.strip(),
                "category": "Movie" if media_type == "movie" else "Series",
                "year": date[:4] if isinstance(date, str) and len(date) >= 4 else "",
                "overview": item.get("overview") if isinstance(item.get("overview"), str) else "",
                "posterPath": tmdb_poster_path(item.get("poster_path")),
            }
        )
        if len(results) == 12:
            break
    return results


def format_movie_runtime(minutes: Any) -> str:
    if not isinstance(minutes, int) or minutes <= 0:
        return ""
    hours, remaining = divmod(minutes, 60)
    if hours and remaining:
        return f"{hours}h {remaining}m"
    if hours:
        return f"{hours}h"
    return f"{remaining}m"


def tmdb_title_details(media_type: str, tmdb_id: int) -> dict[str, Any]:
    if media_type not in {"movie", "tv"}:
        raise RequestError(HTTPStatus.BAD_REQUEST, "TMDB media type must be movie or tv.")
    if tmdb_id <= 0:
        raise RequestError(HTTPStatus.BAD_REQUEST, "TMDB title ID is invalid.")
    payload = tmdb_request(f"/{media_type}/{tmdb_id}", {"language": "en-US"})
    if media_type == "movie":
        title = payload.get("title")
        date = payload.get("release_date")
        duration = format_movie_runtime(payload.get("runtime"))
        category = "Movie"
    else:
        title = payload.get("name")
        date = payload.get("first_air_date")
        episode_count = payload.get("number_of_episodes")
        episode_runtimes = payload.get("episode_run_time")
        episode_runtime = next(
            (value for value in episode_runtimes if isinstance(value, int) and value > 0),
            None,
        ) if isinstance(episode_runtimes, list) else None
        if isinstance(episode_count, int) and episode_count > 0 and episode_runtime:
            duration = f"{episode_count}ep x {episode_runtime} min"
        else:
            season_count = payload.get("number_of_seasons")
            duration = f"{season_count} season{'s' if season_count != 1 else ''}" if isinstance(season_count, int) and season_count > 0 else ""
        category = "Series"
    if not isinstance(title, str) or not title.strip():
        raise RequestError(HTTPStatus.BAD_GATEWAY, "TMDB returned an incomplete title record.")
    return {
        "id": tmdb_id,
        "mediaType": media_type,
        "title": title.strip(),
        "category": category,
        "year": date[:4] if isinstance(date, str) and len(date) >= 4 else "",
        "duration": duration,
        "overview": payload.get("overview") if isinstance(payload.get("overview"), str) else "",
        "posterPath": tmdb_poster_path(payload.get("poster_path")),
    }


def fetch_tmdb_poster(path: str) -> tuple[bytes, str]:
    poster_path = tmdb_poster_path(path)
    if not poster_path:
        raise RequestError(HTTPStatus.BAD_REQUEST, "TMDB poster path is invalid.")
    request = Request(
        f"{TMDB_IMAGE_BASE}{quote(poster_path, safe='/._-')}",
        headers={"Accept": "image/*", "User-Agent": "Sanford-Library/1.0"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            content_type = response.headers.get_content_type()
            data = response.read(MAX_TMDB_POSTER_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RequestError(HTTPStatus.BAD_GATEWAY, "The TMDB poster could not be downloaded.") from error
    if not content_type.startswith("image/") or not data or len(data) > MAX_TMDB_POSTER_BYTES:
        raise RequestError(HTTPStatus.BAD_GATEWAY, "TMDB returned an invalid poster.")
    return data, content_type


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\xff\xd8"):
        raise RequestError(HTTPStatus.BAD_REQUEST, "The poster must be a JPEG image.")

    position = 2
    while position < len(data):
        while position < len(data) and data[position] != 0xFF:
            position += 1
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break

        marker = data[position]
        position += 1
        if marker in {0xD8, 0xD9}:
            continue
        if position + 2 > len(data):
            break

        segment_length = int.from_bytes(data[position : position + 2], "big")
        if segment_length < 2 or position + segment_length > len(data):
            break
        if marker in SOF_MARKERS:
            if segment_length < 7:
                break
            height = int.from_bytes(data[position + 3 : position + 5], "big")
            width = int.from_bytes(data[position + 5 : position + 7], "big")
            if width and height:
                return width, height
            break
        position += segment_length

    raise RequestError(HTTPStatus.BAD_REQUEST, "The poster JPEG is invalid.")


def decode_poster(value: Any) -> tuple[bytes, int, int]:
    if not isinstance(value, str) or not value.startswith(JPEG_DATA_PREFIX):
        raise RequestError(HTTPStatus.BAD_REQUEST, "An optimized JPEG poster is required.")
    try:
        poster = base64.b64decode(value[len(JPEG_DATA_PREFIX) :], validate=True)
    except (binascii.Error, ValueError) as error:
        raise RequestError(HTTPStatus.BAD_REQUEST, "The poster data is invalid.") from error
    if not poster or len(poster) > MAX_POSTER_BYTES:
        raise RequestError(HTTPStatus.BAD_REQUEST, "The poster is too large.")
    width, height = jpeg_dimensions(poster)
    if width > 640 or height > 960:
        raise RequestError(HTTPStatus.BAD_REQUEST, "The poster exceeds 640×960 pixels.")
    return poster, width, height


def load_catalog() -> list[dict[str, Any]]:
    try:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "The catalog could not be read.") from error
    if not isinstance(catalog, list):
        raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "The catalog has an invalid format.")
    return catalog


def load_skins() -> list[dict[str, str]]:
    if not SKINS_PATH.exists():
        return []
    try:
        skins = json.loads(SKINS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "The custom skins could not be read.") from error
    if not isinstance(skins, list):
        raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "The custom skins have an invalid format.")
    return skins


def save_skins(skins: list[dict[str, str]]) -> None:
    temporary_path = SKINS_PATH.with_suffix(".json.tmp")
    try:
        temporary_path.write_text(
            json.dumps(skins, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary_path, SKINS_PATH)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "The custom skins could not be saved.") from error


def add_skin(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise RequestError(HTTPStatus.BAD_REQUEST, "The request body must be an object.")
    name = clean_text(payload.get("name"), "Skin name", required=True, maximum=40)
    skin: dict[str, str] = {"id": "", "name": name}
    for field in SKIN_COLOR_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise RequestError(HTTPStatus.BAD_REQUEST, f"{field} must be a six-digit hex color.")
        skin[field] = value.lower()
    for field, choices in SKIN_OPTION_FIELDS.items():
        value = payload.get(field)
        if value not in choices:
            raise RequestError(HTTPStatus.BAD_REQUEST, f"{field} has an unsupported value.")
        skin[field] = value
    for field, (minimum, maximum) in SKIN_NUMBER_FIELDS.items():
        value = payload.get(field)
        if not isinstance(value, str) or not value.isdigit() or not minimum <= int(value) <= maximum:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                f"{field} must be a whole number from {minimum} to {maximum}.",
            )
        skin[field] = value
    for field in SKIN_URL_FIELDS:
        value = payload.get(field, "")
        if not isinstance(value, str) or len(value) > 500 or any(character in value for character in "\r\n<>"):
            raise RequestError(HTTPStatus.BAD_REQUEST, f"{field} must be a valid image URL or path.")
        if value and not re.fullmatch(r"(?:https?://|/|\.\.?/)?[A-Za-z0-9_%+.,@:/?#=&~-]+", value):
            raise RequestError(HTTPStatus.BAD_REQUEST, f"{field} must be a valid image URL or path.")
        skin[field] = value

    with SKINS_LOCK:
        skins = load_skins()
        if any(item.get("name", "").casefold() == name.casefold() for item in skins):
            raise RequestError(HTTPStatus.CONFLICT, "A skin with this name already exists.")
        base_id = slugify(name)
        skin_id = base_id
        suffix = 2
        existing_ids = {item.get("id") for item in skins}
        while skin_id in existing_ids:
            skin_id = f"{base_id}-{suffix}"
            suffix += 1
        skin["id"] = skin_id
        skins.append(skin)
        save_skins(skins)
    return skin


def delete_skin(skin_id: str) -> None:
    with SKINS_LOCK:
        skins = load_skins()
        remaining = [skin for skin in skins if skin.get("id") != skin_id]
        if len(remaining) == len(skins):
            raise RequestError(HTTPStatus.NOT_FOUND, "The custom skin was not found.")
        save_skins(remaining)


def unique_poster_path(title: str, order: int) -> Path:
    base = slugify(title)
    candidate = POSTER_DIR / f"{base}.jpg"
    if not candidate.exists():
        return candidate
    candidate = POSTER_DIR / f"{base}-{order}.jpg"
    suffix = 2
    while candidate.exists():
        candidate = POSTER_DIR / f"{base}-{order}-{suffix}.jpg"
        suffix += 1
    return candidate


def add_movie(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RequestError(HTTPStatus.BAD_REQUEST, "The request body must be an object.")

    title = clean_text(payload.get("title"), "Title", required=True)
    category = clean_text(payload.get("category"), "Category", required=True, maximum=20)
    if category not in {"Movie", "Series"}:
        raise RequestError(HTTPStatus.BAD_REQUEST, "Category must be Movie or Series.")
    movie_format = clean_text(payload.get("format"), "Format", required=True, maximum=100)
    hdr = clean_text(payload.get("hdr", ""), "HDR", required=False, maximum=100)
    audio = clean_text(payload.get("audio"), "Audio", required=True, maximum=100)
    duration = clean_text(payload.get("duration"), "Duration", required=True, maximum=50)
    family = payload.get("family", False)
    if not isinstance(family, bool):
        raise RequestError(HTTPStatus.BAD_REQUEST, "Family approval must be true or false.")
    tmdb_type = payload.get("tmdbType")
    tmdb_id = payload.get("tmdbId")
    if tmdb_type is not None or tmdb_id is not None:
        if (
            tmdb_type not in {"movie", "tv"}
            or not isinstance(tmdb_id, int)
            or isinstance(tmdb_id, bool)
            or tmdb_id <= 0
        ):
            raise RequestError(HTTPStatus.BAD_REQUEST, "TMDB title information is invalid.")
    poster, width, height = decode_poster(payload.get("posterData"))

    with CATALOG_LOCK:
        catalog = load_catalog()
        if any(str(item.get("title", "")).casefold() == title.casefold() for item in catalog):
            raise RequestError(HTTPStatus.CONFLICT, "A title with this name already exists.")
        if tmdb_id is not None and any(
            item.get("tmdbId") == tmdb_id and item.get("tmdbType") == tmdb_type for item in catalog
        ):
            raise RequestError(HTTPStatus.CONFLICT, "This TMDB title is already in the library.")

        order = max((int(item.get("order", 0)) for item in catalog), default=0) + 1
        poster_path = unique_poster_path(title, order)
        relative_poster = f"posters/{poster_path.name}"
        movie = {
            "title": title,
            "category": category,
            "format": movie_format,
            "hdr": hdr,
            "audio": audio,
            "family": family,
            "order": order,
            "duration": duration,
            "poster": relative_poster,
            "posterAlt": title,
            "posterWidth": width,
            "posterHeight": height,
        }
        if tmdb_id is not None:
            movie["tmdbId"] = tmdb_id
            movie["tmdbType"] = tmdb_type
        catalog.append(movie)

        POSTER_DIR.mkdir(parents=True, exist_ok=True)
        poster_temp = poster_path.with_suffix(".jpg.tmp")
        catalog_temp = CATALOG_PATH.with_suffix(".json.tmp")
        try:
            poster_temp.write_bytes(poster)
            catalog_temp.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            os.replace(poster_temp, poster_path)
            os.replace(catalog_temp, CATALOG_PATH)
        except OSError as error:
            poster_temp.unlink(missing_ok=True)
            catalog_temp.unlink(missing_ok=True)
            poster_path.unlink(missing_ok=True)
            raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "The title could not be saved.") from error

    return movie


class SanfordLibraryHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        request_path = urlparse(self.path).path
        if request_path in {"/", "/index.html", "/movies.json"} or request_path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/status":
                self.send_json(
                    HTTPStatus.OK,
                    {"catalogEditing": True, "tmdbConfigured": bool(TMDB_READ_TOKEN)},
                )
                return
            if parsed.path == "/api/skins":
                self.send_json(HTTPStatus.OK, {"skins": load_skins()})
                return
            if parsed.path == "/api/tmdb/search":
                query = parse_qs(parsed.query).get("q", [""])[0]
                self.send_json(HTTPStatus.OK, {"results": tmdb_search(query)})
                return
            detail_match = re.fullmatch(r"/api/tmdb/title/(movie|tv)/(\d+)", parsed.path)
            if detail_match:
                media_type, tmdb_id = detail_match.groups()
                self.send_json(
                    HTTPStatus.OK,
                    {"title": tmdb_title_details(media_type, int(tmdb_id))},
                )
                return
            if parsed.path == "/api/tmdb/poster":
                poster_path = parse_qs(parsed.query).get("path", [""])[0]
                body, content_type = fetch_tmdb_poster(poster_path)
                self.send_bytes(HTTPStatus.OK, body, content_type)
                return
            super().do_GET()
        except RequestError as error:
            self.send_json(error.status, {"error": str(error)})

    def do_POST(self) -> None:
        request_path = urlparse(self.path).path
        if request_path not in {"/api/movies", "/api/skins"}:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        try:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise RequestError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json.")
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise RequestError(HTTPStatus.BAD_REQUEST, "Content-Length is invalid.") from error
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The request is too large.")
            try:
                payload = json.loads(self.rfile.read(content_length))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RequestError(HTTPStatus.BAD_REQUEST, "The request contains invalid JSON.") from error
            if request_path == "/api/skins":
                skin = add_skin(payload)
                self.send_json(HTTPStatus.CREATED, {"skin": skin})
            else:
                movie = add_movie(payload)
                self.send_json(HTTPStatus.CREATED, {"movie": movie})
        except RequestError as error:
            self.send_json(error.status, {"error": str(error)})

    def do_DELETE(self) -> None:
        request_path = urlparse(self.path).path
        match = re.fullmatch(r"/api/skins/([a-z0-9-]+)", request_path)
        if not match:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        try:
            delete_skin(match.group(1))
            self.send_json(HTTPStatus.OK, {"deleted": True})
        except RequestError as error:
            self.send_json(error.status, {"error": str(error)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sanford Library locally with catalog editing enabled.")
    parser.add_argument("--port", type=int, default=8000, help="Local port (default: 8000)")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), SanfordLibraryHandler)
    print(f"Sanford Library is running at http://127.0.0.1:{args.port}")
    print(f"TMDB search is {'enabled' if TMDB_READ_TOKEN else 'disabled (set TMDB_READ_TOKEN to enable)'}.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
