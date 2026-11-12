# Security Policy

## Reporting a vulnerability

Report it privately through GitHub Security Advisories: open the repository's
**Security** tab and click **Report a vulnerability**. Please do not open a
public issue for a security problem.

## What the service does on your machine

The service runs only the actions declared in your `config.json`. A touch on the
panel sends an action id, not a command. The service runs an id only when it
exists in `actions`; any other id is ignored. Text
from a touch is never turned into a path, argument or command line.

The action types are:

- `media`: play/pause, next, previous
- `key`: presses a virtual key
- `volume`: volume up, down or mute
- `folder`: opens a folder
- `app`: opens an application or protocol
- `url`: opens a URL in the browser
- `command`: runs the command you wrote in `config.json` (`cmd`)
- `display`: adjusts the panel screen brightness
- `rest`: turns rest mode on or off
- `overlay`: turns the clock over the photos on or off

`folder`, `app`, `url` and `command` start programs or open addresses on your
computer. Put in `config.json` only what you want the panel to launch.

## Where the panel token lives

The panel token is not in the repository and not in `config.json`. It is read
from `secrets.json` in your per-user data folder:
`%APPDATA%\mcp-display-companion\secrets.json` on Windows, or
`~/.config/mcp-display-companion/secrets.json` elsewhere. The `PANEL_TOKEN` environment variable takes precedence over the file. Do not commit `secrets.json`, and do not paste the token in
an issue.

## Claude Code permission alerts

- Approving from the panel is off by default: `alerts.allow_approve` is `false`.
- When it is on, approving means holding the button for 1.5 s. Denying is one tap.
- The service accepts an answer only from the panel screen it published, and
  only with the secret (nonce) it sent to that screen for that alert.
- An alert id must match `^[A-Za-z0-9_-]{1,80}$` before the service builds any
  file name from it.
- The hook waits for the panel's answer up to its time limit: 290 s by
  default, changed with `PANEL_COMPANION_ALERT_WAIT`. At the limit it exits with
  no output. It waits only while the service is alive: the service renews a sign
  of life file (`.service-alive`) in the alerts folder while it is connected to the
  phone, and the hook leaves at once, with no output, when the sign is missing or
  older than 20 s (`PANEL_COMPANION_ALIVE_MAX_AGE`). So with the service or the
  phone off, Claude Code shows its normal dialog instead of waiting. The sign is
  not a credential: whoever can write in the alerts folder can already write
  `*.answer` files, which is the trust boundary described in the configuration.

**Warning: with `allow_approve` on, anyone who can reach the panel can approve a
request. There is no PIN and no biometric check.** Keep it off if other people
can touch the panel.

The alert shows the request on the wall. The banner prints the command or the
tool input (up to about 1000 characters for a shell command). If a command
carries a password or a token, anyone looking at the panel can read it, even
with approving off. Turn `alerts.enabled` off if that matters where the panel
hangs.

The alerts folder is also a trust boundary: whoever can write `*.answer` files
there can approve tool calls. Keep it in a private per-user folder, never in a
shared, synced or network folder.
