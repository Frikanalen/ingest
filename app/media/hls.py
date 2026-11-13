"""HLS playlists over the DASH ladder's own media.

Apple devices play DASH only through ManagedMediaSource, where the page builds
the stream itself. That costs them AirPlay -- an Apple TV needs a URL it can
fetch, and dash.js switches remote playback off -- and leaves seeking to
dash.js rather than the system player. Native HLS gives both back.

Nothing is encoded or stored twice for it. The ladder is fMP4 with one segment
per GOP, which is exactly what HLS wants, and the manifest ffmpeg writes for
`-single_file 1` is a SegmentList that already names every segment's byte
range. So the playlists are a translation of the manifest and nothing more:
they live in a directory of their own and point back at `../dash/` by range.

The flip side is that they are only true of the manifest they were made from.
Rebuilding the ladder moves every byte range, so whatever rebuilds `dash` has
to rewrite these too -- see `app.converge.chores.produce_formats`.
"""

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction

#: Bump when a change to what is written here makes existing playlists worth
#: rewriting. Numbered from 1, like the templates' revisions.
REVISION = 1

#: The playlist a player is handed; the media playlists are named after the
#: file each one indexes.
MASTER_PLAYLIST = "master.m3u8"

#: Where the playlists find the media, relative to themselves. The variant
#: directories are siblings under the video's directory, so this is the whole
#: of what ties the two together.
MEDIA_PREFIX = "../dash/"

_NS = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}

#: EXT-X-MAP with a byte range needs version 6, and fMP4 media 7.
HLS_VERSION = 7

#: Codec families that start a variant ahead of the rest. A player starts
#: with the first variant it can decode, and H.264 is the one every Apple
#: device can; AV1 follows for the ones that have a decoder for it.
_START_FIRST = ("avc1", "avc3")


class UnsupportedManifest(ValueError):
    """The manifest is not the shape `-single_file 1` writes, so it cannot be translated."""


@dataclass(frozen=True)
class Segment:
    duration: Fraction
    offset: int
    length: int


@dataclass(frozen=True)
class Representation:
    """One file of the ladder, and everything a playlist needs to say about it."""

    id: str
    content_type: str
    codecs: str
    media_file: str
    init_offset: int
    init_length: int
    segments: tuple[Segment, ...]
    width: int | None = None
    height: int | None = None
    frame_rate: Fraction | None = None

    @property
    def playlist_name(self) -> str:
        stem = self.media_file.rsplit(".", 1)[0]
        return f"{stem}.m3u8"

    @property
    def duration(self) -> Fraction:
        return sum((segment.duration for segment in self.segments), Fraction(0))

    @property
    def peak_bandwidth(self) -> int:
        """HLS's BANDWIDTH: the highest bitrate of any one segment, in bits per second."""
        return max(math.ceil(segment.length * 8 / segment.duration) for segment in self.segments)

    @property
    def average_bandwidth(self) -> int:
        return math.ceil(sum(segment.length for segment in self.segments) * 8 / self.duration)


def _byte_range(value: str | None, what: str) -> tuple[int, int]:
    """`first-last`, inclusive as DASH writes it, as (offset, length)."""
    match = re.fullmatch(r"(\d+)-(\d+)", value or "")
    if not match:
        raise UnsupportedManifest(f"{what} has no usable byte range: {value!r}")
    first, last = int(match[1]), int(match[2])
    return first, last - first + 1


def _iso_duration(value: str | None) -> Fraction:
    """`PT1H2M3.5S`, which is all ffmpeg writes for mediaPresentationDuration."""
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?", value or "")
    if not match or not any(match.groups()):
        raise UnsupportedManifest(f"cannot read the presentation duration {value!r}")
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + Fraction(seconds or "0")


def _segments(segment_list: ET.Element, total: Fraction) -> tuple[Segment, ...]:
    timescale = int(segment_list.get("timescale", "1"))
    duration = segment_list.get("duration")
    if duration is None:
        raise UnsupportedManifest("SegmentList without a fixed segment duration")
    nominal = Fraction(int(duration), timescale)

    ranges = [_byte_range(url.get("mediaRange"), "SegmentURL") for url in segment_list.findall("mpd:SegmentURL", _NS)]
    if not ranges:
        raise UnsupportedManifest("SegmentList names no segments")

    # Every segment but the last is the nominal length -- that is what the
    # segmentation guarantees. The last is whatever the presentation has left.
    # The presentation duration is rounded, so a remainder outside (0, nominal]
    # is that rounding talking; the last segment then stands at nominal length.
    last = total - nominal * (len(ranges) - 1)
    if not 0 < last <= nominal:
        last = nominal
    durations = [nominal] * (len(ranges) - 1) + [last]
    return tuple(
        Segment(duration=length, offset=offset, length=size)
        for length, (offset, size) in zip(durations, ranges, strict=True)
    )


