#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
import threading
import time
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
    rotation: int
    button_pins: List[int]
    button_pull_up: bool


@dataclasses.dataclass(frozen=True)
class Event:
    title: str
    start: datetime
    end: datetime
    calendar_name: str
    color: str
    all_day: bool


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

    rotation = int(raw.get("rotation", 0))
    if rotation % 90 != 0:
        raise ValueError("rotation must be a multiple of 90 degrees (0/90/180/270).")
    rotation = rotation % 360
    button_pins = raw.get("button_pins", [5, 6, 16, 24])
    if not isinstance(button_pins, list) or not all(isinstance(pin, int) for pin in button_pins):
        raise ValueError("button_pins must be a list of GPIO pin numbers (BCM).")

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
        rotation=rotation,
        button_pins=button_pins,
        button_pull_up=bool(raw.get("button_pull_up", True)),
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


def event_overlaps_day(event_start: datetime, event_end: datetime, day: date, all_day: bool = False) -> bool:
    day_start = datetime.combine(day, time.min, tzinfo=event_start.tzinfo)
    day_end = datetime.combine(day, time.max, tzinfo=event_start.tzinfo)
    if all_day:
        event_end = event_end - timedelta(microseconds=1)
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
        all_day = isinstance(start_raw, date) and not isinstance(start_raw, datetime)
        end_component = component.get("dtend")
        if end_component is not None:
            end = normalize_datetime(end_component.dt, tz)
        else:
            duration = component.get("duration")
            if duration is not None:
                end = start + duration.dt
            elif all_day:
                end = start + timedelta(days=1)
            else:
                end = start + timedelta(hours=1)
        if end <= start:
            end = start + timedelta(minutes=1)
        title = str(component.get("summary", "Untitled"))
        if any(event_overlaps_day(start, end, day, all_day=all_day) for day in day_list):
            events.append(
                Event(
                    title=title,
                    start=start,
                    end=end,
                    calendar_name=config.name,
                    color=config.color,
                    all_day=all_day,
                )
            )
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
        if event_overlaps_day(event.start, event.end, today, all_day=event.all_day):
            today_events.append(event)
        if event_overlaps_day(event.start, event.end, tomorrow, all_day=event.all_day):
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
    placements = compute_event_layout(list(events))
    gap = 6
    for event in events:
        y1, y2 = event_to_block(event, day, config.hour_start, config.hour_end, top, bottom)
        y1 += 1
        y2 -= 1
        if y2 <= y1:
            y2 = y1 + 1
        x_min = column_left + 6
        x_max = column_right - 6
        column_index, column_count = placements.get(event, (0, 1))
        total_gap = gap * (column_count - 1)
        available_width = max(1, x_max - x_min - total_gap)
        column_width = max(1, int(available_width / column_count))
        x1 = x_min + column_index * (column_width + gap)
        x2 = min(x_max, x1 + column_width)
        draw.rounded_rectangle([x1, y1, x2, y2], radius=4, fill=event.color, outline=None)
        draw.rounded_rectangle([x1, y1, x1 + 6, y2], radius=4, fill=config.text_color, outline=None)
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


def compute_event_layout(events: List[Event]) -> dict[Event, tuple[int, int]]:
    sorted_events = sorted(events, key=lambda ev: (ev.start, ev.end))
    groups: List[List[Event]] = []
    group: List[Event] = []
    group_end: Optional[datetime] = None
    for event in sorted_events:
        if not group:
            group = [event]
            group_end = event.end
            continue
        if event.start <= (group_end or event.end):
            group.append(event)
            if event.end > (group_end or event.end):
                group_end = event.end
        else:
            groups.append(group)
            group = [event]
            group_end = event.end
    if group:
        groups.append(group)

    placements: dict[Event, tuple[int, int]] = {}
    for group in groups:
        active: List[tuple[datetime, int]] = []
        column_for_event: dict[Event, int] = {}
        max_cols = 1
        for event in sorted(group, key=lambda ev: (ev.start, ev.end)):
            active = [(end, col) for end, col in active if end > event.start]
            active_cols = {col for _, col in active}
            col = 0
            while col in active_cols:
                col += 1
            active.append((event.end, col))
            active.sort(key=lambda item: item[0])
            column_for_event[event] = col
            max_cols = max(max_cols, len(active))
        for event in group:
            placements[event] = (column_for_event[event], max_cols)
    return placements


