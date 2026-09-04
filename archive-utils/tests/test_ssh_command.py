import pytest

from fk_archive_utils.errors import UsageError
from fk_archive_utils.profile import Profile
from fk_archive_utils.ssh_command import FK_ARCHIVE, SUDO, resolve

PROFILE = Profile(name="staging", root="/archive/media-staging", manager="archive-manager-staging")


def test_an_archive_command_is_run_as_the_archive_account():
    argv = resolve("fk-archive trash 12/dash", PROFILE)

    assert argv == [SUDO, "-n", "-u", "archive-manager-staging", "--", FK_ARCHIVE, "staging", "trash", "12/dash"]


def test_delete_variant_is_passed_through_like_any_other_archive_command():
    argv = resolve("fk-archive delete-variant dash_preview 12", PROFILE)

    assert argv == [
        SUDO,
        "-n",
        "-u",
        "archive-manager-staging",
        "--",
        FK_ARCHIVE,
        "staging",
        "delete-variant",
        "dash_preview",
        "12",
    ]


def test_the_profile_comes_from_the_key_and_not_from_the_request():
    argv = resolve("fk-archive prod trash 12", PROFILE)

    # "prod" is carried through as the verb, which fk-archive then refuses.
    # What matters is that it did not become the profile.
    assert argv[: argv.index("staging") + 1][-1] == "staging"


def test_quoted_arguments_survive_intact():
    argv = resolve("fk-archive publish '12/original/a file.mov' --size 3", PROFILE)

    assert argv[-3:] == ["12/original/a file.mov", "--size", "3"]


@pytest.mark.parametrize(
    "requested",
    [
        "",
        "   ",
        "bash",
        "/bin/sh -c 'rm -rf /archive'",
        "scp -t /archive/media",
        "rsync --server .",
        # Reads come off the NFS export now, so the key has no SFTP half left
        # to ask for. Refused like anything else it is not allowed to run.
        "sftp-server",
        "internal-sftp",
        "/usr/lib/openssh/sftp-server",
        "fk-archive-purge-trash staging --older-than 0",
        "/usr/bin/fk-archive-purge-trash staging --older-than 0",
    ],
)
def test_everything_else_is_refused(requested: str):
    with pytest.raises(UsageError):
        resolve(requested, PROFILE)


def test_an_unparseable_request_is_refused_rather_than_guessed_at():
    with pytest.raises(UsageError, match="could not parse"):
        resolve("fk-archive publish 'unbalanced", PROFILE)
