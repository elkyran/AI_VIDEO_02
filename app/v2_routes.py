"""Host-owned worker routing; no credentials, clients or alternate scheduler."""
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Identifier, Sha256
from .v2_contracts import Contract


class AccountBinding(Contract):
    identity_hash: Sha256
    auth_generation: int = Field(ge=1)


class WorkerRoute(Contract):
    role: Literal['gemini_story', 'gemini_visual', 'codex']
    worker_id: Identifier
    provider: Literal['gemini', 'codex']
    profile_id: Identifier
    account_binding: AccountBinding
    synthetic: bool

    @model_validator(mode='after')
    def fixed_role(self):
        if self.role in ('gemini_story', 'gemini_visual'):
            if (self.worker_id, self.provider) != (
                    'antigravity_'+self.role.removeprefix('gemini_'), 'gemini'):
                raise ValueError('Story and Visual cannot change worker/provider')
        if self.profile_id not in {self.provider+'-'+str(i) for i in range(1, 5)}:
            raise ValueError('Route must refer to an existing provider profile')
        return self

    @property
    def reservation(self):
        # Generation and profile aliases cannot create another account lock.
        return self.provider+':'+self.account_binding.identity_hash


class WorkerRoutes(Contract):
    routes: list[WorkerRoute] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def unique(self):
        for key in ('role', 'worker_id', 'profile_id'):
            values = [getattr(route, key) for route in self.routes]
            if len(values) != len(set(values)):
                raise ValueError('Ambiguous host route '+key)
        return self

    def for_task(self, task):
        canonical = task.get('context', {}).get('v2_contract', {}).get('role')
        role = canonical or {'writer': 'gemini_story', 'critic': 'gemini_visual', 'director': 'codex'}.get(task['role'])
        return next((route for route in self.routes if route.role == role), None)


def routes_from_slots(slots):
    """Convert only explicit synthetic host slots; no identity inference."""
    return WorkerRoutes(routes=[WorkerRoute(role=slot.role, worker_id=slot.worker_id,
        provider='gemini', profile_id=slot.profile_id,
        account_binding=AccountBinding(identity_hash=slot.identity_hash, auth_generation=slot.auth_generation),
        synthetic=True) for slot in (slots.story, slots.visual)])
