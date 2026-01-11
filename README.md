+# Inky Impression Calendar Display
 
+This project renders two-column calendar views (Today + Tomorrow) onto a Pimoroni
+Inky Impression 13.3" display. It fetches events from 4 ICS feeds (Google,
+Outlook, etc.), color-codes each calendar, and refreshes hourly via `systemd`.
+
+## What you need
+
+- Raspberry Pi Zero 2 W (or similar)
+- Pimoroni Inky Impression 13.3" display
+- Raspberry Pi OS (Lite or Desktop)
+- Wi-Fi configured
+- Your private ICS links for Google/Outlook calendars
+
+## 1) Clone the repo on your Pi
+
+```bash
+cd ~
+git clone https://github.com/beznick-ops/Inky.git inky-calendar
+cd inky-calendar
+```
+
+## 2) Install system packages
+
+```bash
+sudo apt update
+sudo apt install -y python3 python3-venv python3-pip git fonts-dejavu-core
+```
+
+## 3) Create and activate a virtual environment
+
+```bash
+python3 -m venv .venv
+source .venv/bin/activate
+```
+
+## 4) Install Python dependencies
+
+```bash
+pip install -r requirements.txt
+```
+
+## 5) Configure your calendars
+
+Copy the example config and edit it:
+
+```bash
+cp config.yaml.example config.yaml
+nano config.yaml
+```
+
+Fill in the `calendars` section with your 4 ICS URLs and choose colors.
+
+## 6) Enable SPI and I2C (required by Inky)
+
+```bash
+sudo raspi-config
+```
+
+1. Interface Options
+2. SPI -> Enable
+3. I2C -> Enable
+4. Reboot the Pi
+
+```bash
+sudo reboot
+```
+
+## 7) Test a manual update
+
+```bash
+cd inky-calendar
+source .venv/bin/activate
+python inky_calendar.py
+```
+
+If everything is wired correctly, the display updates after a short refresh delay.
+
+## 8) Install systemd service (hourly auto-refresh)
+
+Edit the service file if your folder is not `/home/pi/inky-calendar`:
+
+```bash
+nano systemd/inky-calendar.service
+```
+
+Then copy the service + timer:
+
+```bash
+sudo cp systemd/inky-calendar.service /etc/systemd/system/
+sudo cp systemd/inky-calendar.timer /etc/systemd/system/
+sudo systemctl daemon-reload
+sudo systemctl enable --now inky-calendar.timer
+```
+
+Check status:
+
+```bash
+systemctl status inky-calendar.timer
+systemctl status inky-calendar.service
+```
+
+Logs are written to `logs/inky_calendar.log`.
+
+## Troubleshooting
+
+- If the update fails, it will retry on the next hourly run.
+- Confirm your ICS links are valid by opening them in a browser.
+- If you see blank events, check your timezone in `config.yaml`.
+- If fonts look wrong, set `font_path` in `config.yaml` to a local `.ttf`.
+
+## Customizing the style
+
+Edit the following in `config.yaml`:
+
+- `hour_start` / `hour_end` (visible hours)
+- `render_width` / `render_height` (canvas size)
+- `body_font_size`, `title_font_size`, `footer_font_size`
+- `column_gap`, `margin`
+- calendar colors (HEX values recommended)
EOF
)
