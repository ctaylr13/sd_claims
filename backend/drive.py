import csv
import io
import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

FOLDER_MIME = 'application/vnd.google-apps.folder'
SHORTCUT_MIME = 'application/vnd.google-apps.shortcut'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
FILE_FIELDS = 'id,name,mimeType,parents,size,modifiedTime,version,md5Checksum,webViewLink,shortcutDetails'
SHEET_MIME = 'application/vnd.google-apps.spreadsheet'
REPO_ROOT = Path(__file__).resolve().parents[1]


def load_sources(path=REPO_ROOT / 'drive-sources.json'):
    sources = json.loads(path.read_text())
    for key in ('claims_file_id', 'evidence_root_folder_id'):
        if not isinstance(sources.get(key), str) or not sources[key].strip():
            raise ValueError(f'Drive sources must contain {key}')
    return sources


def file_metadata(service, file_id):
    return service.files().get(
        fileId=file_id, fields='id,name,mimeType,modifiedTime,version,webViewLink,capabilities(canDownload)',
        supportsAllDrives=True,
    ).execute(num_retries=5)


def read_file(service, metadata, export_mime=None):
    if not metadata.get('capabilities', {}).get('canDownload', False):
        raise ValueError('Drive does not permit downloading this file')
    mime = metadata['mimeType']
    if mime in (FOLDER_MIME, SHORTCUT_MIME):
        raise ValueError('Folders and shortcuts cannot be read as documents')
    if mime.startswith('application/vnd.google-apps.'):
        if not export_mime:
            raise ValueError('A Google Workspace file needs an export MIME type')
        return service.files().export_media(
            fileId=metadata['id'], mimeType=export_mime,
        ).execute(num_retries=5)
    if export_mime:
        raise ValueError('Only Google Workspace files can be exported')
    return service.files().get_media(
        fileId=metadata['id'], supportsAllDrives=True,
    ).execute(num_retries=5)


def parse_claims(content):
    reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig'), newline=''))
    fields = reader.fieldnames or []
    if 'Tracking Number' not in fields:
        raise ValueError('Claims source must contain Tracking Number')
    rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError('CSV contains rows with an unexpected number of columns')
    return fields, rows


def read_claims(service, file_id):
    metadata = file_metadata(service, file_id)
    if metadata['mimeType'] not in (SHEET_MIME, 'text/csv', 'application/csv', 'application/vnd.ms-excel'):
        raise ValueError('Claims source must be a Google spreadsheet or CSV')
    content = read_file(service, metadata, 'text/csv' if metadata['mimeType'] == SHEET_MIME else None)
    fields, rows = parse_claims(content)
    source = {key: value for key, value in metadata.items() if key != 'capabilities'}
    source['read_at'] = datetime.now(timezone.utc).isoformat()
    return fields, rows, source


def write_json(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), 'w') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
    temporary.replace(path)


def connect_drive(client_path, token_path, *, allow_interactive=True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path))
        if not credentials.has_scopes(SCOPES):
            credentials = None
    changed = False
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        changed = True
    if not credentials or not credentials.valid:
        if not allow_interactive:
            raise ValueError('Drive authorization required; authorize with a command-line Drive tool first')
        if not client_path.is_file():
            raise ValueError(f'Download a Desktop app OAuth client JSON to {client_path} first')
        flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
        credentials = flow.run_local_server(port=0, prompt='consent')
        changed = True
    if changed:
        write_json(token_path, json.loads(credentials.to_json()))
    return build('drive', 'v3', credentials=credentials, cache_discovery=False)


def list_children(service, folder_id):
    files, page_token, seen_tokens = {}, None, set()
    escaped_id = folder_id.replace('\\', '\\\\').replace("'", "\\'")
    while True:
        page = service.files().list(
            q=f"'{escaped_id}' in parents and trashed = false",
            pageSize=1000, pageToken=page_token,
            fields=f'nextPageToken,incompleteSearch,files({FILE_FIELDS})',
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute(num_retries=5)
        if page.get('incompleteSearch'):
            raise ValueError(f'Drive returned an incomplete search for folder {folder_id}')
        for item in page.get('files', []):
            files[item['id']] = item
        page_token = page.get('nextPageToken')
        if not page_token:
            return list(files.values())
        if page_token in seen_tokens:
            raise ValueError('Drive repeated a pagination token')
        seen_tokens.add(page_token)


def inventory_claim(service, folder):
    pending, visited, items = deque([folder['id']]), set(), {}
    while pending:
        parent_id = pending.popleft()
        if parent_id in visited:
            continue
        visited.add(parent_id)
        for item in list_children(service, parent_id):
            items[item['id']] = item
            if item['mimeType'] == FOLDER_MIME:
                pending.append(item['id'])
    return {'folder': folder, 'items': list(items.values())}

