# Remote access via ngrok

Lets the dashboard be opened from a phone (or any browser) away from home. The
app runs on one machine, and an [ngrok](https://ngrok.com) tunnel exposes it on
a public HTTPS URL. Every request must pass a Google login restricted to a
single account before ngrok forwards it, then the dashboard's own password
login as usual.

- No router / firewall changes: the ngrok agent only makes outbound connections.
- Nothing to install on the phone.
- ngrok terminates TLS, so it can see the traffic in transit.

## Running it

```bash
make remote-dashboard
```

Starts the app in non-debug mode, then the tunnel. Open
`https://<your-ngrok-domain>/dashboard`. Ctrl-C stops both.

If the app is already running (in non-debug mode), `make tunnel` starts just
the tunnel.

Notes:

- **Never tunnel the app in debug mode.** the Werkzeug debugger if run is an
  interactive Python console on any error page. `make remote-dashboard` forces
  it off, but stop any already-running dev server first: otherwise the new one
  can't bind the port and the tunnel ends up pointing at the old (debug)
  instance. If running the app by hand, use
  `GARMIN_DASHBOARD_DEBUG=0 ./.venv/bin/python src/app.py`.
- The machine running ngrok & the server must stay awake.
- Avoid the debug panel's "Force update" / sync buttons while remote: they
  assume they're running on the Pi.

## One-time setup

1. **Create an ngrok account** (free plan is enough).

2. **Install the agent and add the authtoken:**

   ```bash
   brew install ngrok
   ngrok config add-authtoken <secret>
   ```

   The token to use is the *secret value* shown once when creating a token in
   the dashboard (Auth Tokens → create).

3. **Find your static domain** in the ngrok dashboard under Domains. Free
   accounts get one random name (e.g. `<words>.ngrok-free.dev`).

4. **Upgrade the agent config to v3** if it's an older version (named endpoints
   need v3). `--relocate` moves it to the current default location and a
   backup is kept:

   ```bash
   ngrok config upgrade 3 --relocate
   ngrok config check          # prints the config file's path
   ```

5. **Add the `garmin` endpoint** to that config file, below the existing
   `agent:` block:

   ```yaml
   endpoints:
       - name: garmin
         url: https://<your-ngrok-domain>
         upstream:
           url: 9329
         traffic_policy:
           on_http_request:
             - actions:
                 - type: oauth
                   config:
                     provider: google
             - expressions:
                 - "actions.ngrok.oauth.identity.email != '<your-google-email>'"
               actions:
                 - type: deny
                   config:
                     status_code: 403
   ```

   Replace `<your-ngrok-domain>` with your domain and `<your-google-email>` with
   your email.

   The `deny` rule is what restricts access to your account — without it,
   *any* Google account gets through the OAuth step. `upstream.url` must match
   the port hardcoded in `src/app.py`. Run `ngrok config check` afterwards.

6. **Verify the lockout** after the first `make remote-dashboard`:
   - On the phone (off wifi), open the URL: ngrok's free-plan warning page →
     Google sign-in → dashboard.
   - In a private window, sign in with a *different* Google account: expect a
     403.
   - `http://localhost:4040` on the host shows each request and its status.
