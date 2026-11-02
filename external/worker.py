"""Receive HTTP callbacks from native Mayan workflows; process them externally."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import threading
import json
from pathlib import Path
import time

import requests

from client import DOCUMENT_TYPE, INDEX, Mayan, search

api = Mayan()
path = Path('/state/events.json')
old = json.loads(path.read_text()) if path.exists() else {}
state = {'transport': 'http', 'documents': old.get('documents', {}),
         'pending': old.get('pending', {}) if old.get('transport') == 'http' else {},
         'received': old.get('received', []), 'ready': True}
condition = threading.Condition()
model = None
RELEVANT = {
    'documents.document_create', 'documents.document_edit',
    'documents.document_file_created', 'documents.document_file_edited',
    'documents.document_version_created', 'documents.document_version_edited',
    'document_parsing.version_finish', 'ocr.document_version_finish',
    'acls.acl_created', 'acls.acl_edited', 'acls.acl_deleted',
}


def save():
    state['updated_at'] = time.time()
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(path)


def acl(document):
    entries = api.items(f'objects/documents/document/{document}/acls/')
    return [{'role_id': entry['role']['id'],
             'permissions': sorted(p['pk'] for p in api.items(entry['permissions_url']))}
            for entry in entries]


def pieces(document):
    version = document['version_active']
    if not version:
        raise RuntimeError('Versione attiva non ancora disponibile')
    pages = api.items(version['page_list_url'])
    if not pages:
        raise RuntimeError('Pagine non ancora disponibili')
    file_pages = {}
    for file in api.items(document['file_list_url']):
        for page in api.items(file['page_list_url']):
            file_pages[page['id']] = page['url']
    result = []
    for page in pages:
        try:
            text = api.call('GET', page['url'] + 'ocr/')['content']
        except requests.HTTPError as exc:
            if exc.response.status_code != 404:
                raise
            text = ''
        if not text.strip():
            text = api.call('GET', file_pages[page['object_id']] + 'content/')['content']
        words = text.split()
        result.extend({'page': page['page_number'], 'text': ' '.join(words[i:i + 160])}
                      for i in range(0, len(words), 160))
    if not result:
        raise RuntimeError('Testo non ancora disponibile')
    return result


def synchronize(doc_id, events):
    global model
    document = api.call('GET', f'documents/{doc_id}/')
    if document['document_type']['label'] != DOCUMENT_TYPE:
        return
    version = document['version_active']
    previous = dict(state['documents'].get(str(doc_id), {}))
    policy = acl(doc_id)
    only_acl = all(event['verb'].startswith('acls.') for event in events)
    if only_acl and version and previous.get('version_id') == version['id']:
        fingerprint = previous['fingerprint']
    else:
        chunks = pieces(document)
        fingerprint = hashlib.sha256(json.dumps([version['id'], chunks], sort_keys=True).encode()).hexdigest()
        if fingerprint != previous.get('fingerprint'):
            if model is None:
                from fastembed import TextEmbedding
                model = TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
                                      specific_model_path='/models/manual', threads=2)
            vectors = list(model.embed([chunk['text'] for chunk in chunks], batch_size=4))
            if api.call('GET', document['url'])['version_active']['id'] != version['id']:
                raise RuntimeError('Versione cambiata durante l’indicizzazione')
            operations = []
            for number, (chunk, vector) in enumerate(zip(chunks, vectors)):
                operations.extend([
                    {'index': {'_index': INDEX, '_id': f'{doc_id}:{fingerprint}:{number}'}},
                    dict(chunk, document_id=doc_id, version_id=version['id'], fingerprint=fingerprint,
                         label=document['label'], acl=policy, vector=vector.tolist()),
                ])
            body = '\n'.join(json.dumps(item) for item in operations) + '\n'
            response = search('POST', '/_bulk?refresh=wait_for', data=body,
                              headers={'Content-Type': 'application/x-ndjson'})
            if response.get('errors'):
                raise RuntimeError('Errore scrittura chunk OpenSearch')
            search('POST', f'/{INDEX}/_delete_by_query?refresh=true', json={'query': {'bool': {
                'filter': [{'term': {'document_id': doc_id}}],
                'must_not': [{'term': {'fingerprint': fingerprint}}],
            }}})
            previous['embedding_runs'] = previous.get('embedding_runs', 0) + 1
    search('POST', f'/{INDEX}/_update_by_query?refresh=true', json={
        'query': {'term': {'document_id': doc_id}},
        'script': {'source': 'ctx._source.acl = params.acl; ctx._source.label = params.label',
                   'params': {'acl': policy, 'label': document['label']}},
    })
    previous.update(version_id=version['id'], fingerprint=fingerprint,
                    events=(previous.get('events', []) + events)[-50:])
    print(f'Documento {doc_id}: versione {version["id"]}, callback {[e["verb"] for e in events]}', flush=True)
    return previous


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/health' else 404)
        self.end_headers()
        self.wfile.write(b'{"ready":true}')

    def do_POST(self):
        if self.path != '/events':
            self.send_error(404)
            return
        expected = 'Bearer ' + os.environ['WEBHOOK_TOKEN']
        if not hmac.compare_digest(self.headers.get('Authorization', ''), expected):
            self.send_error(401)
            return
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 4096:
                raise ValueError('Invalid size')
            message = json.loads(self.rfile.read(size))
            doc_id, delivery_id = int(message['document_id']), int(message['delivery_id'])
            if doc_id <= 0 or delivery_id <= 0 or message['verb'] not in RELEVANT:
                raise ValueError('Invalid event')
            event = {'id': delivery_id, 'verb': message['verb'], 'transport': 'http',
                     'workflow_instance_id': int(message['workflow_instance_id'])}
        except (KeyError, ValueError, TypeError):
            self.send_error(400)
            return
        with condition:
            if delivery_id not in [entry['id'] for entry in state['received']]:
                state['pending'].setdefault(str(doc_id), []).append(event)
                state['received'] = (state['received'] + [dict(event, document_id=doc_id)])[-100:]
                save()
                condition.notify()
        self.send_response(202)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"accepted":true}')

    def log_message(self, *args):
        pass


def process_received():
    while True:
        with condition:
            condition.wait_for(lambda: bool(state['pending']))
            doc_id = next(iter(state['pending']))
            events = list(state['pending'][doc_id])
        try:
            result = synchronize(int(doc_id), events)
            with condition:
                if result is not None:
                    state['documents'][doc_id] = result
                processed = {event['id'] for event in events}
                remaining = [e for e in state['pending'][doc_id] if e['id'] not in processed]
                if remaining:
                    state['pending'][doc_id] = remaining
                else:
                    del state['pending'][doc_id]
                state.setdefault('errors', {}).pop(doc_id, None)
                save()
        except Exception as exc:
            with condition:
                state.setdefault('errors', {})[doc_id] = str(exc)
                save()
                # Retry only an already received callback when document content
                # is not ready yet. No event feed is queried.
                condition.wait(timeout=2)


if requests.get(os.environ.get('OPENSEARCH_URL', 'http://opensearch:9200') + '/' + INDEX, timeout=10).status_code == 404:
    search('PUT', '/' + INDEX, json={
        'settings': {'index.knn': True, 'number_of_shards': 1, 'number_of_replicas': 0},
        'mappings': {'properties': {
            'document_id': {'type': 'integer'}, 'version_id': {'type': 'integer'},
            'fingerprint': {'type': 'keyword'}, 'page': {'type': 'integer'},
            'text': {'type': 'text'}, 'label': {'type': 'keyword'},
            'acl': {'type': 'nested', 'properties': {'role_id': {'type': 'integer'}, 'permissions': {'type': 'keyword'}}},
            'vector': {'type': 'knn_vector', 'dimension': 384,
                       'method': {'name': 'hnsw', 'engine': 'lucene', 'space_type': 'cosinesimil'}},
        }},
    })
save()
threading.Thread(target=process_received, daemon=True).start()
print('HTTP receiver ready on :8080/events', flush=True)
ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
