from typing import Any

from rich.console import Console
from rich.table import Table


def _deep_get(obj: dict | Any, attr: str) -> Any:
    parts = attr.split(".")
    for part in parts:
        if isinstance(obj, dict):
            obj = obj.get(part, "UNSET")
        else:
            obj = getattr(obj, part, "UNSET")
        if obj is None:
            return "UNSET"
    return obj


def pprint_object_list(data_list: list[Any], fields: list[str], title: str = "Data"):
    table = Table(title=title, expand=True)
    for field in fields:
        table.add_column(field.capitalize())

    for item in data_list:
        row = [str(_deep_get(item, f)) for f in fields]
        table.add_row(*row)

    Console().print(table)
