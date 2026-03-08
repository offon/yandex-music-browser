# Yandex Music Browser for Home Assistant

Адаптированная версия интеграции `hass-yandex-music-browser`.

## Оригинальный проект

- Original: https://github.com/alryaz/hass-yandex-music-browser

## Что изменено в этой версии

- Совместимость с `AlexxIT/YandexStation` (актуальная структура компонента).
- Совместимость с `Home Assistant 2026.1.3`.
- Обновлены устаревшие API Home Assistant:
  - `MediaPlayerEntityFeature` вместо legacy `SUPPORT_*`.
  - enum-типы `MediaType` и `MediaClass` с fallback-обработкой.
  - актуальные типы `HomeAssistant` и сигнатуры setup/unload.
- Обновлены метаданные интеграции:
  - `manifest.json` -> `homeassistant: 2026.1.3`, `version: 0.1.0`.
  - `hacs.json` -> `homeassistant: 2026.1.3`.
- Обновлена зависимость `yandex-music` до `>=2.2.0`.

## Требования

- Home Assistant `2026.1.3` или новее.
- Установленная интеграция `Yandex.Station`:
  - https://github.com/AlexxIT/YandexStation

## Установка

### Через HACS

1. Добавьте этот репозиторий в HACS как `Integration`.
2. Установите интеграцию `Yandex Music Browser`.
3. Перезапустите Home Assistant.
4. Добавьте интеграцию через UI: `Settings -> Devices & Services -> Add Integration`.

### Вручную

1. Скопируйте папку `custom_components/yandex_music_browser` в ваш конфиг Home Assistant.
2. Перезапустите Home Assistant.
3. Добавьте интеграцию через UI.

## Важно

- Для патча `yandex_station` интеграция `Yandex.Station` должна быть уже настроена и авторизована.
- Без доступной авторизации в Yandex Music браузер не сможет получить токен.

## Статус

Проект обновлен под текущие изменения в этом репозитории и предназначен как совместимая адаптация оригинальной работы `alryaz`.
