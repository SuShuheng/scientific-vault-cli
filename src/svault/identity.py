"""Portable identity; old vaults remain readable without implicit registration."""
from pathlib import Path
import json
import uuid
from .context import VaultError
from . import __version__

def main_identity(root):
    root = Path(root)
    path = root / 'vault.json'
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if data['kind'] != 'main' or data['schema_version'] != 1 or not isinstance(data['vault_name'], str) or not data['vault_name']:
                raise ValueError()
            uuid.UUID(data['vault_id'])
        except (ValueError, KeyError, TypeError):
            raise VaultError('主库身份元数据无效')
        return data
    return {'schema_version': 1, 'kind': 'main', 'vault_id': None, 'vault_name': root.name, 'layout_version': '1.0.0', 'registered': False}

def main_name(root):
    return main_identity(root)['vault_name']

def new_main_identity(root):
    root = Path(root).resolve()
    return {'schema_version': 1, 'kind': 'main', 'vault_id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'svault:' + root.as_uri())), 'vault_name': root.name, 'layout_version': __version__}
