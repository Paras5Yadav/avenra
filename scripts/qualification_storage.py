"""Conservative disk admission for large local qualification rehearsals.

This is a preflight/checkpoint guard, not a filesystem quota. It never removes
retained evidence. One kernel-held lease per volume serializes the covered
heavy rehearsals across checkouts, and is released if its owner exits.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

GIB = 1024 ** 3
FREE_FLOOR = 16 * GIB
BUDGETS = {'coordinated_recovery': 6 * GIB, 'aa_recovery': 6 * GIB, 'identity_recovery': 6 * GIB,
           'release_upgrade': 4 * GIB, 'capacity': 2 * GIB}
CASE_PACK_HELPERS = {
    'verify_coordinated_recovery.py': ('readiness_evidence.py',),
    'verify_aa_recovery.py': ('aa_review_oracle.cjs',),
    'verify_identity_recovery.py': ('readiness_evidence.py',),
    'verify_releases.py': (),
    'verify_capacity.py': (),
    'verify_work_queue.py': (),
}


def case_pack_hash(root, *names):
    result = hashlib.sha256()
    for name in [*names, 'qualification_storage.py']:
        result.update(name.encode() + b'\0' + (Path(root) / 'scripts' / name).read_bytes())
    return result.hexdigest()


def harness_case_pack_hash(root, name):
    """Use one recipe for production, readiness and acceptance resume checks."""
    if name in CASE_PACK_HELPERS:
        return case_pack_hash(root, name, *CASE_PACK_HELPERS[name])
    return hashlib.sha256((Path(root) / 'scripts' / name).read_bytes()).hexdigest()


class StorageRefused(RuntimeError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def record_refusal(root, report_name, error, fingerprint, case_pack):
    """A refused latest attempt must not leave an older passing summary selected."""
    from avenra.private_config import write_document
    report = {'passed': False, 'checks': [], 'created_at': time.time(),
              'application_fingerprint': fingerprint, 'case_pack_sha256': case_pack,
              'production_ready': False, 'official_conformance': False,
              'error': error.code, 'message': str(error), 'details': error.details,
              'scope': 'Storage admission refused before the qualification created a case or launched any service.'}
    directory = Path(root) / '.local'
    directory.mkdir(mode=0o700, exist_ok=True)
    write_document(directory / report_name, report)
    print(json.dumps({**report, 'report': str(directory / report_name)}), flush=True)
    return 2


def _private_file(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        os.close(fd)
        raise StorageRefused('QUALIFICATION_STORAGE_STATE', 'Storage admission needs a private, unlinked regular state file.')
    return fd


class QualificationStorage:
    def __init__(self, root, profile, *, state_directory=None, floor_bytes=FREE_FLOOR):
        if profile not in BUDGETS or type(floor_bytes) is not int or floor_bytes < FREE_FLOOR:
            raise ValueError('Use a supported qualification profile and at least the declared free-space floor.')
        self.root = Path(root).resolve()
        self.profile = profile
        self.budget = BUDGETS[profile]
        self.floor = floor_bytes
        self.directory = Path(state_directory or Path.home() / '.local/share/avenra/qualification-storage')
        self.fd = None
        self.record = None
        self.record_path = None

    def __enter__(self):
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise StorageRefused('QUALIFICATION_STORAGE_STATE', 'Storage admission needs a private state directory.')
            # The lock is shared across worktrees on this volume. It is not a
            # PID/expiry lease: a live owner cannot be evicted by elapsed time.
            volume = self.root.stat().st_dev
            self.fd = _private_file(self.directory / ('volume-' + str(volume) + '.lock'))
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StorageRefused('QUALIFICATION_STORAGE_BUSY', 'Another covered heavy qualification owns this volume; wait for that actual process to finish.') from None
            available = self.free_bytes()
            self.record = {'format': 'avenra-qualification-storage/1', 'id': uuid.uuid4().hex,
                           'profile': self.profile, 'root': str(self.root), 'volume': volume,
                           'pid': os.getpid(), 'created_at': time.time(), 'initial_free_bytes': available,
                           'free_floor_bytes': self.floor, 'planned_bytes': self.budget,
                           'state': 'active', 'checkpoints': []}
            self.record_path = self.directory / ('attempt-' + self.record['id'] + '.json')
            self.checkpoint('admission')
            return self
        except BaseException as error:
            # An inaccessible/link-substituted lock or a failed metadata write
            # is an admission refusal too. Keep it within the typed boundary
            # used by each harness to invalidate its latest passing summary.
            # A second metadata failure must not hide the original refusal or
            # prevent the finally block in close() from releasing the lease.
            try:
                self.close('refused')
            except OSError:
                pass
            if isinstance(error, OSError):
                raise StorageRefused('QUALIFICATION_STORAGE_STATE',
                    'Storage admission could not access or retain its private state; qualification was refused.',
                    {'errno': error.errno}) from error
            raise

    def free_bytes(self):
        value = os.statvfs(self.root)
        return value.f_bavail * value.f_frsize

    def _persist(self):
        if self.record_path is None:
            return
        # Unique record names, single writer under the volume lease. State
        # contains no credentials and failed admission records remain retained.
        content = (json.dumps(self.record, sort_keys=True, indent=2) + '\n').encode()
        fd = _private_file(self.record_path)
        try:
            os.ftruncate(fd, 0)
            view = memoryview(content)
            while view:
                count = os.write(fd, view)
                if count == 0:
                    raise OSError('Unable to retain storage admission record.')
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def checkpoint(self, stage):
        if self.fd is None or self.record is None:
            raise RuntimeError('Storage admission must be acquired before checkpointing.')
        free = self.free_bytes()
        # Free-space deltas also include unrelated applications; they cannot
        # prove how much of this rehearsal's future work has been consumed.
        # Keep the full conservative write headroom at every checkpoint.
        remaining = self.budget
        accepted = free >= self.floor + remaining
        observation = {'stage': stage, 'observed_at': time.time(), 'free_bytes': free,
                       'planned_remaining_bytes': remaining, 'accepted': accepted}
        self.record['checkpoints'].append(observation)
        self._persist()
        if not accepted:
            raise StorageRefused('QUALIFICATION_DISK_SPACE', 'Insufficient disk space for this rehearsal while preserving the 16 GiB free-space floor. Retained cases are not deleted automatically.', observation)
        return observation

    def snapshot(self):
        return json.loads(json.dumps(self.record)) if self.record is not None else None

    def close(self, outcome='finished'):
        try:
            if self.record is not None and self.fd is not None:
                self.record.update(state=outcome, completed_at=time.time())
                self._persist()
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    def __exit__(self, kind, value, traceback):
        self.close('failed' if kind else 'finished')
