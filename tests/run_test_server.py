import uvicorn

from app.main import app


def run_server(host="127.0.0.1", port=8001, log_level="debug"):
    print(f"Trying to start server on port {port} with host {host}")
    uvicorn.run(app, host=host, port=port, log_level=log_level)
