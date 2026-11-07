"""Opt-in local audit. Contains no credentials and never affects authorization."""
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
from uuid import uuid4

EVENTS = frozenset(('request_received authentication_succeeded authentication_denied '
    'tenant_authorization_succeeded tenant_authorization_denied '
    'permission_authorization_succeeded permission_authorization_denied '
    'analysis_service_entered supervisor_construction_started '
    'supervisor_construction_completed request_completed').split())
KINDS = {'AuthenticationRequired', 'InvalidCredentials', 'IdentityInfrastructureError',
         'TenantAccessDenied', 'PermissionDenied'}
CURRENT = ContextVar('local_security_audit', default=None)

class LocalAudit:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or 'artifacts' in (s.lower() for s in self.path.parts):
            raise ValueError('Audit requires an explicit private path')
        workspace = Path(__file__).resolve().parent
        if any(p.is_symlink() or getattr(p.lstat(), 'st_file_attributes', 0) & 0x400
               for p in self.path.parents):
            raise ValueError('Audit parents must not be links')
        if self.path.resolve().is_relative_to(workspace):
            raise ValueError('Audit must be outside the repository')
        self.key = secrets.token_bytes(32)
        self.lock = threading.Lock()
        self.write_failed = False

    def write(self, record):
        try:
            with self.lock:
                # Provision an existing private file; do not create directories or follow links.
                info = self.path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or self.path.is_symlink():
                    raise OSError('Unsafe audit file')
                if getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise OSError('Reparse point')
                if os.name != 'nt' and info.st_mode & 0o077:
                    raise OSError('Audit file is not private')
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
                try:
                    actual = os.fstat(fd)
                    if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino) or actual.st_size >= 10_000_000:
                        raise OSError('Audit file changed or full')
                    data = (json.dumps(record, separators=(',', ':')) + '\n').encode('utf-8')
                    if os.write(fd, data) != len(data):
                        raise OSError('Incomplete audit write')
                finally:
                    os.close(fd)
        except Exception:
            self.write_failed = True  # No exception text or sensitive fallback logging.


def configured_audit():
    if os.environ.get('INDAI_LOCAL_SECURITY_AUDIT') != '1':
        return None
    return LocalAudit(os.environ['INDAI_SECURITY_AUDIT_FILE'])


def emit(event, stage, **kwargs):
    try:
        _emit(event, stage, **kwargs)
    except Exception:
        # Observability must never change authentication or business outcomes.
        scope = CURRENT.get()
        if scope is not None:
            scope[0].write_failed = True


def _emit(event, stage, *, principal=None, tenant=None, error=None, http_status=None):
    scope = CURRENT.get()
    if scope is None or event not in EVENTS:
        return
    sink, fields = scope
    if principal is not None:
        raw = json.dumps([principal.issuer, principal.subject]).encode()
        fields['principal'] = hmac.new(sink.key, raw, hashlib.sha256).hexdigest()
    if tenant is not None and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', tenant):
        fields['tenant'] = tenant
    record = {'timestamp': datetime.now(timezone.utc).isoformat(),
              'correlation_id': fields['correlation_id'], 'event': event, 'stage': stage}
    record.update({k: fields[k] for k in ('principal', 'tenant') if k in fields})
    if error is not None:
        record['exception_type'] = type(error).__name__ if type(error).__name__ in KINDS else 'SecurityError'
    if http_status is not None:
        record['http_status'] = http_status
    sink.write(record)


class AuditMiddleware:
    def __init__(self, app, sink):
        self.app, self.sink = app, sink

    async def __call__(self, scope, receive, send):
        if self.sink is None or scope['type'] != 'http' or scope['path'] != '/api/v1/analysis' or scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        values = [v.decode('ascii', errors='replace') for k,v in scope['headers'] if k.lower() == b'x-validation-id']
        correlation = values[0] if len(values) == 1 and re.fullmatch('[a-f0-9]{32}', values[0]) else uuid4().hex
        fields = {'correlation_id': correlation}
        token = CURRENT.set((self.sink, fields))
        status = 500
        async def audited_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            await send(message)
        try:
            emit('request_received', 'HTTP')
            await self.app(scope, receive, audited_send)
        finally:
            emit('request_completed', 'HTTP', http_status=status)
            CURRENT.reset(token)


def correlation_id():
    scope = CURRENT.get()
    return scope[1]['correlation_id'] if scope else uuid4().hex
