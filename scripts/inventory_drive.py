import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .drive_source import (
    FOLDER_MIME, SHORTCUT_MIME, add_drive_arguments, connect_from_args,
    inventory_claim, list_children, read_claims, write_json,
)


def summarize(inventory, tracking_numbers):
    folders_by_name = defaultdict(list)
    for item in inventory['root_items']:
        if item['mimeType'] == FOLDER_MIME:
            folders_by_name[item['name'].strip()].append(item['id'])
    folder_names = set(folders_by_name)
    mime_counts, checksum_groups = Counter(), defaultdict(list)
    empty, shortcuts, filename_duplicates = [], [], []
    for claim in inventory['claims'].values():
        files = [item for item in claim['items'] if item['mimeType'] != FOLDER_MIME]
        if not files:
            empty.append(claim['folder']['id'])
        names = defaultdict(list)
        for item in files:
            mime_counts[item['mimeType']] += 1
            names[item['name']].append(item['id'])
            if item.get('md5Checksum'):
                checksum_groups[item['md5Checksum']].append(item['id'])
            if item['mimeType'] == SHORTCUT_MIME:
                shortcuts.append(item['id'])
        for name, ids in names.items():
            if len(ids) > 1:
                filename_duplicates.append({'folder_id': claim['folder']['id'], 'name': name, 'file_ids': ids})
    return {
        'generated_at': inventory['updated_at'],
        'claims_source': inventory.get('claims_source'),
        'root_folder_id': inventory['root_folder_id'],
        'inventory_complete': inventory['complete'],
        'csv_unique_tracking_numbers': len(tracking_numbers),
        'root_claim_folders': sum(map(len, folders_by_name.values())),
        'matched_csv_tracking_numbers': len(tracking_numbers & folder_names),
        'missing_claim_folders': sorted(tracking_numbers - folder_names),
        'unmatched_folder_names': sorted(folder_names - tracking_numbers),
        'duplicate_folder_names': {name: ids for name, ids in folders_by_name.items() if len(ids) > 1},
        'inventoried_claim_folders': len(inventory['claims']),
        'folder_errors': inventory['errors'],
        'empty_claim_folder_ids': empty,
        'file_mime_counts': dict(mime_counts),
        'duplicate_content_file_ids': [ids for ids in checksum_groups.values() if len(set(ids)) > 1],
        'repeated_filenames_within_claim': filename_duplicates,
        'unresolved_shortcut_ids': shortcuts,
        'non_folder_root_items': [item for item in inventory['root_items'] if item['mimeType'] != FOLDER_MIME],
        'limitations': 'Metadata inventory only. ZIP contents, document text, historical revisions, and decision-time availability are not examined. Missing folders mean not visible to the authenticated account under this root at inventory time.',
    }


def run_inventory(service, root_id, output_dir, tracking_numbers, resume, claims_source=None):
    from googleapiclient.errors import HttpError

    inventory_path = output_dir / 'drive-inventory.json'
    if resume and inventory_path.exists():
        inventory = json.loads(inventory_path.read_text())
        if inventory['root_folder_id'] != root_id:
            raise ValueError('Resume inventory belongs to another root folder')
        print('Resuming saved metadata snapshot; use a fresh run to discover changes.', flush=True)
    else:
        root = service.files().get(
            fileId=root_id, fields='id,name,mimeType', supportsAllDrives=True,
        ).execute(num_retries=5)
        if root['mimeType'] != FOLDER_MIME:
            raise ValueError('The evidence root must be a Drive folder')
        inventory = {
            'root_folder_id': root_id, 'root_name': root['name'],
            'started_at': datetime.now(timezone.utc).isoformat(),
            'root_items': list_children(service, root_id), 'claims': {}, 'errors': {},
        }
    inventory['claims_source'] = claims_source
    inventory['complete'] = False
    inventory['updated_at'] = datetime.now(timezone.utc).isoformat()
    write_json(inventory_path, inventory)
    folders = [item for item in inventory['root_items'] if item['mimeType'] == FOLDER_MIME]
    for index, folder in enumerate(folders, 1):
        if folder['id'] in inventory['claims']:
            continue
        try:
            inventory['claims'][folder['id']] = inventory_claim(service, folder)
            inventory['errors'].pop(folder['id'], None)
        except HttpError as error:
            inventory['errors'][folder['id']] = f'HTTP {error.resp.status}'
            if error.resp.status in (401, 429) or error.resp.status >= 500:
                break
        except ValueError as error:
            inventory['errors'][folder['id']] = str(error)
        inventory['updated_at'] = datetime.now(timezone.utc).isoformat()
        write_json(inventory_path, inventory)
        print(f'Listed {index}/{len(folders)} claim folders', flush=True)
    inventory['complete'] = len(inventory['claims']) == len(folders) and not inventory['errors']
    inventory['updated_at'] = datetime.now(timezone.utc).isoformat()
    write_json(inventory_path, inventory)
    summary = summarize(inventory, tracking_numbers)
    write_json(output_dir / 'drive-inventory-summary.json', summary)
    return inventory['complete']


def main():
    parser = argparse.ArgumentParser(description='Inventory claim evidence metadata through the read-only Drive API.')
    add_drive_arguments(parser)
    parser.add_argument('--output-dir', type=Path, default=Path('data'))
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        service, sources = connect_from_args(args)
        _, rows, source = read_claims(service, sources['claims_file_id'])
        tracking_numbers = {row['Tracking Number'].strip() for row in rows if row['Tracking Number'].strip()}
        complete = run_inventory(service, sources['evidence_root_folder_id'], args.output_dir, tracking_numbers, args.resume, source)
    except ImportError:
        parser.exit(1, 'Install Drive dependencies: python3 -m pip install -r requirements-drive.txt\n')
    except ValueError as error:
        parser.exit(1, f'{error}\n')
    except KeyboardInterrupt:
        parser.exit(130, '\nStopped. Use --resume to continue the last saved snapshot.\n')
    print(f'Inventory {"complete" if complete else "partial"}. Reports saved in {args.output_dir}.')
    return 0 if complete else 1


if __name__ == '__main__':
    sys.exit(main())
