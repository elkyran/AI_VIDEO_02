"""Trusted in-process wiring, never a module/function/credential path from JSON."""
from dataclasses import dataclass
from typing import Callable

from .registry import StudioError


@dataclass(frozen=True)
class HostServices:
    worker_transport: object | None = None
    director_factory: Callable | None = None
    drive_host: object | None = None

    def __post_init__(self):
        from .v2_provider_bridge import SyntheticTransport
        from .v2_delivery_host import DriveHostContext
        if self.drive_host is not None and type(self.drive_host) is not DriveHostContext:
            raise StudioError('WAITING_AUTH', 'Only an exact trusted DriveHostContext can resolve authorization')
        if self.worker_transport is not None and type(self.worker_transport) is not SyntheticTransport:
            raise StudioError('BLOCKED_ELIGIBILITY', 'Only an exact synthetic worker transport is enabled')
        if self.director_factory is not None and not callable(self.director_factory):
            raise ValueError('Director factory is trusted host code, not an import name')


def host_services(value):
    if value is not None and type(value) is not HostServices:
        raise StudioError('HOST_BINDING_REQUIRED', 'Use explicit trusted HostServices; JSON cannot import host code')
    return value or HostServices()
