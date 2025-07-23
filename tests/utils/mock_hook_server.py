from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response
from typing import Dict, Callable
from queue import Queue


class MockHookServer:
    recorded_requests: Queue[Request]

    def __init__(self, port: int = None):
        self.recorded_requests = Queue()
        self.server = HTTPServer(port=port)
        self.response_handlers: Dict[str, Callable] = {}

    def configure_response(
        self,
        path: str,
        handler: Callable[[Request], Response] = lambda _: Response("{}", content_type="application/json", status=200),
    ):
        """Configure a response handler for a specific path."""

        def wrapped_handler(request: Request):
            self.recorded_requests.put(request)

            try:
                return handler(request)
            except Exception as e:
                return Response(f"Internal server error: {e}", status=500)

        self.server.expect_request(path).respond_with_handler(wrapped_handler)

    def clear_configuration(self):
        """Clear all response configurations."""
        self.server.clear_all_handlers()

    def start(self):
        """Start the mock server."""
        self.server.start()

    def stop(self):
        """Stop the mock server."""
        self.server.stop()

    @property
    def port(self):
        return self.server.port
