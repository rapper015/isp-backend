"""Declarative compatibility profiles for supported RouterOS release families."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RouterOSProfile:
    key: str
    major: int
    minimum: tuple[int, int, int]
    plaintext_login: bool
    package_path: str


ROUTEROS_PROFILES = {
    "v6-modern": RouterOSProfile("v6-modern", 6, (6, 43, 0), True, "/system/package"),
    "v7": RouterOSProfile("v7", 7, (7, 0, 0), True, "/system/package/update"),
}

# Exact production releases offered by the onboarding UI. Compatibility is
# still selected by profile, so a newly released patch can be entered manually
# without requiring a new adapter implementation.
_V6_RELEASES = (
    "6.43.16", "6.44.6", "6.45.9", "6.46.8", "6.47.10", "6.48.7",
    "6.49", "6.49.1", "6.49.2", "6.49.3", "6.49.4", "6.49.5",
    "6.49.6", "6.49.7", "6.49.8", "6.49.9", "6.49.10", "6.49.11",
    "6.49.12", "6.49.13", "6.49.14", "6.49.15", "6.49.16",
    "6.49.17", "6.49.18", "6.49.19", "6.49.20", "6.49.21", "6.49.22",
)
_V7_RELEASES = (
    "7.1", "7.1.5", "7.2", "7.2.3", "7.3", "7.3.1", "7.4", "7.4.1",
    "7.5", "7.6", "7.7", "7.8", "7.9", "7.9.2", "7.10", "7.10.2",
    "7.11", "7.11.3", "7.12", "7.12.2", "7.13", "7.13.5", "7.14",
    "7.14.3", "7.15", "7.15.3", "7.16", "7.16.2", "7.17", "7.17.2",
    "7.18", "7.18.2", "7.19", "7.19.6", "7.20", "7.20.8", "7.21",
    "7.21.5", "7.22", "7.22.3", "7.23", "7.23.7", "7.24", "7.24.4",
)


def profile_for_version(version: tuple[int, int, int]) -> RouterOSProfile:
    for profile in ROUTEROS_PROFILES.values():
        if version[0] == profile.major and version >= profile.minimum:
            return profile
    raise ValueError("unsupported RouterOS version; supported versions are 6.43+ and 7.x")


def version_catalog() -> list[dict[str, str]]:
    """Return exact releases for UI selection, newest first."""
    releases = [
        {"version": version, "family": "6", "profile": "v6-modern"}
        for version in _V6_RELEASES
    ] + [
        {"version": version, "family": "7", "profile": "v7"}
        for version in _V7_RELEASES
    ]
    return list(reversed(releases))
