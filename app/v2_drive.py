"""Drive v3 resumable transport. Host supplies existing auth; no login or token files.

Protocol references: developers.google.com/workspace/drive/api/guides/manage-uploads
and /reference/rest/v3/files. Nothing in this module runs on import.
"""
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .registry import Registry, StudioError, content_hash, workspace_path
from .v2_acceptance import fingerprint
from .v2_production import verify_portable
from .v2_run_config import no_links

FIELDS = 'id,name,size,parents,trashed,appProperties,sha256Checksum,md5Checksum'
CHUNK = 256*1024


class DriveError(StudioError):
    def __init__(self, kind):
        self.kind = kind
        super().__init__('DRIVE_'+kind.upper(), 'Drive delivery stopped: '+kind)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GoogleDriveHTTP:
    """Only documented REST calls; injected token and private session store are host-owned.

    session_store has get(key)/__setitem__; session URIs never enter manifests,
    receipts or stdout. Persistent sessions need an owner-approved protected store.
    """
    is_simulated = False
    def __init__(self, *, token_supplier, session_store, authorized=False, max_requests=100, timeout=30):
        if authorized is not True or not callable(token_supplier):
            raise DriveError('auth')
        self.token_supplier, self.sessions = token_supplier, session_store
        self.remaining, self.timeout = max_requests, timeout
        self.opener = build_opener(NoRedirects())

    def request(self, method, url, *, body=None, headers=None):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != 'www.googleapis.com' or not parsed.path.startswith(('/drive/v3/', '/upload/drive/v3/')):
            raise DriveError('unsupported')
        if self.remaining <= 0:
            raise DriveError('request_budget')
        self.remaining -= 1
        try:
            token = self.token_supplier()
            if not isinstance(token, str) or not token or '\n' in token or '\r' in token:
                raise DriveError('auth')
            req = Request(url, data=body, method=method,
                headers={'Authorization': 'Bearer '+token, **(headers or {})})
            with self.opener.open(req, timeout=self.timeout) as result:
                raw = result.read(1024*1024)
                return result.status, dict(result.headers), json.loads(raw) if raw else {}
        except HTTPError as exc:
            if exc.code == 308:
                return 308, dict(exc.headers), {}
            if exc.code == 404:
                raise DriveError('not_found') from None
            if exc.code == 401:
                raise DriveError('auth') from None
            if exc.code == 403:
                try:
                    reasons = {e.get('reason') for e in json.loads(exc.read(65536)).get('error', {}).get('errors', [])}
                except (ValueError, TypeError):
                    reasons = set()
                raise DriveError('quota' if reasons & {'dailyLimitExceeded', 'storageQuotaExceeded', 'rateLimitExceeded', 'userRateLimitExceeded'} else 'auth') from None
            raise DriveError('conflict' if exc.code == 409 else 'transient' if exc.code in {429,500,502,503,504} else 'unsupported') from None
        except (URLError, TimeoutError, OSError):
            raise DriveError('transient') from None

    def lookup(self, folder, name, key):
        # folder/name/key are host generated restricted IDs, never model queries.
        def quote(value):
            return value.replace('\\', '\\\\').replace("'", "\\'")
        query = f"'{quote(folder)}' in parents and trashed=false and name='{quote(name)}'"
        _, _, value = self.request('GET', 'https://www.googleapis.com/drive/v3/files?'+urlencode({
            'q': query, 'fields': 'files('+FIELDS+'),nextPageToken', 'pageSize': 100}))
        if value.get('nextPageToken') or len(value.get('files', [])) > 1:
            raise DriveError('conflict')
        items = value.get('files', [])
        if items and items[0].get('appProperties', {}).get('system_key') != key:
            raise DriveError('conflict')
        return items[0] if items else None

    def allocate(self):
        _, _, value = self.request('GET', 'https://www.googleapis.com/drive/v3/files/generateIds?count=1&space=drive&type=files')
        return value['ids'][0]

    def inspect(self, identifier):
        _, _, result = self.request('GET', 'https://www.googleapis.com/drive/v3/files/'+identifier+'?'+urlencode({'fields': FIELDS}))
        return result

    def position(self, identifier, total):
        session = self.sessions.get(identifier)
        if not session:
            return None
        try:
            status, headers, data = self.request('PUT', session, body=b'', headers={'Content-Range': 'bytes */'+str(total), 'Content-Length': '0'})
        except DriveError as exc:
            if exc.kind == 'not_found':
                return None
            raise
        return total if status in {200,201} else self.offset(headers)

    @staticmethod
    def offset(headers):
        value = next((v for k,v in headers.items() if k.lower() == 'range'), None)
        return int(value.rsplit('-',1)[1])+1 if value else 0

    def begin(self, identifier, folder, name, key, total, *, existing=False):
        metadata = {'id': identifier, 'name': name, 'parents': [folder], 'appProperties': {'system_key': key}}
        method = 'POST'
        url = 'https://www.googleapis.com/upload/drive/v3/files'
        if existing:
            # Resume only our exact incomplete object; never overwrite a conflicting file.
            method = 'PATCH'; url += '/'+identifier
            metadata = {'appProperties': {'system_key': key}}
        _, headers, _ = self.request(method, url+'?'+urlencode({'uploadType': 'resumable', 'fields': FIELDS}),
            body=json.dumps(metadata).encode(), headers={'Content-Type': 'application/json; charset=UTF-8',
                'X-Upload-Content-Type': 'application/octet-stream', 'X-Upload-Content-Length': str(total)})
        session = next((v for k,v in headers.items() if k.lower() == 'location'), None)
        if not session:
            raise DriveError('unsupported')
        self.sessions[identifier] = session
        return 0

    def chunk(self, identifier, offset, data, total):
        status, headers, _ = self.request('PUT', self.sessions[identifier], body=data,
            headers={'Content-Type': 'application/octet-stream', 'Content-Length': str(len(data)),
                'Content-Range': f'bytes {offset}-{offset+len(data)-1}/{total}'})
        return total if status in {200,201} else self.offset(headers)


