# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0] - Unreleased

### Added

- Home Assistant page: one air conditioner and one light, called straight from the phone.
- Claude Code permission alerts on the panel: deny with a tap; approving from the panel is off by default.
- Panel language: `view.language`, `en` (default) or `pt-BR`.
- Portrait layout: the screen rearranges into a column when the phone is upright.

### Changed

- A `hotkey_toggle` button shows when its keys are held, and the service releases those keys at startup.
- The panel is in English by default. Set `"language": "pt-BR"` in `view` to keep Portuguese.
- Example config labels are in English.

### Fixed

- The permission hook no longer waits up to 290 s when the service or the phone is off: it leaves at once and Claude Code shows its normal dialog. Update the service together with the hook.
- The watchdog no longer restarts the service while the phone is unreachable.
- Browser family detection for Windows paths on Linux.

## [1.0.0] - 2026-10-25

Initial version.
