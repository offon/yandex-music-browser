# Yandex Music Browser for Home Assistant

> Engineering-ready fork of `hass-yandex-music-browser` with compatibility fixes for modern Home Assistant and AlexxIT YandexStation.

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.1.3%2B-41BDF5)](#compatibility)
[![YandexStation](https://img.shields.io/badge/AlexxIT-YandexStation-00A3FF)](https://github.com/AlexxIT/YandexStation)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Original Project

- Upstream repository: https://github.com/alryaz/hass-yandex-music-browser

## Compatibility

- Home Assistant: `2026.1.3+`
- Yandex Station integration: latest `AlexxIT/YandexStation`
- Component type: `custom_component`

## Keywords / Tags

`home-assistant`, `hass`, `custom-component`, `yandex-music`, `yandex-station`, `media-browser`, `media-player`, `music`, `alexxit`, `integration`, `hacs`

## What Is Implemented In This Fork

- Updated HA API usage for 2026.x:
  - `MediaPlayerEntityFeature` instead of legacy `SUPPORT_*`
  - `MediaType` / `MediaClass` handling with enum fallbacks
  - modern `HomeAssistant` typing and setup flow behavior
- Updated patch loading to avoid blocking `import_module` inside event loop.
- Fixed patch import paths and runtime behavior with current YandexStation internals.
- Extended auth fallback chain:
  - active YandexStation runtime sessions
  - YandexStation config entries
  - optional local credentials
- Improved error behavior for media browse failures (clean `BrowseError` instead of opaque websocket failures).

## Installation

### Option 1: HACS

1. Add this repository as `Integration` in HACS.
2. Install `Yandex Music Browser`.
3. Restart Home Assistant.
4. Add the integration from UI:
   - `Settings -> Devices & Services -> Add Integration`

### Option 2: Manual

1. Copy `custom_components/yandex_music_browser` into your HA config folder:
   - `/config/custom_components/yandex_music_browser`
2. Restart Home Assistant.
3. Add integration in UI.

## Requirements

- Installed and configured `YandexStation` integration:
  - https://github.com/AlexxIT/YandexStation
- Valid Yandex authentication context available through YandexStation.

## Troubleshooting

### Browser cannot authenticate

Check in this order:

1. YandexStation integration is loaded and authorized.
2. At least one Yandex station entity is online and available in HA.
3. After updates, Home Assistant was fully restarted.
4. Integration files in `/config/custom_components/yandex_music_browser/` are from the same fork version.

### Still failing

Collect and inspect logs for:

- `custom_components.yandex_music_browser`
- `custom_components.yandex_music_browser.patches.yandex_station`

## Architecture Notes

Main patch points:

- `custom_components/yandex_music_browser/patches/yandex_station.py`
- `custom_components/yandex_music_browser/patches/generic.py`
- `custom_components/yandex_music_browser/default.py`

## Acknowledgements

- Original component author: `alryaz`
- YandexStation ecosystem: `AlexxIT`
- This fork adaptation and refactoring were implemented with AI assistance from **OpenAI Codex (GPT-5)**.

## Status

This repository is maintained as a practical compatibility fork focused on production usability with current Home Assistant builds.
