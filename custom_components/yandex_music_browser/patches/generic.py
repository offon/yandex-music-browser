import logging
import random
import re
import string
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Type, TypeVar, Union
from urllib.parse import quote

from aiohttp.abc import Request
from aiohttp.web_exceptions import HTTPFound
from aiohttp.web_response import Response
from homeassistant.components.http import HomeAssistantView, KEY_HASS
from homeassistant.components.media_player import (
    BrowseError,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaType,
)
from homeassistant.core import HomeAssistant
from yandex_music import Album, Artist, DownloadInfo, Playlist, Track, YandexMusicObject

from custom_components.yandex_music_browser.const import (
    DATA_PLAY_KEY,
    DOMAIN,
    ROOT_MEDIA_CONTENT_TYPE,
)
from custom_components.yandex_music_browser.default import async_get_music_browser
from custom_components.yandex_music_browser.media_browser import (
    YandexBrowseMedia,
    YandexMusicBrowser,
    YandexMusicBrowserAuthenticationError,
    YandexMusicBrowserException,
)
from custom_components.yandex_music_browser.patches._base import _patch_root_async_browse_media

_LOGGER = logging.getLogger(__name__)

MEDIA_TYPE_MUSIC = getattr(getattr(MediaType, "MUSIC", "music"), "value", "music")
MEDIA_TYPE_PLAYLIST = getattr(
    getattr(MediaType, "PLAYLIST", "playlist"), "value", "playlist"
)
_TRACK_CONTEXT_ATTR = "_yandex_track_context"
_TRACK_TITLES_ATTR = "_yandex_track_titles"
_TRACK_CONTEXT_SEPARATOR = "|ctx="
_TRACK_CONTEXT_MAX_ITEMS = 5000


def _get_track_context(self: "MediaPlayerEntity") -> Dict[str, Tuple[List[str], int]]:
    context = getattr(self, _TRACK_CONTEXT_ATTR, None)
    if context is None or not isinstance(context, dict):
        context = {}
        setattr(self, _TRACK_CONTEXT_ATTR, context)
    return context


def _get_track_titles(self: "MediaPlayerEntity") -> Dict[str, str]:
    titles = getattr(self, _TRACK_TITLES_ATTR, None)
    if titles is None or not isinstance(titles, dict):
        titles = {}
        setattr(self, _TRACK_TITLES_ATTR, titles)
    return titles


def _trim_track_context(context: Dict[str, Tuple[List[str], int]]) -> None:
    while len(context) > _TRACK_CONTEXT_MAX_ITEMS:
        context.pop(next(iter(context)))


def _trim_track_titles(titles: Dict[str, str]) -> None:
    while len(titles) > _TRACK_CONTEXT_MAX_ITEMS:
        titles.pop(next(iter(titles)))


def _sanitize_track_filename(value: Optional[str], fallback_track_id: str) -> str:
    if not value:
        return f"track-{fallback_track_id}"

    filename = value.strip()
    if not filename:
        return f"track-{fallback_track_id}"

    filename = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', " ", filename)
    filename = re.sub(r"\s+", " ", filename).strip().replace(" ", "_")
    filename = filename.strip("._")
    if len(filename) > 96:
        filename = filename[:96].rstrip("._")

    return filename or f"track-{fallback_track_id}"


def _build_track_proxy_url(
    hass: HomeAssistant,
    media_type: str,
    media_id: str,
    track_name: Optional[str] = None,
) -> Optional[str]:
    base_url = hass.config.internal_url or hass.config.external_url
    if base_url is None:
        return None

    if track_name:
        filename = quote(_sanitize_track_filename(track_name, media_id))
        track_suffix = "/" + filename + ".mp3"
    else:
        track_suffix = "/track.mp3"

    return (
        base_url
        + YandexMusicBrowserView.url.format(
            key=get_play_key(hass),
            media_type=quote(media_type),
            media_id=quote(media_id),
        )
        + track_suffix
    )


