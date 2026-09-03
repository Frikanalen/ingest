import io
import json
from pathlib import Path

import pytest

from fk_archive_utils import cli, purge_cli
from fk_archive_utils.errors import AlreadyExists, NotFound, ProfileError, TransferError, UsageError

CONTENT = b"a video, notionally"


def invoke(profile_dir: Path, argv, stdin=b""):
    out = io.StringIO()
    code = cli.run(argv, stdin=io.BytesIO(stdin), stdout=out, profile_dir=profile_dir)
    return code, out.getvalue()


def test_publish_reports_what_it_wrote_as_json(profile_dir: Path, archive_root: Path):
    code, out = invoke(
        profile_dir,
        ["test", "publish", "12/original/a.mov", "--size", str(len(CONTENT))],
        stdin=CONTENT,
    )

    assert code == 0
    assert json.loads(out)["path"] == "12/original/a.mov"
    assert (archive_root / "12/original/a.mov").read_bytes() == CONTENT


def test_trash_reports_where_the_media_went(profile_dir: Path, archive_root: Path, make_file):
    make_file("12/dash/manifest.mpd")

    code, out = invoke(profile_dir, ["test", "trash", "12/dash"])

    assert code == 0
    assert (archive_root / json.loads(out)["destination"] / "manifest.mpd").exists()


@pytest.mark.parametrize(
    ("argv", "stdin", "expected"),
    [
        (["test", "trash", "12/dash/manifest.mpd"], b"", UsageError.exit_code),
        (["test", "trash", "12/dash"], b"", NotFound.exit_code),
        (["test", "publish", "12/original/a.mov", "--size", "99"], b"short", TransferError.exit_code),
        (["nosuch", "trash", "12"], b"", ProfileError.exit_code),
    ],
)
def test_each_kind_of_refusal_gets_its_own_exit_code(profile_dir: Path, argv, stdin, expected):
    code, out = invoke(profile_dir, argv, stdin=stdin)

    assert (code, out) == (expected, "")


def test_publishing_over_something_is_distinguishable_from_every_other_failure(profile_dir: Path, make_file):
    make_file("12/original/a.mov")

    code, _ = invoke(profile_dir, ["test", "publish", "12/original/a.mov", "--size", "0"])

    assert code == AlreadyExists.exit_code


@pytest.mark.parametrize(
    "argv",
    [
        # The sudoers rule ends in a wildcard, so what this command cannot do
        # is the whole of what the rule withholds. Both of these are operator
        # tools with their own entry points, and neither may be reachable from
        # an SSH session.
        ["test", "purge-trash", "--older-than", "0"],
        ["test", "move", "12/broadcast/a.mov", "12/original/a.mov"],
    ],
)
def test_the_operator_only_tools_are_not_verbs_of_this_command(profile_dir: Path, argv):
    with pytest.raises(SystemExit):
        cli.run(argv, profile_dir=profile_dir)


def test_purge_needs_an_age_before_it_will_delete_anything(profile_dir: Path):
    with pytest.raises(SystemExit):
        purge_cli.run(["test"], profile_dir=profile_dir)


def test_purge_says_what_it_did(profile_dir: Path, archive_root: Path):
    (archive_root / ".trash/20200101T000000Z/12/dash").mkdir(parents=True)
    out = io.StringIO()

    code = purge_cli.run(["test", "--older-than", "1"], stdout=out, profile_dir=profile_dir)

    assert code == 0
    assert "removed 20200101T000000Z" in out.getvalue()
    assert "removed 1 trash entry from test" in out.getvalue()
    assert not (archive_root / ".trash/20200101T000000Z").exists()
