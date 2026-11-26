# uv + systemd

Runs Douyin Ember straight from a checkout on the host, once a day, with no Docker.

```bash
sudo useradd --system --home /opt/douyin-ember --shell /usr/sbin/nologin douyin-ember
sudo git clone https://github.com/bunizao/douyin-ember.git /opt/douyin-ember
cd /opt/douyin-ember
sudo uv sync --locked
sudo PLAYWRIGHT_BROWSERS_PATH=/opt/douyin-ember/.playwright uv run --locked playwright install --with-deps chromium
sudo cp .env.example .env
sudo cp config.example.json config.json
sudo mkdir -p artifacts
sudo chown -R douyin-ember:douyin-ember artifacts
sudo chown douyin-ember .env config.json && sudo chmod 600 .env config.json
```

Fill in `.env`, `config.json` and the cookie file, then check login and friends without sending:

```bash
sudo -u douyin-ember HEADLESS=true PLAYWRIGHT_BROWSERS_PATH=/opt/douyin-ember/.playwright .venv/bin/python run.py --dry-run
```

Install the units and start the timer (08:00 in the server's time zone; edit `OnCalendar` to change it):

```bash
sudo cp deploy/systemd/douyin-ember.service deploy/systemd/douyin-ember.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now douyin-ember.timer
```

`systemctl list-timers douyin-ember` shows the next run and `journalctl -u douyin-ember` the logs. The service can write only to `artifacts/`, where send history, screenshots and `run.log` live. To update: `sudo git pull && sudo uv sync --locked`.
