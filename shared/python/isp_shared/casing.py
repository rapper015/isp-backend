import re

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def camel_to_snake(key: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", key).lower()


def normalize_keys(data: dict) -> dict:
    return {camel_to_snake(key): value for key, value in data.items()}