def _remember_track_context_from_browse(
    self: "MediaPlayerEntity", browse_object: Optional[YandexBrowseMedia]
) -> None:
    if browse_object is None:
        return

    context = _get_track_context(self)
    titles = _get_track_titles(self)
    stack = [browse_object]
    while stack:
        current = stack.pop()
        children = list(getattr(current, "children", []) or [])
        if not children:
            continue

        track_ids: List[str] = []
        for child in children:
            child_type = getattr(child, "yandex_media_content_type", None)
            child_id = getattr(child, "yandex_media_content_id", None)
            if child_type == "track" and child_id:
                track_id = str(child_id)
                track_ids.append(track_id)

                track_title = getattr(child, "title", None)
                if isinstance(track_title, str) and track_title.strip():
                    titles[track_id] = track_title.strip()
                    _trim_track_titles(titles)

        if len(track_ids) > 1:
            for i, track_id in enumerate(track_ids):
                context[track_id] = (track_ids, i)
            _trim_track_context(context)

        stack.extend(children)


def _build_context_urls(
    self: "MediaPlayerEntity", track_ids: Sequence[str], start_index: int
) -> Optional[List[str]]:
    titles = _get_track_titles(self)
    urls: List[str] = []
    for track_id in track_ids[start_index:]:
        url = _build_track_proxy_url(
            self.hass,
            media_type="track",
            media_id=track_id,
            track_name=titles.get(track_id),
        )
        if url is None:
            return None
        urls.append(url)
    return urls


def _split_track_media_id(media_id: str) -> Tuple[str, Optional[Tuple[str, str]]]:
    if _TRACK_CONTEXT_SEPARATOR not in media_id:
        return media_id, None

    track_id, context_ref = media_id.split(_TRACK_CONTEXT_SEPARATOR, 1)
    if ":" not in context_ref:
        return track_id, None

    context_type, context_id = context_ref.split(":", 1)
    if not context_type or not context_id:
        return track_id, None

    return track_id, (context_type, context_id)


def _extract_track_ids_from_browse(browse_object: Optional[YandexBrowseMedia]) -> List[str]:
    if browse_object is None:
        return []

    track_ids: List[str] = []
    stack = [browse_object]
    while stack:
        current = stack.pop()
        children = list(getattr(current, "children", []) or [])
        for child in children:
            child_type = getattr(child, "yandex_media_content_type", None)
            child_id = getattr(child, "yandex_media_content_id", None)
            if child_type == "track" and child_id is not None:
                track_ids.append(str(child_id))
        stack.extend(children)

    return track_ids


def _build_track_context_from_album(track: Track) -> Optional[Tuple[List[str], int]]:
    albums = getattr(track, "albums", None) or []
    client = getattr(track, "client", None)
    if client is None:
        return None

    for album_ref in albums:
        album_id = getattr(album_ref, "id", None)
        if album_id is None:
            continue

        try:
            album_list = client.albums(album_ids=str(album_id))
            if not album_list:
                continue

            album: Optional[Album] = album_list[0]
            if album is None:
                continue

            album = album.with_tracks()
            if not getattr(album, "volumes", None):
                continue

            track_ids: List[str] = []
            for volume in album.volumes:
                for item in volume:
                    item_id = getattr(item, "id", None)
                    if item_id is not None:
                        track_ids.append(str(item_id))

            track_id = str(getattr(track, "id", ""))
            if len(track_ids) > 1 and track_id in track_ids:
                return track_ids, track_ids.index(track_id)
        except BaseException:
            continue

    return None


async def _try_play_urls_via_mpd_queue(self: "MediaPlayerEntity", urls: Sequence[str]) -> bool:
    module = getattr(self.__class__, "__module__", "")
    if "homeassistant.components.mpd" not in module:
        return False

    connection = getattr(self, "connection", None)
    client = getattr(self, "_client", None)
    if connection is None or client is None:
        return False

    async with connection():
        await client.clear()
        for url in urls:
            await client.add(url)
        await client.play()

    _LOGGER.debug("Queued %s tracks via MPD direct client queue", len(urls))
    return True


