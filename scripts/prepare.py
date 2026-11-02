"""Generate local secrets once, without printing them."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
path = root / '.env'
if not path.exists():
    values = {'POSTGRES_PASSWORD': secrets.token_hex(24), 'MINIO_ROOT_USER': 'mayan-storage',
              'MINIO_ROOT_PASSWORD': secrets.token_hex(24),
              'MAYAN_ADMIN_USER': 'admin', 'MAYAN_ADMIN_PASSWORD': secrets.token_urlsafe(24),
              'WEBHOOK_TOKEN': secrets.token_hex(32)}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
if not any(line.startswith('WEBHOOK_TOKEN=') for line in path.read_text().splitlines()):
    with path.open('a') as stream:
        stream.write('WEBHOOK_TOKEN=' + secrets.token_hex(32) + '\n')
print('Local configuration ready; secrets are in mayan/.env (0600).')
