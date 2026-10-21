"""Capability gate around the existing adapter interface, with injected test transport.

No launcher, account manager, authentication flow or undocumented provider protocol.
Antigravity cannot be enabled by a capability JSON supplied by a model.
"""
import time

from .registry import StudioError


class ProviderBridge:
    is_local = False
    def __init__(self, adapter, *, provider, capability_check, opt_in=False, timeout=60, clock=time.monotonic):
        self.adapter, self.provider, self.capability_check = adapter, provider, capability_check
        self.opt_in, self.timeout, self.clock = opt_in is True, timeout, clock
        self.is_simulated = getattr(adapter, 'is_simulated', False) is True
        self.is_live = not self.is_simulated
        self.paused = False
        self.runs = {}

    def start(self, task, profile, attempt_dir=None):
        if self.paused:
            raise StudioError('PROVIDER_PAUSED', 'Resume explicitly; no account rotation is attempted')
        if not self.is_simulated and (not self.opt_in or self.provider != 'codex'):
            raise StudioError('BLOCKED_UNSUPPORTED', 'Antigravity is AGE-GATED / DISABLED; no replacement route')
        checked = self.capability_check(task, profile)
        if not checked.get('structured_output') or not checked.get('cancellation'):
            raise StudioError('BLOCKED_UNSUPPORTED', 'Required structured/cancellation capabilities are not verified')
        for kind in task.get('context', {}).get('required_media', []):
            if checked.get(kind) is not True:
                raise StudioError('UNSUPPORTED_MEDIA', 'The selected provider has no verified '+kind+' capability')
        if not self.is_simulated and (checked.get('live_verified') is not True or not profile.get('identity_verified')
                or not profile.get('account_binding')):
            raise StudioError('WAITING_AUTH', 'Existing host account/capability binding is unverified')
        handle = self.adapter.start(task, profile, attempt_dir)
        self.runs[handle] = self.clock()+self.timeout
        return handle

    def poll(self, handle):
        if handle not in self.runs:
            return []
        if self.clock() >= self.runs[handle]:
            self.adapter.cancel(handle); self.runs.pop(handle)
            return [{'type': 'failed', 'failure': {'kind': 'local_turn_limit', 'code': 'PROVIDER_TIMEOUT',
                'message': 'Bounded provider deadline expired; accepted results are not repeated', 'retryable': False}}]
        if self.paused:
            return []
        events = self.adapter.poll(handle)
        if any(e.get('type') in {'completed','failed','interrupted'} for e in events):
            self.runs.pop(handle, None)
        return events

    def cancel(self, handle):
        self.runs.pop(handle, None)
        return self.adapter.cancel(handle)

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False
