#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

import requests
import yaml
from icalendar import Calendar
from PIL import Image, ImageDraw, ImageFont

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = APP_DIR / "config.yaml"
DEFAULT_CACHE_PATH = APP_DIR / "cache" / "last.png"
DEFAULT_LOG_PATH = APP_DIR / "logs" / "inky_calendar.log"


@dataclasses.dataclass(frozen=True)
class CalendarConfig:
    name: str
    url: str
    color: str


@dataclasses.dataclass(frozen=True)
class AppConfig:
    timezone: str
    calendars: List[CalendarConfig]
    background_color: str
    grid_color: str
    text_color: str
    font_path: Optional[str]
    title_font_size: int
    body_font_size: int
    footer_font_size: int
    hour_start: int
    hour_end: int
    column_gap: int
    margin: int
    request_timeout: int
    cache_path: str
    log_path: str
    render_width: int
    render_height: int
    inky_color: str


@dataclasses.dataclass(frozen=True)
class Event:
    title: str
    start: datetime
    end: datetime
    calendar_name: str
    color: str


def load_config(path: Path) -> AppConfig:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    calendars: List[CalendarConfig] = []
    for index, entry in enumerate(raw.get("calendars", []), start=1):
        name = entry.get("name")
        url = entry.get("url")
        color = entry.get("color")
        if not name or not url:
            raise ValueError(
                "Each calendar entry must include both 'name' and 'url'. "
                f"Check entry #{index} in {path}."
            )
        if not color:
            raise ValueError(
                "Each calendar entry must include a 'color' value (hex or named color). "
                f"Check entry #{index} in {path}."
            )
        calendars.append(CalendarConfig(name=name, url=url, color=color))

    return AppConfig(
        timezone=raw.get("timezone", "UTC"),
        calendars=calendars,
        background_color=raw.get("background_color", "white"),
        grid_color=raw.get("grid_color", "lightgray"),
        text_color=raw.get("text_color", "black"),
        font_path=raw.get("font_path"),
        title_font_size=int(raw.get("title_font_size", 28)),
        body_font_size=int(raw.get("body_font_size", 20)),
        footer_font_size=int(raw.get("footer_font_size", 16)),
        hour_start=int(raw.get("hour_start", 7)),
        hour_end=int(raw.get("hour_end", 22)),
        column_gap=int(raw.get("column_gap", 20)),
        margin=int(raw.get("margin", 24)),
        request_timeout=int(raw.get("request_timeout", 15)),
        cache_path=raw.get("cache_path", str(DEFAULT_CACHE_PATH)),
        log_path=raw.get("log_path", str(DEFAULT_LOG_PATH)),
        render_width=int(raw.get("render_width", 1600)),
        render_height=int(raw.get("render_height", 1200)),
        inky_color=raw.get("inky_color", "multi"),
    )