def remote_verified(meta, *, identifier, folder, name, key, path):
    if (meta.get('id') != identifier or meta.get('name') != name or meta.get('trashed') is not False
            or meta.get('parents') != [folder] or meta.get('appProperties', {}).get('system_key') != key
            or int(meta.get('size', -1)) != path.stat().st_size):
        raise DriveError('conflict')
    if meta.get('sha256Checksum'):
        algorithm = 'sha256'; local = content_hash(path); remote = meta['sha256Checksum']
    elif meta.get('md5Checksum'):
        algorithm = 'md5'; digest = hashlib.md5(usedforsecurity=False)
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(CHUNK), b''):
                digest.update(chunk)
        local = digest.hexdigest(); remote = meta['md5Checksum']
    else:
        raise DriveError('checksum_unavailable')
    if remote.lower() != local:
        raise DriveError('checksum_mismatch')
    return {'remote_id': identifier, 'size': path.stat().st_size, 'verification': algorithm,
            'remote_checksum': remote.lower(), 'local_checksum': local}


def deliver_package(pipeline, package, transport):
    """Checkpoint each object in existing ai_events; no active Registry leaves the host."""
    config = pipeline.config
    pipeline.permit.validate()
    if config.mode == 'simulation' or 'deliver' not in config.operations or not config.drive or not config.drive.network_write_authorized:
        raise DriveError('permission')
    from .v2_run_config import graph
    if pipeline.accepted(graph(config)[-1]['task_id']) != package:
        raise DriveError('manifest_changed')
    folder = workspace_path(pipeline.root, package['portable'])
    suffixes = {'.mp4', '.mlt', '.json', '.srt', '.png', '.jpg', '.jpeg', '.wav'}
    manifest = verify_portable(folder, allowed_suffixes=suffixes)
    if content_hash(folder/'manifest.json') != package['portable_manifest_sha256']:
        raise DriveError('manifest_changed')
    key = fingerprint({'run_id': config.run_id, 'version': package['version'],
        'manifest': package['portable_manifest_sha256'], 'folder': config.drive.folder_id})
    def read(kind, item):
        with Registry(pipeline.q.root) as registry:
            row = registry.db.execute('SELECT payload FROM ai_events WHERE kind=? AND task_id=? ORDER BY sequence DESC LIMIT 1', (kind, item)).fetchone()
            return json.loads(row[0]) if row else None
    def save(kind, item, value):
        with Registry(pipeline.q.root) as registry, registry.transaction():
            pipeline.q.queue._event(registry, kind, value, item)
    prior = read('system_drive_delivery', key)
    if prior:
        return prior
    receipts = []
    deadline = time.monotonic()+config.budget.seconds
    spent = read('system_drive_budget', key) or {'requests': 0}
    requests = spent['requests']
    def call(method, *args, **kwargs):
        nonlocal requests
        for attempt in range(config.budget.attempts):
            if requests >= config.budget.external_requests or time.monotonic() > deadline:
                raise DriveError('request_budget')
            requests += 1
            # Reserve before transport execution, including failed/restarted calls.
            save('system_drive_budget', key, {'requests': requests})
            try:
                return getattr(transport, method)(*args, **kwargs)
            except DriveError as exc:
                if method == 'chunk' or exc.kind != 'transient' or attempt+1 >= config.budget.attempts:
                    raise
    for relative in sorted(set(manifest['files']) | {'manifest.json'}):
        path = no_links(workspace_path(folder, relative))
        object_key = fingerprint({'delivery': key, 'path': relative, 'sha256': content_hash(path)})
        name = object_key+'_'+path.name
        if len(name) > 200:
            name = object_key+path.suffix
        done = read('system_drive_object', object_key)
        if done:
            receipts.append(done)
            continue
        allocation = read('system_drive_allocation', object_key)
        meta = call('lookup', config.drive.folder_id, name, object_key)
        if meta:
            identifier = meta['id']
            if allocation and allocation['remote_id'] != identifier:
                raise DriveError('conflict')
        elif allocation:
            identifier = allocation['remote_id']
            try:
                meta = call('inspect', identifier)
            except DriveError as exc:
                if exc.kind != 'not_found':
                    raise
        else:
            identifier = call('allocate')
            save('system_drive_allocation', object_key, {'remote_id': identifier})
        if meta and int(meta.get('size', -1)) == path.stat().st_size:
            receipt = remote_verified(meta, identifier=identifier, folder=config.drive.folder_id, name=name, key=object_key, path=path)
        else:
            if meta and (meta.get('appProperties', {}).get('system_key') != object_key or meta.get('parents') != [config.drive.folder_id] or meta.get('name') != name):
                raise DriveError('conflict')
            offset = call('position', identifier, path.stat().st_size)
            if offset is None:
                offset = call('begin', identifier, config.drive.folder_id, name, object_key, path.stat().st_size, existing=bool(meta))
            if not 0 <= offset <= path.stat().st_size:
                raise DriveError('conflict')
            with path.open('rb') as stream:
                while offset < path.stat().st_size:
                    stream.seek(offset); chunk = stream.read(CHUNK)
                    # A transient lost chunk response must query the server offset,
                    # never blindly resend an accepted chunk or create another object.
                    try:
                        new_offset = call('chunk', identifier, offset, chunk, path.stat().st_size)
                    except DriveError as exc:
                        if exc.kind != 'transient':
                            raise
                        new_offset = call('position', identifier, path.stat().st_size)
                        if new_offset is None or new_offset <= offset:
                            raise
                    if new_offset <= offset or new_offset > offset+len(chunk):
                        raise DriveError('conflict')
                    offset = new_offset
            receipt = remote_verified(call('inspect', identifier), identifier=identifier, folder=config.drive.folder_id,
                name=name, key=object_key, path=path)
        receipt = {**receipt, 'relative_path': relative, 'sha256': content_hash(path)}
        save('system_drive_object', object_key, receipt)
        receipts.append(receipt)
    # Recheck the complete immutable local package before publishing host receipt.
    if verify_portable(folder, allowed_suffixes=suffixes) != manifest:
        raise DriveError('manifest_changed')
    simulated = getattr(transport, 'is_simulated', True) is not False
    result = {'status': 'SIMULATED_DELIVERY_VERIFIED' if simulated else 'UPLOADED_VERIFIED',
        'run_id': config.run_id, 'version': package['version'], 'delivery_key': key,
        'manifest_sha256': package['portable_manifest_sha256'], 'objects': receipts,
        'uploaded': not simulated, 'remote_sync': 'SIMULATED' if simulated else 'VERIFIED',
        'implementation_status': 'IMPLEMENTED_NOT_LIVE_TESTED', 'transport_simulated': simulated}
    save('system_drive_delivery', key, result)
    return result
