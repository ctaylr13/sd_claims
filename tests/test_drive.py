import unittest
from unittest.mock import Mock

from backend.drive import SHEET_MIME, parse_claims, read_claims, read_file


class DriveSourceTests(unittest.TestCase):
    def test_native_sheet_is_exported_in_memory_and_keeps_source_version(self):
        service = Mock()
        metadata = {'id': 'claims', 'name': 'Claims', 'mimeType': SHEET_MIME,
                    'version': '7', 'capabilities': {'canDownload': True}}
        service.files.return_value.get.return_value.execute.return_value = metadata
        service.files.return_value.export_media.return_value.execute.return_value = (
            b'\xef\xbb\xbfTracking Number,Status\r\n001,Approved\r\n'
        )
        fields, rows, source = read_claims(service, 'claims')
        self.assertEqual(fields, ['Tracking Number', 'Status'])
        self.assertEqual(rows[0]['Tracking Number'], '001')
        self.assertEqual(source['version'], '7')
        self.assertIn('read_at', source)
        service.files.return_value.export_media.assert_called_once_with(fileId='claims', mimeType='text/csv')
        service.files.return_value.get_media.assert_not_called()

    def test_stored_csv_uses_content_download(self):
        service = Mock()
        metadata = {'id': 'csv', 'mimeType': 'text/csv', 'capabilities': {'canDownload': True}}
        service.files.return_value.get.return_value.execute.return_value = metadata
        service.files.return_value.get_media.return_value.execute.return_value = b'Tracking Number\n002\n'
        self.assertEqual(read_claims(service, 'csv')[1][0]['Tracking Number'], '002')
        service.files.return_value.export_media.assert_not_called()

    def test_malformed_claims_are_rejected(self):
        for content in (b'Status\nApproved\n', b'Tracking Number,Status\n001\n',
                        b'Tracking Number,Status\n001,Approved,extra\n'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_claims(content)

    def test_pdf_bytes_use_download_and_not_export(self):
        service = Mock()
        metadata = {'id': 'pdf', 'mimeType': 'application/pdf', 'capabilities': {'canDownload': True}}
        service.files.return_value.get_media.return_value.execute.return_value = b'%PDF-test'
        self.assertEqual(read_file(service, metadata), b'%PDF-test')
        service.files.return_value.get_media.assert_called_once_with(fileId='pdf', supportsAllDrives=True)
        service.files.return_value.export_media.assert_not_called()

    def test_download_restrictions_and_shortcuts_are_respected(self):
        service = Mock()
        for metadata in (
            {'mimeType': 'application/pdf', 'capabilities': {'canDownload': False}},
            {'mimeType': 'application/vnd.google-apps.shortcut', 'capabilities': {'canDownload': True}},
        ):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                read_file(service, metadata)
        service.files.return_value.get_media.assert_not_called()


if __name__ == '__main__':
    unittest.main()
