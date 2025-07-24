def pretty_duration(duration):
    mins, secs = divmod(duration, 60)
    hours, _ = divmod(mins, 60)
    return f"{int(hours):d}:{int(mins):02d}:{secs:02f}"