async def _patch_generic_async_play_media(
    self: "MediaPlayerEntity",
    media_type: Optional[str] = None,
    media_id: Optional[str] = None,
    **kwargs,
):
    media_type = getattr(media_type, "value", media_type)
    _LOGGER.debug("Generic async play media call: (%s) (%s) %s", media_type, media_id, kwargs)
    if media_type == "yandex":
        _LOGGER.debug(
            "Yandex Music Browser patched play invoked: entity=%s media_id=%s",
            getattr(self, "entity_id", "<unknown>"),
            media_id,
        )
        media_type, _, media_id = media_id.partition(":")
        track_context_ref: Optional[Tuple[str, str]] = None
        if media_type == "track":
            media_id, track_context_ref = _split_track_media_id(media_id)

        _LOGGER.debug("Willing to play Yandex Media: %s - %s", media_type, media_id)
        browse_object = await _patch_root_async_browse_media(self, media_type, media_id)
        media_object = getattr(browse_object, "media_object", None)

        if media_object:
            if isinstance(media_object, Track):
                context = _get_track_context(self).get(str(media_object.id))
                if context is None and track_context_ref is not None:
                    context_type, context_id = track_context_ref
                    try:
                        context_browse = await _patch_root_async_browse_media(
                            self, context_type, context_id, fetch_children=True
                        )
                        context_track_ids = _extract_track_ids_from_browse(context_browse)
                        track_id = str(media_object.id)
                        if len(context_track_ids) > 1 and track_id in context_track_ids:
                            context = (context_track_ids, context_track_ids.index(track_id))
                            _LOGGER.warning(
                                "Track context restored from media_id: entity=%s type=%s tracks=%s",
                                getattr(self, "entity_id", "<unknown>"),
                                context_type,
                                len(context_track_ids),
                            )
                    except BaseException as e:
                        _LOGGER.debug("Could not restore track context from media_id: %s", e)

                if context is None:
                    context = await self.hass.async_add_executor_job(
                        _build_track_context_from_album, media_object
                    )
                    if context:
                        track_ids, _ = context
                        _LOGGER.warning(
                            "Track context built from album fallback: entity=%s tracks=%s",
                            getattr(self, "entity_id", "<unknown>"),
                            len(track_ids),
                        )

                if context:
                    track_ids, start_index = context
                    context_urls = _build_context_urls(self, track_ids, start_index)
                    if not context_urls:
                        _LOGGER.warning(
                            "Cannot build queue URLs: set internal_url/external_url in HA network settings"
                        )
                    elif len(context_urls) > 1:
                        try:
                            if await _try_play_urls_via_mpd_queue(self, context_urls):
                                _LOGGER.warning(
                                    "Queued track context via MPD direct queue: entity=%s tracks=%s start_index=%s",
                                    getattr(self, "entity_id", "<unknown>"),
                                    len(context_urls),
                                    start_index,
                                )
                                return
                        except BaseException as e:
                            _LOGGER.debug("Could not queue track context via MPD direct queue: %s", e)
                        _LOGGER.warning(
                            "Track context queue fallback to single track: MPD direct queue unsupported for entity=%s",
                            getattr(self, "entity_id", "<unknown>"),
                        )
                else:
                    _LOGGER.warning(
                        "No playlist context for track playback: entity=%s track=%s",
                        getattr(self, "entity_id", "<unknown>"),
                        getattr(media_object, "id", "<unknown>"),
                    )

            # Check if media object is supported for URL generation
            media_object_type = type(media_object)
            if media_object_type in URL_ITEM_VALIDATORS:
                # Retrieve URL parser
                getter, _ = URL_ITEM_VALIDATORS[media_object_type]
                media_id = None
                media_type = MEDIA_TYPE_MUSIC
                if getattr(getter, "_is_urls_container", False):
                    base_url = self.hass.config.internal_url or self.hass.config.external_url
                    if base_url is not None:
                        urls = await self.hass.async_add_executor_job(getter, self.hass, media_object)
                        if isinstance(urls, str):
                            media_id = urls
                        elif urls:
                            urls = list(urls)
                            if len(urls) == 1:
                                media_id = urls[0]
                            else:
                                try:
                                    if await _try_play_urls_via_mpd_queue(self, urls):
                                        _LOGGER.warning(
                                            "Queued playlist via MPD direct queue: entity=%s tracks=%s",
                                            getattr(self, "entity_id", "<unknown>"),
                                            len(urls),
                                        )
                                        return
                                except BaseException as e:
                                    _LOGGER.debug(
                                        "Could not queue playlist URLs via MPD direct queue: %s",
                                        e,
                                    )

                                play_media = object.__getattribute__(self, "async_play_media")
                                enqueue_kwargs = dict(kwargs)
                                try:
                                    await play_media(
                                        media_type=MEDIA_TYPE_MUSIC,
                                        media_id=urls[0],
                                        **kwargs,
                                    )
                                    enqueue_kwargs["enqueue"] = "add"
                                    for url in urls[1:]:
                                        await play_media(
                                            media_type=MEDIA_TYPE_MUSIC,
                                            media_id=url,
                                            **enqueue_kwargs,
                                        )
                                    _LOGGER.warning(
                                        "Queued playlist via enqueue fallback: entity=%s tracks=%s",
                                        getattr(self, "entity_id", "<unknown>"),
                                        len(urls),
                                    )
                                    return
                                except BaseException as e:
                                    _LOGGER.debug(
                                        "Could not queue playlist URLs directly, falling back to m3u8: %s",
                                        e,
                                    )

                        if media_id is None:
                            media_id = (
                                base_url
                                + YandexMusicBrowserView.url.format(
                                    key=get_play_key(self.hass),
                                    media_type=quote(browse_object.yandex_media_content_type),
                                    media_id=quote(browse_object.yandex_media_content_id),
                                )
                                + "/playlist.m3u8"
                            )
                            media_type = MEDIA_TYPE_PLAYLIST

                else:
                    # Allow playback only if no test is provided, or preliminary test succeeds
                    media_id = await self.hass.async_add_executor_job(
                        getter, self.hass, media_object
                    )

                if media_id:
                    # Redirect
                    _LOGGER.debug("Retrieved URL: %s", media_id)
                    return await object.__getattribute__(self, "async_play_media")(
                        media_id=media_id,
                        media_type=media_type,
                        **kwargs,
                    )

        raise YandexMusicBrowserException(
            "could not play unsupported type: %s - %s" % (media_type, media_id)
        )

    return await object.__getattribute__(self, "async_play_media")(
        media_type=media_type, media_id=media_id, **kwargs
    )


