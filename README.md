# Inky Impression Calendar Display

This project renders two-column calendar views (Today + Tomorrow) onto a Pimoroni
Inky Impression 13.3" display. It fetches events from 4 ICS feeds (Google,
Outlook, etc.), color-codes each calendar, and refreshes hourly via `systemd`.

## What you need

- Raspberry Pi Zero 2 W (or similar)
- Pimoroni Inky Impression 13.3" display
- Raspberry Pi OS (Lite or Desktop)
- Wi-Fi configured
- Your private ICS links for Google/Outlook calendars

## 1) Clone the repo on your Pi

```bash
cd ~
git clone <YOUR_REPO_URL> inky-calendar
cd inky-calendar
```

## 2) Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git fonts-dejavu-core
```

## 3) Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 4) Install Python dependencies

```bash
pip install -r requirements.txt
```

## 5) Configure your calendars

Copy the example config and edit it:

```bash
cp config.yaml.example config.yaml
nano config.yaml
```

Fill in the `calendars` section with your 4 ICS URLs and choose colors.
Each calendar entry must include `name`, `url`, and `color`.

## 6) Enable SPI and I2C (required by Inky)

```bash
sudo raspi-config
```

1. Interface Options
2. SPI -> Enable
3. I2C -> Enable
4. Reboot the Pi

## 7) Test a manual update

```bash
source .venv/bin/activate
python inky_calendar.py
```

If everything is wired correctly, the display updates after a short refresh delay.

### Optional: render a demo image (no hardware required)

```bash
python inky_calendar.py --demo --no-display
```

This writes `cache/last.png` so you can preview the layout on another machine.

## 8) Install systemd service (hourly auto-refresh)

Edit the service file if your folder is not `/home/inky/inky-calendar` or your user is different:

```bash
nano systemd/inky-calendar.service
```

Then copy the service + timer:

```bash
sudo cp systemd/inky-calendar.service /etc/systemd/system/
sudo cp systemd/inky-calendar.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inky-calendar.timer
```

Check status:

```bash
systemctl status inky-calendar.timer
systemctl status inky-calendar.service
```

Logs are written to `logs/inky_calendar.log`.

## 9) Optional: button-triggered refresh

If you'd like the Inky A/B/C/D buttons to trigger an immediate refresh without affecting
the hourly schedule, enable the optional button listener service:

```bash
sudo cp systemd/inky-calendar-buttons.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inky-calendar-buttons.service
```

The hourly timer keeps running independently.

## Troubleshooting

- If the update fails, it will retry on the next hourly run.
- Confirm your ICS links are valid by opening them in a browser.
- If you see blank events, check your timezone in `config.yaml`.
- If fonts look wrong, set `font_path` in `config.yaml` to a local `.ttf`.
- If the script says it failed to import Inky, run `python -c "from inky import Inky"` in the
  active venv to see the exact error message. This usually points to missing SPI/I2C support
  or a Python version mismatch.
- To check the generated image, run `ls -l cache/last.png` and `python - <<'PY'\nfrom PIL import Image\nprint(Image.open("cache/last.png").size)\nPY`.
- If the button listener reports "No Inky buttons detected", install GPIO support and set
  `button_pins` in your config: `sudo apt install -y python3-gpiozero` (or `pip install gpiozero`).
  The default BCM pins are `[5, 6, 16, 24]` (override if your board uses different pins).
- If you see `ModuleNotFoundError: No module named 'lgpio'`, install it in the venv with
  `pip install lgpio` (or recreate the venv with `python3 -m venv --system-site-packages .venv`
  so `python3-lgpio` is visible).

## Customizing the style

Edit the following in `config.yaml`:

- `hour_start` / `hour_end` (visible hours)
- `render_width` / `render_height` (canvas size)
- `rotation` (0/90/180/270; use 90 for portrait orientation)
- `body_font_size`, `title_font_size`, `footer_font_size`
- `column_gap`, `margin`
- calendar colors (HEX values recommended)
