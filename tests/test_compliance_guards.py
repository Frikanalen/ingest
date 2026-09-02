"""The upload gates must survive `python -O`.

`assert` is a statement the interpreter is entitled to delete. Both the
compliance rules and the archive-path guards are load-bearing, so they are
`raise`d -- these tests pin that, including under `-O` itself.
"""

import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.hooks.metadata import ComplianceError, MetadataExtractor
from tests.get_git_root import get_git_root


def probing(**format_fields) -> MetadataExtractor:
    """A MetadataExtractor whose probe reports exactly these format fields."""
    extractor = MetadataExtractor()

    async def do_probe(_upload_file: Path):
        return SimpleNamespace(format=SimpleNamespace(**format_fields))

    extractor.do_probe = do_probe  # type: ignore[method-assign]
    return extractor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("format_fields", "message"),
    [
        ({"nb_streams": 0, "duration": "60.0"}, "File has no streams"),
        ({"nb_streams": 1, "duration": None}, "File metadata does not contain duration"),
        ({"nb_streams": 1, "duration": "5.0"}, "File duration must be greater than 5 seconds"),
    ],
)
async def test_compliance_failures_keep_their_message(format_fields, message):
    """The uploader is shown str(e) as the NOT_COMPLIANT detail, so it is pinned."""
    with pytest.raises(ComplianceError) as failure:
        await probing(**format_fields).assert_compliance(Path("irrelevant.mp4"))
    assert str(failure.value) == message


@pytest.mark.asyncio
async def test_a_compliant_file_returns_its_metadata():
    metadata = await probing(nb_streams=1, duration="6.0").assert_compliance(Path("irrelevant.mp4"))
    assert metadata.format.duration == "6.0"


@pytest.mark.asyncio
async def test_short_file_is_not_compliant(color_bars_video):
    """The real probe, on a real one-second file."""
    with pytest.raises(ComplianceError, match="greater than 5 seconds"):
        await MetadataExtractor().assert_compliance(color_bars_video)


GUARDS_UNDER_O = textwrap.dedent(
    """
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace

    from app.api.hooks.metadata import ComplianceError, MetadataExtractor
    from app.util.file_name_utils import derived_file_location, original_file_location, program_image_location

    # __debug__ is False exactly when the interpreter is deleting asserts.
    if __debug__:
        raise SystemExit("subprocess is not running with -O")

    def refuses(call, why):
        try:
            call()
        except ValueError:
            return
        raise SystemExit(why)

    refuses(lambda: original_file_location("abcd", Path("v.mp4")), "original_file_location accepted 'abcd'")
    refuses(lambda: derived_file_location("abcd", "webm_med", Path("v.webm")), "derived_file_location accepted 'abcd'")
    refuses(lambda: program_image_location("abcd", "img", ".jpg"), "program_image_location accepted 'abcd'")

    extractor = MetadataExtractor()

    async def do_probe(_upload_file):
        return SimpleNamespace(format=SimpleNamespace(nb_streams=1, duration="1.0"))

    extractor.do_probe = do_probe
    try:
        asyncio.run(extractor.assert_compliance(Path("irrelevant.mp4")))
    except ComplianceError:
        pass
    else:
        raise SystemExit("assert_compliance accepted a one-second file")
    """
)


def test_guards_still_fire_with_asserts_optimised_away():
    result = subprocess.run(
        [sys.executable, "-O", "-c", GUARDS_UNDER_O],
        cwd=get_git_root(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