async def _patch_generic_async_browse_media(
    self: "MediaPlayerEntity",
    media_content_type: Optional[str] = None,
    media_content_id: Optional[str] = None,
):
    media_content_type = getattr(media_content_type, "value", media_content_type)
    _LOGGER.debug(
        "Generic async browse media call: (%s) (%s)", media_content_type, media_content_id
    )
    yandex_browse_object = None

    if media_content_type == "yandex":
        media_content_type, _, media_content_id = media_content_id.partition(":")
        try:
            yandex_browse_object = await _patch_root_async_browse_media(
                self, media_content_type, media_content_id, fetch_children=True
            )
        except YandexMusicBrowserAuthenticationError as e:
            raise BrowseError(str(e)) from e
        result_object = yandex_browse_object

    else:
        async_browse_media_local = self.__class__.async_browse_media
        result_object = None
        if async_browse_media_local is not _patch_generic_async_browse_media:
            try:
                result_object = await async_browse_media_local(
                    self, media_content_type, media_content_id
                )
            except (NotImplementedError, BrowseError):
                pass

        _root_browse_object_access = getattr(self, "_root_browse_object_access", None)

        if (
            (media_content_type is None or media_content_type == ROOT_MEDIA_CONTENT_TYPE)
            and not media_content_id
        ) or (
            result_object
            and _root_browse_object_access
            and (result_object.media_content_id, result_object.media_content_type)
            == _root_browse_object_access
        ):
            try:
                yandex_browse_object = await _patch_root_async_browse_media(
                    self, None, None, fetch_children=not result_object
                )
            except YandexMusicBrowserAuthenticationError as e:
                raise BrowseError(str(e)) from e
            if result_object:
                self._root_browse_object_access = (
                    result_object.media_content_id,
                    result_object.media_content_type,
                )
                result_object.children = [
                    *(result_object.children or []),
                    yandex_browse_object,
                ]
            else:
                result_object = yandex_browse_object

    if result_object is None:
        raise BrowseError("Could not find required object")

    if yandex_browse_object is not None:
        try:
            music_browser = await async_get_music_browser(self)
        except YandexMusicBrowserAuthenticationError as e:
            raise BrowseError(str(e)) from e

        await self.hass.async_add_executor_job(
            _update_browse_object_for_url,
            self.hass,
            music_browser,
            yandex_browse_object,
        )
        _remember_track_context_from_browse(self, yandex_browse_object)

    return result_object


