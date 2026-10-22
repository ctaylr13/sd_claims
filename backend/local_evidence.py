from pathlib import Path
from urllib.parse import quote


def claim_folder(root, tracking_number):
    root = Path(root).resolve()
    if tracking_number in ('', '.', '..') or Path(tracking_number).name != tracking_number:
        return None
    folder = root / tracking_number
    if folder.parent != root or folder.is_symlink() or not folder.is_dir():
        return None
    return folder


def evidence_file(root, tracking_number, relative_path):
    folder = claim_folder(root, tracking_number)
    if folder is None:
        return None
    relative = Path(relative_path)
    if relative.is_absolute() or '..' in relative.parts:
        return None
    path = folder / relative
    if any((folder / part).is_symlink() for part in (relative, *relative.parents)):
        return None
    resolved = path.resolve()
    if not resolved.is_relative_to(folder) or resolved.suffix.lower() != '.pdf' or not resolved.is_file():
        return None
    return resolved


def inventory_local_claim(root, tracking_number):
    folder = claim_folder(root, tracking_number)
    if folder is None:
        return {'folder': None, 'items': []}
    items = []
    for path in sorted(folder.rglob('*')):
        relative = path.relative_to(folder).as_posix()
        if evidence_file(root, tracking_number, relative) is None:
            continue
        items.append({
            'id': relative, 'name': relative, 'mimeType': 'application/pdf',
            'webViewLink': f'/api/claims/{quote(tracking_number, safe="")}/evidence/{quote(relative, safe="/")}',
        })
    return {'folder': {'id': tracking_number, 'name': tracking_number, 'mode': 'local'}, 'items': items}
