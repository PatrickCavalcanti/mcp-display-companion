# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0] - 2026-11-12

### Added

- Home Assistant page: one air conditioner and one light, called straight from the phone.
- Claude Code permission alerts on the panel: deny with a tap; approving from the panel is off by default.
- Panel language: `view.language`, `en` (default) or `pt-BR`.
- Portrait layout: the screen rearranges into a column when the phone is upright.
- `hotkey_toggle` action: a button that holds a key combination until the next tap (Windows). It shows when the keys are held, and the service releases them at startup.
- Claude Code plugin and one-plugin marketplace (`mcp-display-alerts`): install the permission hook with `claude plugin marketplace add PatrickCavalcanti/mcp-display-companion` and `claude plugin install mcp-display-alerts@mcp-display`.

### Changed

- The panel is in English by default. Set `"language": "pt-BR"` in `view` to keep Portuguese.
- Example config labels are in English.

### Fixed

- The permission hook no longer waits up to 290 s when the service or the phone is off: it leaves at once and Claude Code shows its normal dialog. Update the service together with the hook.
- The watchdog no longer restarts the service while the phone is unreachable.
- Browser family detection for Windows paths on Linux.

## [1.0.0] - 2026-10-25

Initial version.
