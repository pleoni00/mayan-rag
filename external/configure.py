"""Configure native Mayan workflows using only its public REST API."""
import json
import os
from pathlib import Path
import time

import requests

from client import DOCUMENT_TYPE, Mayan

EVENTS = [
    'documents.document_create', 'documents.document_edit',
    'documents.document_file_created', 'documents.document_file_edited',
    'documents.document_version_created', 'documents.document_version_edited',
    'document_parsing.version_finish', 'ocr.document_version_finish',
    'acls.acl_created', 'acls.acl_edited', 'acls.acl_deleted',
]
api = Mayan()
for attempt in range(120):
    try:
        types = api.items('document_types/')
        break
    except requests.RequestException:
        if attempt == 119:
            raise
        time.sleep(2)
dtype = next((item for item in types if item['label'] == DOCUMENT_TYPE), None)
if dtype is None:
    dtype = api.call('POST', 'document_types/', json={'label': DOCUMENT_TYPE})
workflows = api.items('workflow_templates/')
workflow = next((item for item in workflows if item['internal_name'] == 'events_poc_http'), None)
if workflow is None:
    workflow = api.call('POST', 'workflow_templates/', json={
        'label': 'Events PoC HTTP', 'internal_name': 'events_poc_http',
        'auto_launch': False, 'ignore_completed': False,
    })
states = api.items(workflow['states_url'])
state = next((item for item in states if item['label'] == 'Notify'), None)
if state is None:
    state = api.call('POST', workflow['states_url'], json={'label': 'Notify', 'initial': True, 'final': False})
payload = json.dumps({
    'document_id': '{{ workflow_instance.document_id }}',
    'workflow_instance_id': '{{ workflow_instance.pk }}',
    'delivery_id': '{{ log_entry.pk }}',
    'verb': "{{ log_entry.transition.label|default:'documents.document_create' }}",
})
backend = {
    'url': 'http://events:8080/events', 'method': 'POST', 'timeout': '5',
    'headers': json.dumps({'Authorization': 'Bearer ' + os.environ['WEBHOOK_TOKEN']}),
    'payload': payload, 'username': '', 'password': '', 'verify_certificate': True,
    'response_store': True, 'response_store_name': 'last_callback',
}
actions = api.items(state['actions_url'])
action = next((item for item in actions if item['label'] == 'HTTP callback'), None)
data = {'label': 'HTTP callback', 'enabled': True, 'when': 1,
        'backend_path': 'mayan.apps.document_states.workflow_actions.HTTPAction',
        'backend_data': json.dumps(backend)}
api.call('PATCH' if action else 'POST', action['url'] if action else state['actions_url'], json=data)
transitions = api.items(workflow['transitions_url'])
for name in EVENTS:
    transition = next((item for item in transitions if item['label'] == name), None)
    if transition is None:
        transition = api.call('POST', workflow['transitions_url'], json={
            'label': name, 'origin_state_id': state['id'], 'destination_state_id': state['id'],
        })
    triggers = api.items(transition['trigger_list_url'])
    if not any(item['event_type']['id'] == name for item in triggers):
        api.call('POST', transition['trigger_list_url'], json={'event_type_id': name})
if dtype['id'] not in [item['id'] for item in api.items(workflow['document_types_url'])]:
    api.call('POST', workflow['document_types_add_url'], json={'document_type_id': dtype['id']})
api.call('PATCH', workflow['url'], json={'auto_launch': True})
Path('/state/workflow.json').write_text(json.dumps({
    'workflow_id': workflow['id'], 'document_type_id': dtype['id'],
    'callback_url': backend['url'], 'trigger_events': EVENTS,
}, indent=2))
print('Workflow nativo configurato: Mayan invia POST a http://events:8080/events.')