def _patch_generic_get_attribute(self, attr: str):
    if attr == "supported_features":
        supported_features = object.__getattribute__(self, attr)
        if (
            supported_features is not None
            and supported_features & MediaPlayerEntityFeature.PLAY_MEDIA
        ):
            return supported_features | MediaPlayerEntityFeature.BROWSE_MEDIA
        return supported_features

    elif attr == "async_play_media":
        return _patch_generic_async_play_media.__get__(self, self.__class__)

    elif attr == "async_browse_media":
        return _patch_generic_async_browse_media.__get__(self, self.__class__)

    return object.__getattribute__(self, attr)


#################################################################################
# URL Filtering and processing
#################################################################################


def _update_browse_object_for_url(
    hass: HomeAssistant,
    music_browser: "YandexMusicBrowser",
    browse_object: YandexBrowseMedia,
    parent_track_context: Optional[Tuple[str, str]] = None,
) -> YandexBrowseMedia:
    browse_object.media_content_type = "yandex"
    yandex_type = browse_object.yandex_media_content_type
    yandex_id = browse_object.yandex_media_content_id
    browse_object.media_content_id = yandex_type + ":" + yandex_id

    current_track_context = parent_track_context
    if yandex_type in ("playlist", "album", "user_liked_tracks"):
        current_track_context = (yandex_type, yandex_id)
    elif yandex_type == "track" and parent_track_context is not None:
        context_type, context_id = parent_track_context
        browse_object.media_content_id += (
            _TRACK_CONTEXT_SEPARATOR + context_type + ":" + context_id
        )

    if browse_object.children:
        browse_object.children = list(
            map(
                lambda x: _update_browse_object_for_url(
                    hass, music_browser, x, current_track_context
                ),
                browse_object.children,
            )
        )

    media_object = browse_object.media_object

    can_play = False
    if media_object:
        solver = URL_ITEM_VALIDATORS.get(media_object.__class__)
        if solver:
            url_getter, requires_test = solver
            if requires_test is False:
                can_play = True
            else:
                can_play = bool(url_getter(hass, media_object))

    browse_object.can_play = can_play

    return browse_object


class YandexMusicBrowserView(HomeAssistantView):
    """Handle Yandex Smart Home unauthorized requests."""

    url = "/api/yandex_music_browser/v1.0/{key}/{media_type}/{media_id}"
    extra_urls = [
        url + "/playlist.m3u8",
        url + "/track.mp3",
        url + "/{track_name}.mp3",
    ]
    name = "api:yandex_music_browser"
    requires_auth = False

    async def get(
        self,
        request: Request,
        key: str,
        media_type: str,
        media_id: str,
        track_name: Optional[str] = None,
    ) -> Response:
        """Handle Yandex Smart Home HEAD requests."""
        hass: HomeAssistant = request.app[KEY_HASS]

        # Bind to existence of config within HA data
        if DOMAIN not in hass.data or DATA_PLAY_KEY not in hass.data:
            return Response(status=404, body="no config")

        # Check playback key
        if hass.data[DATA_PLAY_KEY] != key:
            return Response(status=401, body="invalid key")

        # Get browse media object
        try:
            browse_object = await _patch_root_async_browse_media(
                hass, media_type, media_id, fetch_children=False
            )
        except BrowseError as e:
            return Response(status=404, body=str(e))

        media_object = browse_object.media_object
        if media_object is None:
            return Response(status=404, body="no media object")

        validator = URL_ITEM_VALIDATORS.get(media_object.__class__)
        if validator is None:
            return Response(status=404, body="no support")

        url_getter, _ = validator

        urls = await hass.async_add_executor_job(url_getter, hass, media_object)
        if urls is None:
            return Response(status=404, body="no urls")

        if isinstance(urls, str):
            raise HTTPFound(urls)

        m3u8str = "#EXTM3U\n\n"
        for i, url in enumerate(urls, start=1):
            m3u8str += f"#EXTINF:-1,Track {i}\n{url}\n"

        return Response(status=200, body=m3u8str, content_type="application/mpegurl")