def parse_manifest(mpd: str) -> list[Representation]:
    """Every representation in a `-single_file 1` manifest, in manifest order."""
    try:
        root = ET.fromstring(mpd)
    except ET.ParseError as e:
        raise UnsupportedManifest(f"not XML: {e}") from e

    if root.get("type", "static") != "static":
        raise UnsupportedManifest("only a static (on-demand) manifest can become a VOD playlist")
    total = _iso_duration(root.get("mediaPresentationDuration"))

    periods = root.findall("mpd:Period", _NS)
    if len(periods) != 1:
        raise UnsupportedManifest(f"expected one Period, found {len(periods)}")

    representations = []
    for adaptation_set in periods[0].findall("mpd:AdaptationSet", _NS):
        content_type = adaptation_set.get("contentType") or ""
        set_rate = adaptation_set.get("frameRate")
        for rep in adaptation_set.findall("mpd:Representation", _NS):
            content_type = content_type or (rep.get("mimeType") or "").split("/")[0]
            base_url = rep.find("mpd:BaseURL", _NS)
            segment_list = rep.find("mpd:SegmentList", _NS)
            if base_url is None or not (base_url.text or "").strip() or segment_list is None:
                raise UnsupportedManifest(f"representation {rep.get('id')} is not a single file with a SegmentList")
            media_file = base_url.text.strip()
            if "/" in media_file or media_file.startswith("."):
                raise UnsupportedManifest(f"representation {rep.get('id')} names media outside its directory")

            init = segment_list.find("mpd:Initialization", _NS)
            init_offset, init_length = _byte_range(init.get("range") if init is not None else None, "Initialization")
            rate = rep.get("frameRate") or set_rate
            representations.append(
                Representation(
                    id=rep.get("id") or str(len(representations)),
                    content_type=content_type,
                    codecs=rep.get("codecs") or "",
                    media_file=media_file,
                    init_offset=init_offset,
                    init_length=init_length,
                    segments=_segments(segment_list, total),
                    width=int(rep.get("width")) if rep.get("width") else None,
                    height=int(rep.get("height")) if rep.get("height") else None,
                    frame_rate=Fraction(rate) if rate else None,
                )
            )
    return representations


def _seconds(value: Fraction) -> str:
    return f"{float(value):.6f}"


def media_playlist(rep: Representation) -> str:
    target = max(math.ceil(segment.duration) for segment in rep.segments)
    uri = MEDIA_PREFIX + rep.media_file
    lines = [
        "#EXTM3U",
        f"#EXT-X-VERSION:{HLS_VERSION}",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        f"#EXT-X-TARGETDURATION:{target}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        f'#EXT-X-MAP:URI="{uri}",BYTERANGE="{rep.init_length}@{rep.init_offset}"',
    ]
    for segment in rep.segments:
        lines += [
            f"#EXTINF:{_seconds(segment.duration)},",
            f"#EXT-X-BYTERANGE:{segment.length}@{segment.offset}",
            uri,
        ]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def master_playlist(representations: list[Representation]) -> str:
    videos = [rep for rep in representations if rep.content_type == "video"]
    audios = [rep for rep in representations if rep.content_type == "audio"]
    if not videos:
        raise UnsupportedManifest("no video representation to play")
    # One audio track is all a ladder has; more would need naming and a choice.
    audio = audios[0] if audios else None

    lines = ["#EXTM3U", f"#EXT-X-VERSION:{HLS_VERSION}", "#EXT-X-INDEPENDENT-SEGMENTS"]
    if audio:
        lines.append(
            f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Lyd",DEFAULT=YES,AUTOSELECT=YES,URI="{audio.playlist_name}"'
        )

    def start_order(rep: Representation) -> tuple[bool, int]:
        return (not rep.codecs.startswith(_START_FIRST), -rep.peak_bandwidth)

    for rep in sorted(videos, key=start_order):
        codecs = ",".join(filter(None, (rep.codecs, audio.codecs if audio else "")))
        attributes = [
            f"BANDWIDTH={rep.peak_bandwidth + (audio.peak_bandwidth if audio else 0)}",
            f"AVERAGE-BANDWIDTH={rep.average_bandwidth + (audio.average_bandwidth if audio else 0)}",
            f'CODECS="{codecs}"',
        ]
        if rep.width and rep.height:
            attributes.append(f"RESOLUTION={rep.width}x{rep.height}")
        if rep.frame_rate:
            attributes.append(f"FRAME-RATE={float(rep.frame_rate):.3f}")
        if audio:
            attributes.append('AUDIO="audio"')
        lines += [f"#EXT-X-STREAM-INF:{','.join(attributes)}", rep.playlist_name]
    return "\n".join(lines) + "\n"


def playlists_from_manifest(mpd: str) -> dict[str, str]:
    """Every playlist the manifest translates to, by file name. The master is MASTER_PLAYLIST."""
    representations = parse_manifest(mpd)
    playlists = {rep.playlist_name: media_playlist(rep) for rep in representations}
    if MASTER_PLAYLIST in playlists:
        raise UnsupportedManifest(f"a representation's playlist would be called {MASTER_PLAYLIST}")
    playlists[MASTER_PLAYLIST] = master_playlist(representations)
    return playlists
