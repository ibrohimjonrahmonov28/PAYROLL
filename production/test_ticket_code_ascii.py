"""
Yangi stikerlar QR'ida kirill bo'lmasin (egasi 2026-11-24): bilet kodidagi zakaz nomi lotinga o'giriladi
(0410(Шорты) -> 0410(SHORTY)). US klaviaturadagi skaner kirill harflarini yoza olmaydi (Alt+raqamlar — pauza, Android'da
harf tushib qoladi). Faqat YANGI biletlar — mavjud kodlar hech qachon o'zgarmaydi (oltin qoida).
"""
from decimal import Decimal

from django.test import TestCase

from production.models import (TICKET_CODE_MAX, Article, ArticleOperation, Box, Operation, Order, Ticket,
                               ticket_order_part)
from production.services import generate_box_tickets
from production.terminal_views import find_ticket_fast


class TicketCodeAsciiTest(TestCase):
    def setUp(self):
        self.article = Article.objects.create(code='ART-ASCII', name='Shorty')
        for seq, name in enumerate(('YON CHOK', 'DAZMOL'), start=1):
            operation = Operation.objects.create(name=name, code=f'OP-{seq}')
            ArticleOperation.objects.create(article=self.article, operation=operation,
                                            price_per_unit=Decimal('300'), sequence=seq)

    def tickets_for(self, order_number):
        order = Order.objects.create(order_number=order_number, article=self.article, total_quantity=50)
        box = Box.objects.create(order=order, article=self.article, box_number=1291, quantity=50, razmer='48')
        generate_box_tickets(box)
        return box, list(box.tickets.order_by('id'))

    def test_order_part_is_transliterated_only_when_not_ascii(self):
        cases = {
            '0410(Шорты)': '0410(SHORTY)', 'Х5-ДМ 4': 'X5-DM 4', 'ДМ-43': 'DM-43', 'Павлотти-4': 'PAVLOTTI-4',
            'Ўзбек Қўйлак Ғишт Ҳовли': 'OZBEK QOYLAK GISHT HOVLI', 'Щука №5 «Юла»': 'SHCHUKA No5 YULA',
            'ZAK-MF': 'ZAK-MF', '110/1': '110/1', 'Pijama 2': 'Pijama 2',   # lotincha — aynan o'zi
        }
        for raw, expected in cases.items():
            self.assertEqual(ticket_order_part(raw), expected, raw)
            self.assertTrue(ticket_order_part(raw).isascii(), raw)

    def test_new_tickets_of_cyrillic_order_are_ascii_and_found_exactly(self):
        box, tickets = self.tickets_for('0410(Шорты)')
        self.assertEqual(len(tickets), 2)
        for ticket in tickets:
            self.assertTrue(ticket.ticket_code.isascii(), ticket.ticket_code)
            self.assertTrue(ticket.ticket_code.startswith(f"TK-0410(SHORTY)-1291-{box.box_code}-"), ticket.ticket_code)
            self.assertEqual(find_ticket_fast(f"TICKET:{ticket.ticket_code}"), ticket.id)   # terminal — aniq moslik

    def test_ascii_order_codes_stay_exactly_as_before(self):
        box, tickets = self.tickets_for('ZAK-MF')
        for ticket in tickets:
            self.assertRegex(ticket.ticket_code, rf"^TK-ZAK-MF-1291-{box.box_code}-[0-9A-F]{{6}}$")

    def test_existing_cyrillic_code_never_changes(self):
        box, tickets = self.tickets_for('0410(Шорты)')
        ticket = tickets[0]
        legacy = ticket.ticket_code.replace('0410(SHORTY)', '0410(Шорты)')   # oldin chop etilgan stiker
        Ticket.objects.filter(id=ticket.id).update(ticket_code=legacy)
        ticket.refresh_from_db()
        ticket.quantity = 49
        ticket.save()
        generate_box_tickets(box)                                           # qayta generatsiya ham tegmaydi
        ticket.refresh_from_db()
        self.assertEqual(ticket.ticket_code, legacy)

    def test_long_cyrillic_name_fits_the_code_column(self):
        name = 'Щука Щетка Щавель Щепка Щедрость Щеголь Щелкунчик'
        box, tickets = self.tickets_for(name)
        for ticket in tickets:
            self.assertLessEqual(len(ticket.ticket_code), TICKET_CODE_MAX)
            self.assertTrue(ticket.ticket_code.isascii())
            self.assertRegex(ticket.ticket_code, rf"-1291-{box.box_code}-[0-9A-F]{{6}}$")   # oxiri butun
