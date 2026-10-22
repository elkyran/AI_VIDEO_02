"""Explicit host Drive authorization and protected resumable sessions.

No credential discovery, login, consent, account inspection, or network call is
performed here. A host embeds DriveHostContext with already authorized callbacks;
JSON run inputs cannot create that authority. Delivery still uses v2_drive and
the existing Coordinator, gate, retries, object receipts, and request budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable
from urllib.parse import urlparse

from .contracts import Identifier
from .v2_acceptance import fingerprint
from .v2_drive import DriveError, GoogleDriveHTTP
from .v2_run_config import isolated_root, no_links

MAX_STORE = 1024 * 1024


class _WindowsProtection:
    """Current-user DPAPI, without reading any credential file or profile."""
    def __init__(self, scope):
        if os.name != 'nt':
            raise DriveError('private_store_unsupported')
        self.entropy = scope.encode('ascii')

    def _transform(self, value, *, decrypt=False):
        class Blob(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
        def blob(value):
            buffer = ctypes.create_string_buffer(value)
            return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer
        incoming, keep_input = blob(value)
        entropy, keep_entropy = blob(self.entropy)
        outgoing = Blob()
        crypto = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        operation = crypto.CryptUnprotectData if decrypt else crypto.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
            ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        operation.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        # CRYPTPROTECT_UI_FORBIDDEN prevents interactive authentication/consent.
        if not operation(ctypes.byref(incoming), None, ctypes.byref(entropy), None,
                         None, 1, ctypes.byref(outgoing)):
            raise DriveError('private_store_unavailable')
        try:
            return ctypes.string_at(outgoing.data, outgoing.size)
        finally:
            kernel.LocalFree(outgoing.data)

    def seal(self, value):
        return self._transform(value)

    def open(self, value):
        return self._transform(value, decrypt=True)


def _session_uri(value):
    if not isinstance(value, str) or len(value) > 8192 or any(c in value for c in '\r\n'):
        raise DriveError('private_store_invalid')
    parsed = urlparse(value)
    if (parsed.scheme != 'https' or parsed.netloc != 'www.googleapis.com'
            or not parsed.path.startswith('/upload/drive/v3/') or parsed.fragment):
        raise DriveError('private_store_invalid')
    return value


class PrivateSessionStore:
    """Small scope-bound DPAPI file; session URIs never enter public artifacts.

    Requires Windows current-user DPAPI, rejects links/sync/run/input roots, and
    uses an exclusive lock for each read-modify-replace. The existing delivery
    owner serializes the requests; a second writer fails closed without waiting.
    """
    def __init__(self, root, *, scope, excluded_roots=()):
        self.root = isolated_root(root)
        for excluded in excluded_roots:
            excluded = no_links(excluded)
            if self.root.is_relative_to(excluded) or excluded.is_relative_to(self.root):
                raise DriveError('private_store_location')
        self.scope = fingerprint(scope)
        self.path = self.root/(self.scope+'.dpapi')
        self._protection = _WindowsProtection(self.scope)
        # Construction is read-only; create only upon an explicit session write.
        no_links(self.path)

    def _load(self):
        path = no_links(self.path)
        if not path.exists():
            return {}
        if not path.is_file() or path.stat().st_size > MAX_STORE:
            raise DriveError('private_store_invalid')
        try:
            value = json.loads(self._protection.open(path.read_bytes()))
            if (set(value) != {'scope', 'sessions'} or value['scope'] != self.scope
                    or not isinstance(value['sessions'], dict)):
                raise ValueError()
            for key, uri in value['sessions'].items():
                self._key(key); _session_uri(uri)
            return value['sessions']
        except (ValueError, TypeError, KeyError, UnicodeError, OSError, DriveError):
            raise DriveError('private_store_invalid') from None

    @staticmethod
    def _key(key):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', key):
            raise DriveError('private_store_invalid')
        return key

    def get(self, key, default=None):
        return self._load().get(self._key(key), default)

    def __getitem__(self, key):
        return self._load()[self._key(key)]

    def __setitem__(self, key, value):
        from .development import locked
        key, value = self._key(key), _session_uri(value)
        no_links(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        lock = no_links(self.root/(self.scope+'.lock'))
        temporary = None
        try:
            with locked(lock):
                values = self._load(); values[key] = value
                data = json.dumps({'scope': self.scope, 'sessions': values}, sort_keys=True).encode()
                if len(data) > MAX_STORE//2:
                    raise DriveError('private_store_budget')
                encrypted = self._protection.seal(data)
                if len(encrypted) > MAX_STORE:
                    raise DriveError('private_store_budget')
                descriptor, temporary = tempfile.mkstemp(prefix=self.scope+'_', dir=self.root)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(encrypted); stream.flush(); os.fsync(stream.fileno())
                no_links(self.path)
                os.replace(temporary, self.path)
                temporary = None
        except ValueError:
            raise DriveError('private_store_busy') from None
        except OSError:
            raise DriveError('private_store_unavailable') from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class DriveHostAuthorization:
    credential_ref: str
    folder_id: str
    run_id: str
    version: int
    token_supplier: Callable[[], str] = field(repr=False, compare=False)

    def __post_init__(self):
        from pydantic import TypeAdapter
        TypeAdapter(Identifier).validate_python(self.credential_ref)
        TypeAdapter(Identifier).validate_python(self.run_id)
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', self.folder_id)
                or type(self.version) is not int or self.version < 1 or not callable(self.token_supplier)):
            raise DriveError('host_binding_invalid')


class DriveHostResolver:
    """Finite host allowlist of existing authorization callbacks, not accounts."""
    def __init__(self, bindings=()):
        bindings = tuple(bindings)
        if any(type(binding) is not DriveHostAuthorization for binding in bindings):
            raise DriveError('host_binding_invalid')
        self._bindings = {(b.credential_ref, b.folder_id, b.run_id, b.version): b for b in bindings}
        if len(self._bindings) != len(bindings):
            raise DriveError('host_binding_invalid')

    def resolve(self, *, credential_ref, folder_id, run_id, version):
        try:
            return self._bindings[(credential_ref, folder_id, run_id, version)].token_supplier
        except KeyError:
            raise DriveError('auth') from None


@dataclass(frozen=True)
class DriveHostContext:
    resolver: DriveHostResolver = field(repr=False)
    session_root: Path
    authorized: bool = False

    def transport(self, config):
        if (self.authorized is not True or type(self.resolver) is not DriveHostResolver
                or config.mode == 'simulation' or not config.drive
                or not config.drive.network_write_authorized or 'deliver' not in config.operations
                or config.budget.external_requests < 1):
            raise DriveError('permission')
        supplier = self.resolver.resolve(credential_ref=config.drive.credential_ref,
            folder_id=config.drive.folder_id, run_id=config.run_id, version=config.version)
        def safe_supplier():
            try:
                return supplier()
            except Exception:
                # An embedder's private exception must never reach public logs.
                raise DriveError('auth') from None
        scope = {'folder': config.drive.folder_id, 'run': config.run_id, 'version': config.version,
            'credential_ref': config.drive.credential_ref, 'configuration': fingerprint(config.pinned())}
        store = PrivateSessionStore(self.session_root, scope=scope,
            excluded_roots=(config.root, config.input_root))
        return GoogleDriveHTTP(token_supplier=safe_supplier, session_store=store, authorized=True,
            max_requests=config.budget.external_requests, timeout=min(30, config.budget.seconds))


def drive_host_readiness(context=None):
    """No resolver/token access, filesystem write or network probe."""
    return {'implementation': 'HOST_INTERFACE_IMPLEMENTED',
        'host_binding_supplied': type(context) is DriveHostContext,
        'credential_resolution': 'NOT_RUN', 'uploaded': False,
        'remote_sync': 'UNVERIFIED', 'cloud_receipt': None,
        'next_action': 'Owner supplies an authorized host callback and protected session root; separately authorize remote verification.'}
