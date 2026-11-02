"""Five integration checks, exclusively through public Mayan and OpenSearch APIs."""
import hashlib
import json
from pathlib import Path
import secrets
import time

import requests

from client import DOCUMENT_TYPE, Mayan, hits

api = Mayan()
VIEW = 'documents.document_view'
state_path = Path('/state/events.json')
checks, documents, original_files = [], [], []


def state():
    return json.loads(state_path.read_text()) if state_path.exists() else {}


def wait(condition):
    deadline = time.monotonic() + 300
    last_error = None
    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = str(exc)
        time.sleep(2)
    raise AssertionError(f'Attesa scaduta. Ultimo errore di connessione: {last_error}. '
                         'Stato del componente esterno: ' + json.dumps(state()))


def passed(label):
    checks.append({'check': label, 'passed': True})
    print(f'[{len(checks)}/5] PASS — {label}', flush=True)


def fingerprint(doc_id):
    return hashlib.sha256(json.dumps(sorted(
        (hit['_id'], {key: value for key, value in hit['_source'].items() if key != 'acl'})
        for hit in hits(doc_id)
    ), sort_keys=True).encode()).hexdigest()


def indexed(doc_id, version=None, marker=None):
    result = hits(doc_id)
    return bool(result and all('acl' in hit['_source'] and len(hit['_source']['vector']) == 384
                              and (version is None or hit['_source']['version_id'] == version)
                              for hit in result)
                and (marker is None or marker in ' '.join(h['_source']['text'] for h in result))
                and str(doc_id) in state().get('documents', {}))


def permission_in_index(doc_id, role_id):
    return any(entry['role_id'] == role_id and VIEW in entry['permissions']
               for hit in hits(doc_id) for entry in hit['_source']['acl'])


wait(lambda: state().get('transport') == 'http' and requests.get('http://events:8080/health', timeout=3).ok)
suffix = secrets.token_hex(3)
types = api.items('document_types/')
dtype = next((item for item in types if item['label'] == DOCUMENT_TYPE), None)
if dtype is None:
    dtype = api.call('POST', 'document_types/', json={'label': DOCUMENT_TYPE})
api.call('PATCH', f'document_types/{dtype["id"]}/parsing/settings/', json={'auto_parsing': True})
api.call('PATCH', f'document_types/{dtype["id"]}/ocr/settings/', json={'auto_ocr': False})
for number in range(1, 4):
    body = f'Project {number}. Original content ORIGINAL-{number}-731.'.encode()
    doc = api.call('POST', 'documents/upload/', data={'document_type_id': dtype['id'], 'label': f'PoC {suffix} {number}'},
                   files={'file': (f'project-{number}.txt', body, 'text/plain')})
    documents.append(doc['id'])
    wait(lambda: api.call('GET', doc['url'])['file_latest'] is not None)
    file = api.call('GET', doc['url'])['file_latest']
    response = api.session.get(file['url'] + 'download/', timeout=30)
    response.raise_for_status()
    assert response.content == body
    original_files.append({'url': file['url'], 'key': file['file'], 'sha256': hashlib.sha256(body).hexdigest()})
passed('Tre documenti caricati e riletti tramite Mayan, configurato con storage MinIO')

users, roles = {}, {}
for name in ('A', 'B'):
    username, password = f'poc-{suffix}-{name}', secrets.token_urlsafe(24)
    user = api.call('POST', 'users/', json={'username': username, 'password': password})
    group = api.call('POST', 'groups/', json={'name': username})
    role = api.call('POST', 'roles/', json={'label': username})
    api.call('POST', group['users_add_url'], json={'user': user['id']})
    api.call('POST', role['groups_add_url'], json={'group': group['id']})
    users[name], roles[name] = Mayan(username, password), role['id']
acls = {}
for number, names in enumerate((('A',), ('A', 'B'), ('B',))):
    for name in names:
        entry = api.call('POST', f'objects/documents/document/{documents[number]}/acls/', json={'role_id': roles[name]})
        api.call('POST', entry['permissions_add_url'], json={'permission': VIEW})
        acls[(number, name)] = entry


def visible(name, doc_id):
    response = users[name].session.get(users[name].base + f'/api/v4/documents/{doc_id}/', timeout=30)
    assert response.status_code in (200, 403, 404), response.status_code
    return response.status_code == 200


assert [visible('A', doc) for doc in documents] == [True, True, False]
assert [visible('B', doc) for doc in documents] == [False, True, True]
passed('ACL native: A vede 1 e 2; B vede 2 e 3')
for doc_id in documents:
    wait(lambda doc_id=doc_id: indexed(doc_id))
for doc_id in documents:
    assert any(event['verb'] == 'documents.document_create' and event['transport'] == 'http'
               for event in state()['documents'][str(doc_id)]['events'])
    workflows = api.items(f'documents/{doc_id}/workflow_instances/')
    workflow = next(w for w in workflows if w['workflow_template']['internal_name'] == 'events_poc_http')
    assert workflow['context']['workflow_instance_context']['last_callback']['status_code'] == 202
passed('POST dei workflow nativi → componente esterno → chunk ed embedding, senza polling eventi')

doc_id = documents[1]
wait(lambda: permission_in_index(doc_id, roles['A']) and permission_in_index(doc_id, roles['B']))
before = fingerprint(doc_id)
embedding_runs = state()['documents'][str(doc_id)]['embedding_runs']
entry = acls[(1, 'A')]
api.call('POST', entry['permissions_remove_url'], json={'permission': VIEW})
assert not visible('A', doc_id) and visible('B', doc_id)
wait(lambda: not permission_in_index(doc_id, roles['A']))
assert before == fingerprint(doc_id)
assert embedding_runs == state()['documents'][str(doc_id)]['embedding_runs']
api.call('POST', entry['permissions_add_url'], json={'permission': VIEW})
wait(lambda: permission_in_index(doc_id, roles['A']))
assert before == fingerprint(doc_id)
passed('Evento ACL: metadati aggiornati, ID/testo/vettori invariati e nessun nuovo embedding')

doc_id = documents[0]
doc = api.call('GET', f'documents/{doc_id}/')
old_version = doc['version_active']
api.call('POST', doc['file_list_url'], data={'action_name': 'replace'},
         files={'file_new': ('project-1-v2.txt', b'Updated project VERSION-TWO-842.', 'text/plain')})
wait(lambda: api.call('GET', doc['url'])['version_active']['id'] != old_version['id'])
new_version = api.call('GET', doc['url'])['version_active']['id']
wait(lambda: indexed(doc_id, new_version, 'VERSION-TWO-842'))
assert 'ORIGINAL-1-731' not in ' '.join(h['_source']['text'] for h in hits(doc_id))
response = api.session.get(original_files[0]['url'] + 'download/', timeout=30)
response.raise_for_status()
assert hashlib.sha256(response.content).hexdigest() == original_files[0]['sha256']
api.call('PATCH', old_version['url'], json={'active': True})
wait(lambda: indexed(doc_id, old_version['id'], 'ORIGINAL-1-731'))
assert len(api.items(doc['file_list_url'])) == 2
assert any(event['verb'] == 'documents.document_version_edited'
           for event in state()['documents'][str(doc_id)]['events'])
passed('Nuova versione e rollback tramite API native; chunk coerenti e originali conservati')
Path('/state/test.json').write_text(json.dumps({
    'documents': documents, 'original_files': original_files, 'checks': checks,
    'events': {str(doc): state()['documents'][str(doc)] for doc in documents},
}, indent=2))
print('PASS: 5/5.', flush=True)
