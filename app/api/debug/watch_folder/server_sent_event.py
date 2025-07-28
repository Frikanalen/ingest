from dataclasses import dataclass


@dataclass
class ServerSentEvent:
    data: str
    event: str | None = None
    id: str | None = None
    retry: int | None = None

    def encode(self) -> str:
        """Encode to SSE format."""
        lines = []
        if self.event:
            lines.append(f"event: {self.event}")
        if self.id:
            lines.append(f"id: {self.id}")
        if self.retry:
            lines.append(f"retry: {self.retry}")
        for line in self.data.splitlines() or [""]:
            lines.append(f"data: {line}")

        return "\n".join(lines) + "\n\n"