def ensure_directories(config: AppConfig) -> None:
    cache_path = Path(config.cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = Path(config.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)


def configure_logging(config: AppConfig) -> None:
    logging.basicConfig(
        filename=config.log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def fetch_calendar(config: CalendarConfig, timeout: int) -> Calendar:
    response = requests.get(config.url, timeout=timeout)
    response.raise_for_status()
    return Calendar.from_ical(response.text)


def normalize_datetime(value, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=tz)
    raise ValueError(f"Unsupported datetime value: {value!r}")


def event_overlaps_day(event_start: datetime, event_end: datetime, day: date) -> bool:
    day_start = datetime.combine(day, time.min, tzinfo=event_start.tzinfo)
    day_end = datetime.combine(day, time.max, tzinfo=event_start.tzinfo)
    return event_start <= day_end and event_end >= day_start


def parse_events(
    calendar: Calendar,
    config: CalendarConfig,
    tz: ZoneInfo,
    days: Iterable[date],
) -> List[Event]:
    events: List[Event] = []
    day_list = list(days)
    for component in calendar.walk():
        if component.name != "VEVENT":
            continue
        start_component = component.get("dtstart")
        if start_component is None:
            continue
        start_raw = start_component.dt
        start = normalize_datetime(start_raw, tz)
        end_component = component.get("dtend")
        if end_component is not None:
            end = normalize_datetime(end_component.dt, tz)
        else:
            duration = component.get("duration")
            if duration is not None:
                end = start + duration.dt
            elif isinstance(start_raw, date) and not isinstance(start_raw, datetime):
                end = start + timedelta(days=1)
            else:
                end = start + timedelta(hours=1)
        if end <= start:
            end = start + timedelta(minutes=1)
        title = str(component.get("summary", "Untitled"))
        for day in day_list:
            if event_overlaps_day(start, end, day):
                events.append(
                    Event(
                        title=title,
                        start=start,
                        end=end,
                        calendar_name=config.name,
                        color=config.color,
                    )
                )
                break
    return events


def load_fonts(config: AppConfig) -> dict[str, ImageFont.FreeTypeFont]:
    if config.font_path:
        font_path = config.font_path
    else:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return {
        "title": ImageFont.truetype(font_path, config.title_font_size),
        "body": ImageFont.truetype(font_path, config.body_font_size),
        "footer": ImageFont.truetype(font_path, config.footer_font_size),
    }


def split_events_by_day(events: Iterable[Event], today: date, tomorrow: date) -> dict[str, List[Event]]:
    today_events: List[Event] = []
    tomorrow_events: List[Event] = []
    for event in events:
        if event_overlaps_day(event.start, event.end, today):
            today_events.append(event)
        elif event_overlaps_day(event.start, event.end, tomorrow):
            tomorrow_events.append(event)
    return {"today": sorted(today_events, key=lambda ev: ev.start), "tomorrow": sorted(tomorrow_events, key=lambda ev: ev.start)}


def draw_grid(
    draw: ImageDraw.ImageDraw,
    config: AppConfig,
    column_left: int,
    column_right: int,
    top: int,
    bottom: int,
    hour_start: int,
    hour_end: int,
    font: ImageFont.FreeTypeFont,
    label_color: str,
) -> None:
    hour_count = max(1, hour_end - hour_start)
    height = bottom - top
    for hour in range(hour_start, hour_end + 1):
        y = top + int((hour - hour_start) / hour_count * height)
        draw.line([(column_left, y), (column_right, y)], fill=config.grid_color, width=1)
        label = f"{hour:02d}:00"
        label_width, label_height = measure_text(draw, label, font)
        draw.text((column_left - label_width - 6, y - label_height / 2), label, fill=label_color, font=font)


def event_to_block(
    event: Event,
    day: date,
    hour_start: int,
    hour_end: int,
    top: int,
    bottom: int,
) -> tuple[int, int]:
    start = event.start
    end = event.end
    day_start = datetime.combine(day, time(hour_start, 0), tzinfo=start.tzinfo)
    day_end = datetime.combine(day, time(hour_end, 0), tzinfo=start.tzinfo)
    start = max(start, day_start)
    end = min(end, day_end)
    total_minutes = (day_end - day_start).total_seconds() / 60
    start_minutes = (start - day_start).total_seconds() / 60
    end_minutes = (end - day_start).total_seconds() / 60
    height = bottom - top
    y1 = top + int((start_minutes / total_minutes) * height)
    y2 = top + int((end_minutes / total_minutes) * height)
    if y2 <= y1 + 6:
        y2 = y1 + 6
    return y1, y2


def render_events(
    draw: ImageDraw.ImageDraw,
    events: Iterable[Event],
    day: date,
    config: AppConfig,
    column_left: int,
    column_right: int,
    top: int,
    bottom: int,
    fonts: dict[str, ImageFont.FreeTypeFont],
) -> None:
    for event in events:
        y1, y2 = event_to_block(event, day, config.hour_start, config.hour_end, top, bottom)
        x1 = column_left + 6
        x2 = column_right - 6
        draw.rectangle([x1, y1, x2, y2], fill=event.color, outline=None)
        draw.rectangle([x1, y1, x1 + 6, y2], fill=config.text_color, outline=None)
        padding = 10
        text_x = x1 + padding + 6
        text_y = y1 + padding
        time_label = f"{event.start.strftime('%H:%M')}–{event.end.strftime('%H:%M')}"
        draw.text((text_x, text_y), time_label, fill=config.text_color, font=fonts["footer"])
        text_y += fonts["footer"].size + 4
        title = event.title
        max_width = x2 - text_x - padding
        draw.multiline_text(
            (text_x, text_y),
            wrap_text(title, fonts["body"], max_width),
            fill=config.text_color,
            font=fonts["body"],
            spacing=4,
        )


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    words = text.split()
    lines: List[str] = []
    current: List[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        width, _ = measure_font_text(candidate, font)
        if width <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def measure_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def measure_font_text(text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def render_calendar(
    config: AppConfig,
    today_events: List[Event],
    tomorrow_events: List[Event],
    now: datetime,
) -> Image.Image:
    image = Image.new("RGB", (config.render_width, config.render_height), color=config.background_color)
    draw = ImageDraw.Draw(image)
    fonts = load_fonts(config)

    margin = config.margin
    column_width = (config.render_width - margin * 2 - config.column_gap) // 2
    left_x = margin + 50
    right_x = left_x + column_width + config.column_gap
    top = margin + fonts["title"].size + 10
    bottom = config.render_height - margin - fonts["footer"].size - 10

    draw.text((left_x, margin), "Today", fill=config.text_color, font=fonts["title"])
    draw.text((right_x, margin), "Tomorrow", fill=config.text_color, font=fonts["title"])

    draw_grid(
        draw,
        config,
        left_x,
        left_x + column_width,
        top,
        bottom,
        config.hour_start,
        config.hour_end,
        fonts["footer"],
        config.text_color,
    )
    draw_grid(
        draw,
        config,
        right_x,
        right_x + column_width,
        top,
        bottom,
        config.hour_start,
        config.hour_end,
        fonts["footer"],
        config.text_color,
    )

    render_events(
        draw,
        today_events,
        now.date(),
        config,
        left_x,
        left_x + column_width,
        top,
        bottom,
        fonts,
    )
    render_events(
        draw,
        tomorrow_events,
        (now + timedelta(days=1)).date(),
        config,
        right_x,
        right_x + column_width,
        top,
        bottom,
        fonts,
    )

    footer_text = f"Last update: {now.strftime('%Y-%m-%d %H:%M')} {config.timezone}"
    footer_width, _ = measure_text(draw, footer_text, fonts["footer"])
    draw.text(
        (config.render_width - margin - footer_width, config.render_height - margin - fonts["footer"].size),
        footer_text,
        fill=config.text_color,
        font=fonts["footer"],
    )

    return image


def push_to_display(config: AppConfig, image: Image.Image) -> None:
    try:
        from inky import Inky
        inky = Inky(config.inky_color)
    except Exception as exc:  # pragma: no cover
        try:
            from inky.auto import auto
        except Exception as auto_exc:  # pragma: no cover
            raise SystemExit(
                "Failed to import Inky. Make sure the inky package is installed in the active "
                "virtual environment, SPI/I2C are enabled, and your Python version is supported. "
                f"Original error: {exc}"
            ) from auto_exc
        else:
            inky = auto()

    target_size = (inky.width, inky.height)
    if image.size != target_size:
        image = image.resize(target_size, Image.LANCZOS)
    inky.set_image(image)
    inky.show()


def save_cache_image(config: AppConfig, image: Image.Image) -> None:
    cache_path = Path(config.cache_path)
    image.save(cache_path)


def build_demo_events(now: datetime, tz: ZoneInfo) -> List[Event]:
    base = datetime.combine(now.date(), time(9, 0), tzinfo=tz)
    demo_events = [
        Event(
            title="Daily sync (Teams)",
            start=base + timedelta(hours=1),
            end=base + timedelta(hours=2),
            calendar_name="Demo A",
            color="#FEE29B",
        ),
        Event(
            title="Project planning",
            start=base + timedelta(hours=3, minutes=30),
            end=base + timedelta(hours=5),
            calendar_name="Demo B",
            color="#CDE7F5",
        ),
        Event(
            title="Customer call",
            start=base + timedelta(hours=8),
            end=base + timedelta(hours=9, minutes=30),
            calendar_name="Demo C",
            color="#D5F5D0",
        ),
        Event(
            title="Evening class",
            start=base + timedelta(hours=10),
            end=base + timedelta(hours=12),
            calendar_name="Demo D",
            color="#F5C9C9",
        ),
    ]
    tomorrow_base = base + timedelta(days=1)
    demo_events.extend(
        [
            Event(
                title="Sprint kickoff",
                start=tomorrow_base + timedelta(hours=1),
                end=tomorrow_base + timedelta(hours=2, minutes=30),
                calendar_name="Demo A",
                color="#FEE29B",
            ),
            Event(
                title="Design review",
                start=tomorrow_base + timedelta(hours=4),
                end=tomorrow_base + timedelta(hours=5),
                calendar_name="Demo B",
                color="#CDE7F5",
            ),
            Event(
                title="Gym",
                start=tomorrow_base + timedelta(hours=9),
                end=tomorrow_base + timedelta(hours=10),
                calendar_name="Demo C",
                color="#D5F5D0",
            ),
        ]
    )
    return demo_events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render calendars to Inky Impression display.")
    parser.add_argument(
        "--config",
        default=os.environ.get("INKY_CONFIG", str(DEFAULT_CONFIG_PATH)),
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="Render image without pushing to the display.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Render a demo image using sample events.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found at {config_path}. Copy config.yaml.example to config.yaml.", file=sys.stderr)
        return 2

    config = load_config(config_path)
    ensure_directories(config)
    configure_logging(config)

    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    events: List[Event] = []
    failed = False
    if args.demo:
        events = build_demo_events(now, tz)
    else:
        for calendar_config in config.calendars:
            try:
                calendar = fetch_calendar(calendar_config, config.request_timeout)
                events.extend(parse_events(calendar, calendar_config, tz, [today, tomorrow]))
            except Exception as exc:  # noqa: BLE001
                logging.exception("Failed to fetch calendar %s: %s", calendar_config.name, exc)
                failed = True

    event_groups = split_events_by_day(events, today, tomorrow)
    image = render_calendar(config, event_groups["today"], event_groups["tomorrow"], now)

    try:
        save_cache_image(config, image)
        if not args.no_display:
            push_to_display(config, image)
        logging.info("Display updated successfully")
    except Exception as exc:  # noqa: BLE001
        logging.exception("Failed to update display: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
