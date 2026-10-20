import unittest
from unittest.mock import Mock

from scripts.inventory_drive import FOLDER_MIME, inventory_claim, list_children, summarize


def folder(folder_id, name):
    return {'id': folder_id, 'name': name, 'mimeType': FOLDER_MIME}


def document(file_id, name='lease.pdf', checksum='same'):
    return {'id': file_id, 'name': name, 'mimeType': 'application/pdf', 'md5Checksum': checksum}


class InventoryTests(unittest.TestCase):
    def test_pagination_continues_after_empty_page_and_deduplicates_ids(self):
        service = Mock()
        service.files.return_value.list.return_value.execute.side_effect = [
            {'files': [], 'nextPageToken': 'next'},
            {'files': [document('a'), document('a')]},
        ]
        self.assertEqual([item['id'] for item in list_children(service, 'root')], ['a'])
        calls = service.files.return_value.list.call_args_list
        self.assertEqual(calls[1].kwargs['pageToken'], 'next')

    def test_incomplete_search_cannot_be_reported_as_complete(self):
        service = Mock()
        service.files.return_value.list.return_value.execute.return_value = {'incompleteSearch': True}
        with self.assertRaisesRegex(ValueError, 'incomplete search'):
            list_children(service, 'root')

    def test_repeated_page_token_stops_pagination(self):
        service = Mock()
        service.files.return_value.list.return_value.execute.return_value = {'nextPageToken': 'same'}
        with self.assertRaisesRegex(ValueError, 'repeated'):
            list_children(service, 'root')

    def test_nested_folders_are_walked_without_following_shortcuts(self):
        service = Mock()
        shortcut = {'id': 'link', 'name': 'shortcut', 'mimeType': 'application/vnd.google-apps.shortcut'}
        service.files.return_value.list.return_value.execute.side_effect = [
            {'files': [folder('nested', 'supporting'), shortcut]},
            {'files': [document('pdf')]},
        ]
        result = inventory_claim(service, folder('claim', '001'))
        self.assertEqual({item['id'] for item in result['items']}, {'nested', 'link', 'pdf'})
        self.assertEqual(service.files.return_value.list.call_count, 2)

    def test_summary_preserves_tracking_number_strings_and_reports_gaps(self):
        root_items = [folder('a', '001'), folder('b', '001'), folder('c', 'extra')]
        inventory = {
            'updated_at': 'now', 'root_folder_id': 'root', 'complete': False,
            'root_items': root_items, 'errors': {'b': 'HTTP 403'},
            'claims': {
                'a': {'folder': root_items[0], 'items': [document('one'), document('two')]},
                'c': {'folder': root_items[2], 'items': []},
            },
        }
        report = summarize(inventory, {'001', '1', 'missing'})
        self.assertEqual(report['missing_claim_folders'], ['1', 'missing'])
        self.assertEqual(report['duplicate_folder_names'], {'001': ['a', 'b']})
        self.assertEqual(report['unmatched_folder_names'], ['extra'])
        self.assertEqual(report['duplicate_content_file_ids'], [['one', 'two']])
        self.assertEqual(report['empty_claim_folder_ids'], ['c'])
        self.assertEqual(report['file_mime_counts'], {'application/pdf': 2})
        self.assertFalse(report['inventory_complete'])
        self.assertEqual(report['folder_errors'], {'b': 'HTTP 403'})


if __name__ == '__main__':
    unittest.main()
