# XXX very hacky
VF_FORMATS = {
    "large_thumb": 1,
    "broadcast": 2,
    "vc1": 3,
    "med_thumb": 4,
    "small_thumb": 5,
    "original": 6,
    "theora": 7,
    "srt": 8,
}
for k, v in list(VF_FORMATS.items()):
    VF_FORMATS[v] = k
