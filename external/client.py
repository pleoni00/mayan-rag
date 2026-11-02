import os
import time

import requests

INDEX = 'mayan-events-poc'
DOCUMENT_TYPE = 'Events PoC'


class Mayan:
    def __init__(self, username=None, password=None):
        self.base = os.environ.get('MAYAN_URL', 'http://mayan:8000')
        self.session = requests.Session()
        self.session.auth = (username or os.environ['MAYAN_ADMIN_USER'],
                             password or os.environ['MAYAN_ADMIN_PASSWORD'])

    def call(self, method, path, **kwargs):
        url = path if path.startswith('http') else self.base + '/api/v4/' + path
        attempts = 4 if method == 'GET' else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(method, url, timeout=30, **kwargs)
                response.raise_for_status()
                return response.json() if response.content else None
            except (requests.ConnectionError, requests.Timeout):
                if attempt == attempts - 1:
                    raise
            except requests.HTTPError as exc:
                if attempt == attempts - 1 or exc.response.status_code not in (502, 503, 504):
                    raise
            time.sleep(0.5 * 2 ** attempt)

    def items(self, path):
        result = []
        while path:
            page = self.call('GET', path)
            result.extend(page['results'])
            path = page.get('next')
        return result


def search(method, path, **kwargs):
    response = requests.request(method, os.environ.get('OPENSEARCH_URL', 'http://opensearch:9200') + path,
                                timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


def hits(document_id):
    return search('POST', f'/{INDEX}/_search', json={
        'size': 100, 'query': {'term': {'document_id': document_id}}
    })['hits']['hits']
