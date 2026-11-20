"""
Rasmlar (media) zaxirasi (egasi talabi 2026-11-20): tungi baza zaxirasi bilan birga Telegramga. Dushanba — to'liq
arxiv, boshqa kunlar — dushanbadan beri o'zgarganlar; 45 MB dan katta arxiv qismlarga bo'linadi; hisobot yuborilmasa
rasmlar ham yuborilmaydi (qayta urinishda takror yo'q).
"""
import datetime
import io
import os
import shutil
import tarfile
import tempfile
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from production import media_backup
from production.management.commands import run_scheduler

MONDAY = timezone.make_aware(datetime.datetime(2026, 11, 16, 0, 5))
WEDNESDAY = timezone.make_aware(datetime.datetime(2026, 11, 18, 0, 5))


class MediaBackupTest(TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='media_test_')
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.root, TELEGRAM_BOT_TOKEN='token', TELEGRAM_REPORT_CHAT_ID='-100')
        override.enable()
        self.addCleanup(override.disable)
        self.calls = []
        patcher = mock.patch.object(media_backup, '_post', side_effect=self.fake_post)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_post(self, client, token, method, data, files=None):
        name, content = (files['document'][0], files['document'][1]) if files else (None, None)
        self.calls.append({'method': method, 'caption': data.get('caption') or data.get('text'), 'name': name,
                           'content': content})
        return None

    def file(self, rel, when, size=10):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(os.urandom(size))
        os.utime(path, (when.timestamp(), when.timestamp()))

    def members(self):
        data = b''.join(c['content'] for c in self.calls if c['method'] == 'sendDocument')
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            return sorted(tar.getnames())

    def test_monday_sends_full_archive_without_hidden_files(self):
        self.file('articles/2026/09/model.jpg', MONDAY - datetime.timedelta(days=60))
        self.file('qr_codes/users/user_qr_123456.png', MONDAY - datetime.timedelta(days=30))
        self.file('.DS_Store', MONDAY)
        result = media_backup.send_media_backup(now=MONDAY)
        self.assertEqual((result['success'], result['files'], result['parts']), (True, 2, 1))
        self.assertEqual([c['name'] for c in self.calls], ['terryjar_media_2026_11_16_full.tar.gz'])
        self.assertIn("TO'LIQ", self.calls[0]['caption'])
        self.assertEqual(self.members(), ['articles/2026/09/model.jpg', 'qr_codes/users/user_qr_123456.png'])

    def test_other_nights_send_only_files_changed_since_monday(self):
        self.file('articles/old.jpg', MONDAY - datetime.timedelta(days=3))
        self.file('awards/2026/11/photo_new.jpg', MONDAY + datetime.timedelta(days=1))
        result = media_backup.send_media_backup(now=WEDNESDAY)
        self.assertEqual((result['success'], result['files']), (True, 1))
        self.assertEqual(self.calls[0]['name'], 'terryjar_media_2026_11_18_farq.tar.gz')
        self.assertIn('16.11', self.calls[0]['caption'])
        self.assertEqual(self.members(), ['awards/2026/11/photo_new.jpg'])

    def test_full_flag_overrides_weekday(self):
        self.file('articles/old.jpg', MONDAY - datetime.timedelta(days=3))
        with mock.patch.object(media_backup.timezone, 'now', return_value=WEDNESDAY):
            call_command('send_media_backup', '--full', stdout=io.StringIO())
        self.assertEqual(self.members(), ['articles/old.jpg'])

    def test_large_archive_is_split_into_parts_that_join_back(self):
        for n in range(6):
            self.file(f'articles/model_{n}.jpg', MONDAY, size=3000)
        with mock.patch.object(media_backup, 'PART_SIZE', 8192):
            result = media_backup.send_media_backup(now=MONDAY)
        self.assertGreater(result['parts'], 1)
        names = [c['name'] for c in self.calls]
        self.assertEqual(names, [f'terryjar_media_2026_11_16_full.tar.gz.{i:03d}' for i in range(1, len(names) + 1)])
        self.assertIn(f"1/{len(names)}-qism", self.calls[0]['caption'])
        self.assertIn('cat terryjar_media_2026_11_16_full.tar.gz.0*', self.calls[-1]['caption'])
        self.assertEqual(self.members(), [f'articles/model_{n}.jpg' for n in range(6)])

    def test_no_new_files_sends_short_message(self):
        self.file('articles/old.jpg', MONDAY - datetime.timedelta(days=3))
        result = media_backup.send_media_backup(now=WEDNESDAY)
        self.assertEqual((result['success'], result['files']), (True, 0))
        self.assertEqual([c['method'] for c in self.calls], ['sendMessage'])
        self.assertIn("yangi yoki o'zgargan rasm yo'q", self.calls[0]['caption'])


class NightlyJobsMediaTest(TestCase):
    @mock.patch.object(run_scheduler, 'perform_daily_closing', return_value={})
    def test_media_sent_only_after_report_succeeds(self, _closing):
        day = datetime.date(2026, 11, 17)
        with mock.patch.object(run_scheduler, 'send_month_to_date_telegram_report',
                               return_value={'success': False, 'message': 'tarmoq'}), \
                mock.patch.object(run_scheduler, 'send_media_backup') as media:
            res = run_scheduler.execute_nightly_jobs(day)
        media.assert_not_called()
        self.assertNotIn('media', res)

        with mock.patch.object(run_scheduler, 'send_month_to_date_telegram_report', return_value={'success': True}), \
                mock.patch.object(run_scheduler, 'send_media_backup', side_effect=RuntimeError('disk')) as media:
            res = run_scheduler.execute_nightly_jobs(day)
        media.assert_called_once()
        self.assertEqual(res['media'], {'success': False, 'message': 'disk'})   # xato hisobotni buzmaydi
        self.assertTrue(res['report']['success'])
