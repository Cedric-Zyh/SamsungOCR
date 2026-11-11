"""Explicit ownership of a remote attempt and the local evidence it reuses.

This identity describes durable queue attempts. Runtime timing collectors keep
their own run IDs; neither wall-clock order nor SQLite row order defines a new
attempt when this link is present.
"""
from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class LocalAttempt:
    job_id: str
    attempt: int

    @classmethod
    def from_json(cls, value):
        if (not isinstance(value, dict) or not isinstance(value.get('job_id'), str)
                or not value['job_id'] or type(value.get('attempt')) is not int
                or value['attempt'] < 0):
            raise ValueError('Invalid local attempt identity')
        return cls(value['job_id'], value['attempt'])


@dataclass(frozen=True)
class RemoteRunLink:
    remote_id: str
    created_for: LocalAttempt
    attached_to: LocalAttempt
    local_evidence: str = 'shared'
    remote_evidence: str = 'current'
    replaces_remote_id: str = ''
    reused_from: LocalAttempt | None = None

    @classmethod
    def create(cls, remote_id, owner, *, retains_local=False, replaces_remote_id=''):
        return cls(remote_id, owner, owner, 'retained' if retains_local else 'shared',
                   replaces_remote_id=replaces_remote_id)

    def attach(self, owner, *, completed):
        if owner == self.attached_to:
            return self
        return replace(self, attached_to=owner, local_evidence='shared',
                       remote_evidence='retained' if completed or self.remote_evidence == 'retained'
                                       else 'continuing', reused_from=self.attached_to)

    def matches(self, job_id, attempt, *, queued=False):
        return self.attached_to.job_id == job_id and (
            self.attached_to.attempt == attempt
            or queued and self.attached_to.attempt == attempt + 1)

    def to_json(self):
        return {'version': 1, **asdict(self)}

    @classmethod
    def from_json(cls, value):
        """Unknown/malformed versions remain on the conservative legacy path."""
        if not isinstance(value, dict) or value.get('version') != 1:
            return None
        try:
            if (not isinstance(value['remote_id'], str) or not value['remote_id']
                    or value['local_evidence'] not in {'shared', 'retained'}
                    or value['remote_evidence'] not in {'current', 'continuing', 'retained'}):
                return None
            return cls(value['remote_id'], LocalAttempt.from_json(value['created_for']),
                       LocalAttempt.from_json(value['attached_to']), value['local_evidence'],
                       value['remote_evidence'], value.get('replaces_remote_id', ''),
                       LocalAttempt.from_json(value['reused_from']) if value.get('reused_from') else None)
        except (KeyError, TypeError, ValueError):
            return None
