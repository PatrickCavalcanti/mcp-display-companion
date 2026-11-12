# Notes for AI agents installing this project

This page is for an agent (Claude Code or similar) that installs the companion
for a user. The user-facing overview is in the [README](../README.md).

## What to tell the user after installing

Say it in plain words:

1. **Where the project is.** The folder you cloned (for example
   `C:\...\mcp-display-companion`). Give the real path.
2. **The photos on the panel are examples.** `slides/` inside that folder ships
   with sample photos: landscapes that have nothing to do with the user's life.
3. **Where to put theirs.** Copy their photos into `slides/`, or better, point
   `slides.folder` in `config.json` to a folder of theirs (for example
   `%USERPROFILE%\Pictures\Panel`) and restart the service. The page shows up
   as soon as the folder has one image and disappears when it is empty.
4. Restart the service after editing `config.json`; the photos folder does not
   need a restart (it rescans every `rescan_s`).

## Install checklist

1. Clone, install the Windows extras if on Windows, copy `config.example.json`
   to `config.json` and set `panel.url`.
2. Save the token **outside the repository** with
   `python -m mcp_display_companion --set-token <token>`. Never write the token
   into `config.json`, a log or a message.
3. `python -m mcp_display_companion --check` must print the summary without an
   error.
4. Install the service (`scripts/install-service.ps1` or
   `scripts/install-service.sh`). Starting the companion from inside an agent
   session is what made it die silently: see [service.md](service.md).
5. Do not configure power-off, delete or spend actions without the user asking
   for them ([security notes](configuration.md#security-notes)).
6. Claude Code permission alerts: leave `alerts.allow_approve` at `false` unless
   the user asks for approving from the panel and understands that anyone who can
   touch the panel can then approve
   ([`alerts`](configuration.md#alerts--claude-code-asking-for-permission)).

## Where things are

| Need | File |
|---|---|
| Every config key | [configuration.md](configuration.md) |
| Every action type | [actions.md](actions.md) |
| Running as a service, logs, failures | [service.md](service.md) |
