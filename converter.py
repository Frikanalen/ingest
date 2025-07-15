import os


class Converter(object):
    CONVERT = {
        "theora": {
            "ffmpeg": (
                "-vcodec libtheora -acodec libvorbis "
                "-qscale:v 7 -qscale:a 2 -vf scale=720:-1"
            ),
            "ext": "ogv",
        },
        "broadcast": {
            "ffmpeg": "-target pal-dv",
            "ext": "dv",
        },
        "large_thumb": {
            "ffmpeg": "-vframes 1 -ss {thumb_sec} -vf scale=720:-1 -aspect 16:9",
            "ext": "jpg",
        },
    }

    @classmethod
    def new_filepath(cls, path, format):
        c = cls.CONVERT[format]
        fn = os.path.splitext(os.path.basename(path))[0]
        return os.path.join(
            os.path.dirname(os.path.dirname(path)), format, "%s.%s" % (fn, c["ext"])
        )

    @classmethod
    def convert_cmds(cls, filepath, format, metadata=None):
        c = cls.CONVERT[format]
        to_fn = cls.new_filepath(filepath, format)
        cmd = ["ffmpeg", "-nostats", "-i", filepath, "-y", "-threads", "8"]
        dur = int(metadata and metadata["duration"] * 0.25 or 30)
        cmd.extend(c["ffmpeg"].format(thumb_sec=dur).split())
        cmd.append(to_fn)
        return cmd, to_fn

    @classmethod
    def get_formats(cls, filepath):
        formats = ["large_thumb"]
        path = os.path.dirname(filepath)
        if "original" in path:
            formats.append("broadcast")
        else:
            assert "broadcast" in path
        formats.append("theora")
        return formats
