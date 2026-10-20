import argparse
import json
import os
from pathlib import Path

from .drive_source import (
    FOLDER_MIME, add_drive_arguments, connect_from_args, file_metadata,
    inventory_claim, list_children, read_claims, read_file,
)


def find_claim_evidence(service, root_id, tracking_number):
    folders = [item for item in list_children(service, root_id)
               if item['mimeType'] == FOLDER_MIME and item['name'].strip() == tracking_number]
    if not folders:
        raise ValueError('No evidence folder visible for this tracking number')
    if len(folders) != 1:
        raise ValueError('Multiple evidence folders match this tracking number; resolve the duplicate first')
    return inventory_claim(service, folders[0])


def main():
    parser = argparse.ArgumentParser(description='Look up claim records and evidence directly in Drive.')
    add_drive_arguments(parser)
    parser.add_argument('tracking_number')
    parser.add_argument('--file-id')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--export-mime', help='Required when saving a native Google Workspace document')
    args = parser.parse_args()
    if bool(args.file_id) != bool(args.output):
        parser.error('--file-id and --output must be supplied together')
    if args.export_mime and not args.file_id:
        parser.error('--export-mime requires --file-id and --output')
    try:
        service, sources = connect_from_args(args)
        _, rows, source = read_claims(service, sources['claims_file_id'])
        matches = [row for row in rows if row['Tracking Number'].strip() == args.tracking_number]
        if not matches:
            raise ValueError('Tracking number is not in the live claims source')
        evidence = find_claim_evidence(service, sources['evidence_root_folder_id'], args.tracking_number)
        if args.file_id:
            if args.file_id not in {item['id'] for item in evidence['items']}:
                raise ValueError('Requested file is not in this claim evidence folder')
            metadata = file_metadata(service, args.file_id)
            content = read_file(service, metadata, args.export_mime)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as target:
                target.write(content)
            print(f'Saved Drive file to {args.output}')
        else:
            print(json.dumps({'claims_source': source, 'claim_records': matches, 'evidence': evidence}, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
