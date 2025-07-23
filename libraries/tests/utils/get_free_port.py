import socket


def get_free_port() -> int:
    """Get a free port number from the operating system.

    Returns:
        int: A port number that is currently unused (between 0 and 65535).
    """
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]
