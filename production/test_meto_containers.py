"""
Meto Markazi → Konteynerlar (egasi talabi 2026-11-19, yangi meto oqimining 1-bosqichi):
yaratish (ketma-ket KONT-000123), 100×80 mm yorliq, kuzatish va QR skaner.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from production.container_services import parse_container_code
from production.models import Container, ContainerEvent


class MetoContainerTest(TestCase):
    def setUp(self):
        self.meto = User.objects.create_user(username='metochi_k', password='x', role=User.Role.METO)
        self.client.force_login(self.meto)
        self.url = reverse('meto_hub_containers')

    def create(self, count, kind='BIG'):
        return self.client.post(reverse('meto_hub_container_create'), {'soni': count, 'tur': kind})

    def labels(self, **params):
        return self.client.get(reverse('meto_hub_container_labels'), params)

    def test_numbers_continue_and_creation_is_logged(self):
        res = self.create(3)
        self.assertRedirects(res, f"{self.url}?yaratildi_dan=1&yaratildi_gacha=3", fetch_redirect_response=False)
        self.create(2, 'SMALL')
        self.assertEqual(list(Container.objects.values_list('number', 'kind')),
                         [(1, 'BIG'), (2, 'BIG'), (3, 'BIG'), (4, 'SMALL'), (5, 'SMALL')])
        self.assertEqual(Container.objects.get(number=4).code, 'KONT-000004')
        self.assertEqual(ContainerEvent.objects.filter(action=ContainerEvent.Action.CREATED).count(), 5)
        for container in Container.objects.all():
            self.assertEqual((container.status, container.branch, container.created_by_id),
                             (Container.Status.EMPTY, 'HQ', self.meto.id))

    def test_bad_input_creates_nothing(self):
        for data in ({'soni': 0, 'tur': 'BIG'}, {'soni': 1001, 'tur': 'BIG'}, {'soni': 'abc', 'tur': 'BIG'},
                     {'soni': 5, 'tur': 'XYZ'}):
            self.client.post(reverse('meto_hub_container_create'), data)
        self.assertFalse(Container.objects.exists())

    def test_label_page_prints_the_range_and_logs_it(self):
        self.create(3)
        res = self.labels(dan='KONT-000001', gacha='2')
        self.assertContains(res, 'alt="KONT-000001"')
        self.assertContains(res, 'alt="KONT-000002"')
        self.assertNotContains(res, 'alt="KONT-000003"')
        self.assertContains(res, 'size: 100mm 80mm')
        self.assertEqual(Container.objects.get(number=1).print_count, 1)
        self.assertIsNotNone(Container.objects.get(number=2).last_printed_at)
        self.assertEqual(Container.objects.get(number=3).print_count, 0)
        self.assertEqual(ContainerEvent.objects.filter(action=ContainerEvent.Action.PRINTED).count(), 2)
        self.labels(dan=1)   # bitta yorliqni qayta chop etish — o'sha ID
        self.assertEqual(Container.objects.get(number=1).print_count, 2)

    def test_label_page_limits(self):
        self.create(3)
        for params in ({'dan': 1, 'gacha': 501}, {'dan': 50, 'gacha': 60}, {'dan': 'abc'}, {'dan': 3, 'gacha': 1}):
            self.assertRedirects(self.labels(**params), self.url, fetch_redirect_response=False)
        self.assertFalse(ContainerEvent.objects.filter(action=ContainerEvent.Action.PRINTED).exists())

    def test_scan_opens_the_card_and_filters_work(self):
        self.create(2)
        self.create(1, 'SMALL')
        res = self.client.get(self.url, {'q': 'kont+000003'})
        self.assertRedirects(res, reverse('meto_hub_container_detail', args=[3]), fetch_redirect_response=False)
        page = self.client.get(self.url, {'tur': 'SMALL'})
        self.assertEqual([c.number for c in page.context['page']], [3])
        self.assertEqual((page.context['summary']['total'], page.context['summary']['unprinted']), (3, 3))
        self.assertEqual(list(self.client.get(self.url, {'q': 'KONT-000099'}).context['page']), [])
        detail = self.client.get(reverse('meto_hub_container_detail', args=[3]))
        self.assertContains(detail, 'KONT-000003')
        self.assertContains(detail, 'Yaratildi')

    def test_print_buttons_after_a_big_creation(self):
        page = self.client.get(self.url, {'yaratildi_dan': 1, 'yaratildi_gacha': 1200})
        self.assertEqual(page.context['print_chunks'], [(1, 500), (501, 1000), (1001, 1200)])
        self.assertContains(page, 'KONT-000001')

    def test_parse_container_code(self):
        cases = {'KONT-000123': 123, 'kont+123': 123, 'KONT:000123': 123, 'KONT000123': 123,
                 'CONTROL:A1-1': None, '123': None, '': None, None: None, 'KONT-12A': None}
        for raw, expected in cases.items():
            self.assertEqual(parse_container_code(raw), expected, raw)

    def test_only_meto_hub_roles_get_in(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        technologist = User.objects.create_user(username='texnolog_k', password='x', role=User.Role.TECHNOLOGIST)
        self.client.force_login(technologist)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.create(5)
        self.assertFalse(Container.objects.exists())