def render_all_day_events(
    draw: ImageDraw.ImageDraw,
    events: Iterable[Event],
    config: AppConfig,
    column_left: int,
    column_right: int,
    top: int,
    fonts: dict[str, ImageFont.FreeTypeFont],
) -> int:
    padding = 6
    gap = 6
    row_height = fonts["body"].size + padding * 2
    x1 = column_left + 6
    x2 = column_right - 6
    y = top
    for event in events:
        y1 = y
        y2 = y1 + row_height
        draw.rectangle([x1, y1, x2, y2], fill=event.color, outline=None)
        draw.rectangle([x1, y1, x1 + 6, y2], fill=config.text_color, outline=None)
        text_x = x1 + padding + 6
        text_y = y1 + padding
        max_width = x2 - text_x - padding
        label = truncate_text(event.title, fonts["body"], max_width)
        draw.text((text_x, text_y), label, fill=config.text_color, font=fonts["body"])
        y = y2 + gap
    return y - top if events else 0


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


def truncate_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    if measure_font_text(text, font)[0] <= max_width:
        return text
    ellipsis = "…"
    trimmed = text
    while trimmed and measure_font_text(trimmed + ellipsis, font)[0] > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + ellipsis) if trimmed else ellipsis


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


def apply_rotation(image: Image.Image, rotation: int) -> Image.Image:
    if rotation == 0:
        return image
    return image.rotate(rotation, expand=True)


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

    weekday_names = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]
    draw.text((left_x, margin), weekday_names[now.weekday()], fill=config.text_color, font=fonts["title"])
    draw.text(
        (right_x, margin),
        weekday_names[(now + timedelta(days=1)).weekday()],
        fill=config.text_color,
        font=fonts["title"],
    )

    all_day_today = [event for event in today_events if event.all_day]
    all_day_tomorrow = [event for event in tomorrow_events if event.all_day]
    day_today = [event for event in today_events if not event.all_day]
    day_tomorrow = [event for event in tomorrow_events if not event.all_day]

    all_day_height_left = render_all_day_events(
        draw,
        all_day_today,
        config,
        left_x,
        left_x + column_width,
        top,
        fonts,
    )
    all_day_height_right = render_all_day_events(
        draw,
        all_day_tomorrow,
        config,
        right_x,
        right_x + column_width,
        top,
        fonts,
    )
    grid_top = top + max(all_day_height_left, all_day_height_right) + (10 if (all_day_today or all_day_tomorrow) else 0)

    draw_grid(
        draw,
        config,
        left_x,
        left_x + column_width,
        grid_top,
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
        grid_top,
        bottom,
        config.hour_start,
        config.hour_end,
        fonts["footer"],
        config.text_color,
    )

    render_events(
        draw,
        day_today,
        now.date(),
        config,
        left_x,
        left_x + column_width,
        grid_top,
        bottom,
        fonts,
    )
    render_events(
        draw,
        day_tomorrow,
        (now + timedelta(days=1)).date(),
        config,
        right_x,
        right_x + column_width,
        grid_top,
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
    inky = get_inky_display(config)
    target_size = (inky.width, inky.height)
    if image.size != target_size:
        image = image.resize(target_size, Image.LANCZOS)
    inky.set_image(image)
    inky.show()


def get_inky_display(config: AppConfig):
    try:
        from inky import Inky
        return Inky(config.inky_color)
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
            return auto()


def collect_buttons(inky, config: AppConfig) -> list[tuple[str, object]]:
    buttons: list[tuple[str, object]] = []
    if inky is not None:
        candidate_buttons = getattr(inky, "buttons", None)
        if candidate_buttons:
            try:
                for index, button in enumerate(candidate_buttons):
                    buttons.append((f"button_{index + 1}", button))
            except TypeError:
                pass
        if buttons:
            return buttons
        for label in ("a", "b", "c", "d"):
            button = getattr(inky, f"button_{label}", None)
            if button:
                buttons.append((f"button_{label}", button))
        if buttons:
            return buttons
    try:
        from gpiozero import Button
    except Exception:
        return []
    labels = ("a", "b", "c", "d")
    for index, pin in enumerate(config.button_pins):
        label = labels[index] if index < len(labels) else str(index + 1)
        buttons.append((f"button_{label}", Button(pin, pull_up=config.button_pull_up)))
    return buttons


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
            all_day=False,
        ),
        Event(
            title="Project planning",
            start=base + timedelta(hours=3, minutes=30),
            end=base + timedelta(hours=5),
            calendar_name="Demo B",
            color="#CDE7F5",
            all_day=False,
        ),
        Event(
            title="Design pairing",
            start=base + timedelta(hours=3, minutes=45),
            end=base + timedelta(hours=5, minutes=15),
            calendar_name="Demo C",
            color="#D5F5D0",
            all_day=False,
        ),
        Event(
            title="Customer call",
            start=base + timedelta(hours=8),
            end=base + timedelta(hours=9, minutes=30),
            calendar_name="Demo C",
            color="#D5F5D0",
            all_day=False,
        ),
        Event(
            title="Evening class",
            start=base + timedelta(hours=10),
            end=base + timedelta(hours=12),
            calendar_name="Demo D",
            color="#F5C9C9",
            all_day=False,
        ),
        Event(
            title="Company holiday",
            start=base,
            end=base + timedelta(hours=23, minutes=59),
            calendar_name="Demo A",
            color="#FEE29B",
            all_day=True,
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
                all_day=False,
            ),
            Event(
                title="Design review",
                start=tomorrow_base + timedelta(hours=4),
                end=tomorrow_base + timedelta(hours=5),
                calendar_name="Demo B",
                color="#CDE7F5",
                all_day=False,
            ),
            Event(
                title="Gym",
                start=tomorrow_base + timedelta(hours=9),
                end=tomorrow_base + timedelta(hours=10),
                calendar_name="Demo C",
                color="#D5F5D0",
                all_day=False,
            ),
            Event(
                title="All-day workshop",
                start=tomorrow_base,
                end=tomorrow_base + timedelta(hours=23, minutes=59),
                calendar_name="Demo B",
                color="#CDE7F5",
                all_day=True,
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
    parser.add_argument(
        "--listen-buttons",
        action="store_true",
        help="Keep running and update the display when Inky buttons are pressed.",
    )
    return parser.parse_args()

def update_display(config: AppConfig, args: argparse.Namespace) -> int:
    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    events: List[Event] = []
    if args.demo:
        events = build_demo_events(now, tz)
    else:
        for calendar_config in config.calendars:
            try:
                calendar = fetch_calendar(calendar_config, config.request_timeout)
                events.extend(parse_events(calendar, calendar_config, tz, [today, tomorrow]))
            except Exception as exc:  # noqa: BLE001
                logging.exception("Failed to fetch calendar %s: %s", calendar_config.name, exc)

    event_groups = split_events_by_day(events, today, tomorrow)
    image = render_calendar(config, event_groups["today"], event_groups["tomorrow"], now)
    image = apply_rotation(image, config.rotation)

    try:
        save_cache_image(config, image)
        if not args.no_display:
            push_to_display(config, image)
        logging.info("Display updated successfully")
    except Exception as exc:  # noqa: BLE001
        logging.exception("Failed to update display: %s", exc)
        return 1

    return 0


def listen_for_buttons(config: AppConfig, args: argparse.Namespace) -> int:
    inky = None
    try:
        inky = get_inky_display(config)
    except SystemExit as exc:
        logging.warning("Display initialization failed for button listener: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to initialize Inky for button listening: %s", exc)

    update_lock = threading.Lock()
    def wait_for_buttons() -> list[tuple[str, object]]:
        while True:
            detected = collect_buttons(inky, config)
            if detected:
                return detected
            time.sleep(10)

    def handle_press(button_name: str) -> None:
        if not update_lock.acquire(blocking=False):
            logging.info("Skipping button %s press because an update is already running.", button_name)
            return
        try:
            update_display(config, args)
            logging.info("Button %s pressed; display updated.", button_name)
        finally:
            update_lock.release()

    buttons = wait_for_buttons()
    for button_name, button in buttons:
        if hasattr(button, "when_pressed"):
            button.when_pressed = lambda name=button_name: handle_press(name)

    try:
        last_states = {name: False for name, _ in buttons}

        def read_pressed(target) -> Optional[bool]:
            if hasattr(target, "is_pressed"):
                return bool(target.is_pressed)
            if hasattr(target, "value"):
                return bool(target.value)
            if hasattr(target, "read"):
                return bool(target.read())
            return None

        while True:
            for button_name, button in buttons:
                pressed = read_pressed(button)
                if pressed is None:
                    continue
                if pressed and not last_states.get(button_name, False):
                    handle_press(button_name)
                last_states[button_name] = pressed
            time.sleep(0.1)
    except KeyboardInterrupt:
        logging.info("Button listener stopped.")
        return 0


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found at {config_path}. Copy config.yaml.example to config.yaml.", file=sys.stderr)
        return 2

    config = load_config(config_path)
    ensure_directories(config)
    configure_logging(config)

    if args.listen_buttons:
        return listen_for_buttons(config, args)

    return update_display(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