_TYandexMusicObject = TypeVar("_TYandexMusicObject", bound=YandexMusicObject)
TURLGetter = Callable[[HomeAssistant, _TYandexMusicObject], Optional[Union[str, Sequence[str]]]]


GET_MEDIA_OBJECT_NAME = {
    Playlist: lambda x: x.title,
    Track: lambda x: f"{x.art} - {x.title}",
    Artist: lambda x: x.name,
}

URL_ITEM_VALIDATORS: Dict[Type[YandexMusicObject], Tuple[TURLGetter, bool]] = {}


def register_url_processor(cls: Type[_TYandexMusicObject], requires_test: bool = True):
    def _wrapper(fn: TURLGetter):
        URL_ITEM_VALIDATORS[cls] = (fn, requires_test)
        return fn

    return _wrapper


def get_play_key(hass: HomeAssistant):
    play_key = hass.data.get(DATA_PLAY_KEY)

    if play_key is None:
        play_key = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(24))
        hass.data[DATA_PLAY_KEY] = play_key

    return play_key


def wrap_urls_container(
    fn: Callable[[HomeAssistant, _TYandexMusicObject], Optional[Sequence[Union[Tuple[str, str], Tuple[str, str, str]]]]]
):
    @wraps(fn)
    def _wrapped(hass: HomeAssistant, media_object: _TYandexMusicObject):
        if hass.config.internal_url is None and hass.config.external_url is None:
            _LOGGER.debug("To use track containers, you must set your Home Assistant internal URL")
            return None

        items = fn(hass, media_object)
        if items is None:
            return None

        urls = []
        for item in items:
            if len(item) == 3:
                type_, id_, track_name = item
            else:
                type_, id_ = item
                track_name = None

            url = _build_track_proxy_url(
                hass=hass,
                media_type=type_,
                media_id=id_,
                track_name=track_name,
            )
            if url is not None:
                urls.append(url)

        return urls

    setattr(_wrapped, "_is_urls_container", True)

    return _wrapped


@register_url_processor(Track, False)
def get_track_play_url(
    hass: HomeAssistant, media_object: Track, codec: str = "mp3", bitrate_in_kbps: int = 192
) -> Optional[Tuple[str, float]]:
    download_info: Optional[List[DownloadInfo]] = media_object.download_info
    if download_info is None:
        download_info = media_object.get_download_info()

    for info in download_info:
        if info.codec == codec and info.bitrate_in_kbps == bitrate_in_kbps:
            direct_link: Optional[str] = info.direct_link
            if direct_link is None:
                direct_link = info.get_direct_link()
            return direct_link

    return None


@register_url_processor(Playlist)
@wrap_urls_container
def get_playlist_play_url(
    hass: HomeAssistant,
    media_object: Playlist,
) -> Sequence[Tuple[str, str, str]]:
    tracks = media_object.tracks
    if tracks is None:
        tracks = media_object.fetch_tracks()

    items = []
    for track in tracks:
        track_id = str(track.id)
        artists = []
        try:
            artists = track.artists_name() or []
        except BaseException:
            artists = []

        title = str(getattr(track, "title", "") or "").strip()
        artists_str = ", ".join(artists).strip()
        if title and artists_str:
            display_name = f"{artists_str} - {title}"
        elif title:
            display_name = title
        else:
            display_name = f"track-{track_id}"

        items.append(("track", track_id, display_name))

    return items


def install(hass: HomeAssistant):
    from homeassistant.components.media_player import MediaPlayerEntity

    if MediaPlayerEntity.__getattribute__ is not _patch_generic_get_attribute:
        _LOGGER.debug(f"Patching __getattribute__ for generic entities")
        MediaPlayerEntity.orig__getattribute__ = MediaPlayerEntity.__getattribute__
        MediaPlayerEntity.__getattribute__ = _patch_generic_get_attribute

    hass.http.register_view(YandexMusicBrowserView())


def uninstall(hass: HomeAssistant):
    from homeassistant.components.media_player import MediaPlayerEntity

    if MediaPlayerEntity.__getattribute__ is _patch_generic_get_attribute:
        # noinspection PyUnresolvedReferences
        MediaPlayerEntity.__getattribute__ = MediaPlayerEntity.orig__getattribute__

    hass.data.pop(DATA_PLAY_KEY, None)
