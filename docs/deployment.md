# Raspberry Pi Dashboard Deployment

## Overview

The Pi runs a Python HTTP server ([src/app.py](../src/app.py)) on port 9329

- **URL:** <http://localhost:9329>

### Update flow

1. Push to GitHub.
2. A cron job on the Pi polls `origin/main`.
3. If the remote is ahead, the Pi pulls and restarts the systemd service.

---

## Systemd Service

**File:** `/etc/systemd/system/garmin.service`

```ini
[Unit]
Description=Garmin Dashboard server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/temp/sandbox/garmin
ExecStart=/home/pi/temp/sandbox/garmin/scripts/run-server.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`ExecStart` points at a small wrapper script (see below) rather than calling
`python` directly. This is because systemd executes binaries directly with
no shell — it can't `source .envrc` or activate a virtual environment. The
wrapper handles both before exec'ing the Python interpreter.

After editing the unit file, reload systemd and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable garmin.service
sudo systemctl start garmin.service
```

### Common commands

```bash
systemctl status garmin.service
sudo systemctl restart garmin.service
journalctl -u garmin.service -f
```

---

## Wrapper Scripts

Two thin shell wrappers handle the `cd` + `source .envrc` + venv-python
invocation that systemd and cron can't do themselves.

- [scripts/run-server.sh](../scripts/run-server.sh) — used by the systemd
  unit. Sources `.envrc` and exec's `./.venv/bin/python src/app.py`.
- [scripts/run-garmin.sh](../scripts/run-garmin.sh) — used by the daily
  cron job. Pulls latest from GitHub, sources `.envrc`, runs
  `garmin.py --auto`, then pings healthchecks.io.

Both must be executable: `chmod +x scripts/*.sh`.

---

## Cron Job

Edit with `crontab -e`:

```cron
0 5 * * * /home/pi/temp/sandbox/garmin/scripts/run-garmin.sh >> /home/pi/temp/sandbox/garmin/cron.log 2>&1
```

---

## Sudo Config

The `pi` user needs passwordless permission to restart the service, so the
cron-driven update script can call `sudo systemctl restart`.

Edit with `sudo visudo` and add:

```text
pi ALL=NOPASSWD: /usr/bin/systemctl restart --no-block garmin.service, /usr/bin/systemctl restart garmin.service
```

The binary path **must match what `which systemctl` reports** (here
`/usr/bin/systemctl`) and the rule **must list the exact arguments** the
caller uses — sudoers matches the resolved path + argv literally. The
debug-panel "Force update" button runs `force-update.sh` under the systemd
service's minimal `PATH`, where `systemctl` resolves to `/usr/bin/systemctl`
and the command is `restart --no-block garmin.service`. A rule pinned to
`/bin/systemctl` or omitting `--no-block` silently falls through to a password
prompt the detached script can't answer, so the restart never fires. Both the
`--no-block` and bare forms are listed so manual `sudo systemctl restart
garmin.service` keeps working too.

---

## Troubleshooting

**Tail the cron log** (shows daily metric pulls and any failures):

```bash
tail -f /home/pi/temp/sandbox/garmin/cron.log
```

**Check the service status:**

```bash
systemctl status garmin.service
```

**Follow service logs live:**

```bash
journalctl -u garmin.service -f
```

**Confirm the server is responding:**

```bash
curl http://localhost:9329
```
