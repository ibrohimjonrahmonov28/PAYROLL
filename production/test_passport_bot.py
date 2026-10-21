"""
Unit tests for Passport Verification Service, Telegram Bot Handlers, and Guards
"""

import io
from decimal import Decimal
import qrcode
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from accounts.models import Worker
from production.models import (
    Customer,
    Article,
    Operation,
    ArticleOperation,
    Order,
    OrderItem,
    OrderItemSize,
    CuttingBatch,
    CuttingBatchItem,
    Box,
    Ticket,
    CancelledBatchLog,
    _generate_qr_data_uri,
)
from production.services import auto_generate_boxes_for_batch_item
from production.passport_bot_service import (
    resolve_code_to_entity,
    verify_pastal_for_sewing,
    decode_qr_from_image_bytes,
)

User = get_user_model()


class PassportVerificationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='test_user', password='password123')
        self.customer = Customer.objects.create(name="Test Mijoz", code="TM")
        self.article = Article.objects.create(code="ART-101", name="Futbolka")
        
        # Operatsiyalar
        self.op1 = Operation.objects.create(name="Bichuv", code="OP-01")
        self.op2 = Operation.objects.create(name="Tikuv", code="OP-02")
        
        self.art_op1 = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op1,
            price_per_unit=Decimal('500.00'),
            sequence=1
        )
        self.art_op2 = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op2,
            price_per_unit=Decimal('700.00'),
            sequence=2
        )

        self.order = Order.objects.create(
            order_number="ZAK-001",
            customer=self.customer,
            article=self.article,
            total_quantity=200
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            article=self.article,
            quantity=200
        )
        self.size_m = OrderItemSize.objects.create(
            order_item=self.order_item,
            size_name="M",
            planned_quantity=100
        )
        self.size_l = OrderItemSize.objects.create(
            order_item=self.order_item,
            size_name="L",
            planned_quantity=100
        )

        # Kesim partiyasi
        self.batch = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=1,
            name="Kesim 1",
            pastal_code="P-01",
            cutter_name="Ali Bichuvchi"
        )
        self.batch_item_m = CuttingBatchItem.objects.create(
            batch=self.batch,
            order_item_size=self.size_m,
            quantity=100,
            real_quantity=100,
            status=CuttingBatchItem.Status.METO_CONFIRMED
        )
        self.batch_item_l = CuttingBatchItem.objects.create(
            batch=self.batch,
            order_item_size=self.size_l,
            quantity=100,
            real_quantity=100,
            status=CuttingBatchItem.Status.METO_CONFIRMED
        )

        # Qutilar va stikerlarni generatsiya qilish
        self.boxes_m = auto_generate_boxes_for_batch_item(self.batch_item_m, split_count=2)
        self.boxes_l = auto_generate_boxes_for_batch_item(self.batch_item_l, split_count=2)

    def test_passport_qr_data_uri_generation(self):
        """Pasport QR kod data URI xatosiz hosil bo'lishini tekshirish"""
        uri = _generate_qr_data_uri(f"PASTAL:{self.batch.id}", box_size=6, border=1)
        self.assertTrue(uri.startswith("data:image/png;base64,"))

    def test_decode_qr_from_image_bytes(self):
        """OpenCV QRCodeDetector orqali rasmdan QR kodni to'g'ri o'qish"""
        qr = qrcode.QRCode()
        qr.add_data(f"PASTAL:{self.batch.id}")
        qr.make()
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        
        decoded_list = decode_qr_from_image_bytes(buf.getvalue())
        self.assertTrue(len(decoded_list) > 0)
        self.assertEqual(decoded_list[0], f"PASTAL:{self.batch.id}")

    def test_verify_approved_pastal(self):
        """Barcha qutilar va stikerlar to'liq va narxlangan bo'lsa: PATOKKA BERISH MUMKIN"""
        res = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertTrue(res['success'])
        self.assertTrue(res['can_release'])
        self.assertEqual(res['status_code'], 'APPROVED')
        self.assertIn("PATOKKA BERISH MUMKIN", res['message'])
        self.assertEqual(res['details']['total_boxes'], 4)
        self.assertEqual(res['details']['total_units'], 200)

    def test_verify_by_any_box_code(self):
        """Quti kodi orqali ham butun pastal to'liq tekshirilishi kerak"""
        first_box = self.boxes_m[0]
        res = verify_pastal_for_sewing(f"CONTROL:{first_box.box_code}")
        self.assertTrue(res['success'])
        self.assertTrue(res['can_release'])
        self.assertEqual(res['status_code'], 'APPROVED')

    def test_verify_by_any_ticket_code(self):
        """Pastal ichidagi istalgan stiker kodi orqali ham to'liq tekshirilishi kerak"""
        ticket = self.boxes_m[0].tickets.first()
        res = verify_pastal_for_sewing(f"TICKET:{ticket.ticket_code}")
        self.assertTrue(res['success'])
        self.assertTrue(res['can_release'])
        self.assertEqual(res['status_code'], 'APPROVED')

    def test_verify_by_stiker_code(self):
        """8 xonali stiker kodi orqali tekshirish"""
        ticket = self.boxes_m[0].tickets.first()
        res = verify_pastal_for_sewing(f"#{ticket.stiker_code}")
        self.assertTrue(res['success'])
        self.assertTrue(res['can_release'])
        self.assertEqual(res['status_code'], 'APPROVED')

    def test_verify_cancelled_pastal_rejected(self):
        """Agar qutilar atmen (CANCELLED) bo'lsa: PATOKKA BERIB BO'LMAYDI"""
        for b in self.boxes_m + self.boxes_l:
            b.status = Box.Status.CANCELLED
            b.save(update_fields=['status'])
            b.tickets.update(status=Ticket.Status.CANCELLED)

        res = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertFalse(res['can_release'])
        self.assertEqual(res['status_code'], 'CANCELLED')
        self.assertIn("PATOKKA BERIB BO'LMAYDI", res['message'])
        self.assertIn("ATMEN", res['message'])

    def test_verify_missing_price_rejected(self):
        """Agar stikerlarda narx/summa 0 bo'lsa: PATOKKA BERIB BO'LMAYDI (SUMMA KELMAGAN)"""
        first_ticket = self.boxes_m[0].tickets.first()
        Ticket.objects.filter(id=first_ticket.id).update(
            price_per_unit=Decimal('0.00'),
            total_amount=Decimal('0.00')
        )

        res = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertFalse(res['can_release'])
        self.assertEqual(res['status_code'], 'MISSING_PRICE')
        self.assertIn("PATOKKA BERIB BO'LMAYDI", res['message'])
        self.assertIn("OPERATSIYA NARXLARI", res['message'])

    def test_verify_cancelled_log_rejected(self):
        """O'chirilgan partiya CancelledBatchLog da bo'lsa to'xtatish"""
        CancelledBatchLog.objects.create(
            batch_id=999,
            pastal_code="PC-DELETED",
            order_number="ZAK-DEL",
            article_code="ART-DEL",
            reason="Kesimda bekor qilindi"
        )
        res = verify_pastal_for_sewing("PASTAL:999")
        self.assertFalse(res['can_release'])
        self.assertEqual(res['status_code'], 'CANCELLED')
        self.assertIn("BEKOR QILINIB", res['message'])

    def test_cutting_cancel_guard_when_scanned(self):
        """Agar stiker skanerlangan bo'lsa, kesimda o'chirish taqiqlanadi"""
        worker = Worker.objects.create(first_name="Hasan", last_name="Umarov")
        ticket = self.boxes_m[0].tickets.first()
        ticket.status = Ticket.Status.SCANNED
        ticket.worker = worker
        ticket.scanned_at = timezone.now()
        ticket.save()

        from django.test import RequestFactory
        from production.cutting_views import cutting_delete_batch
        from accounts.models import User

        factory = RequestFactory()
        req = factory.post(f"/cutting/orders/{self.order.id}/batches/{self.batch.id}/delete/")
        superuser = User.objects.create_superuser('admin_user', 'admin@example.com', 'adminpass')
        req.user = superuser
        
        # Django messages middleware simulation
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(req, 'session', {})
        setattr(req, '_messages', FallbackStorage(req))

        cutting_delete_batch(req, self.order.id, self.batch.id)

        # Batch o'chmasligi kerak!
        self.assertTrue(CuttingBatch.objects.filter(id=self.batch.id).exists())

    def test_meto_reset_guard_when_scanned(self):
        """Agar stiker skanerlangan bo'lsa, Metoda qutilarni qayta taqsimlash taqiqlanadi"""
        worker = Worker.objects.create(first_name="Zuhra", last_name="Alimova")
        ticket = self.boxes_m[0].tickets.first()
        ticket.status = Ticket.Status.SCANNED
        ticket.worker = worker
        ticket.scanned_at = timezone.now()
        ticket.save()

        from django.test import RequestFactory
        from production.meto_views import meto_reset_item
        from accounts.models import User

        factory = RequestFactory()
        req = factory.post(f"/meto/items/{self.batch_item_m.id}/reset/")
        user = User.objects.create_superuser('meto_admin', 'meto@example.com', 'pass')
        req.user = user

        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(req, 'session', {})
        setattr(req, '_messages', FallbackStorage(req))

        meto_reset_item(req, self.batch_item_m.id)

        # Qutilar o'zgarmasligi va bekor bo'lmasligi kerak
        active_boxes = self.batch_item_m.boxes.exclude(status=Box.Status.CANCELLED)
        self.assertTrue(active_boxes.exists())

    def test_verify_order_cancelled_rejected(self):
        """Zakaz holati CANCELLED (Bekor qilingan) bo'lsa: PATOKKA BERIB BO'LMAYDI"""
        self.order.status = Order.Status.CANCELLED
        self.order.save(update_fields=['status'])

        # Pasport kodi orqali
        res = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertFalse(res['can_release'])
        self.assertEqual(res['status_code'], 'ORDER_CANCELLED')
        self.assertIn("BEKOR QILINGAN", res['message'])

        # Quti stikeri orqali
        first_box = self.boxes_m[0]
        res_box = verify_pastal_for_sewing(f"CONTROL:{first_box.box_code}")
        self.assertFalse(res_box['can_release'])
        self.assertEqual(res_box['status_code'], 'ORDER_CANCELLED')

        # Ticket orqali
        tkt = first_box.tickets.first()
        res_tkt = verify_pastal_for_sewing(f"TICKET:{tkt.ticket_code}")
        self.assertFalse(res_tkt['can_release'])
        self.assertEqual(res_tkt['status_code'], 'ORDER_CANCELLED')

    def test_verify_order_archived_or_completed_rejected(self):
        """Zakaz ARXIVLANGAN yoki TUGATILGAN bo'lsa tikuvga berish taqiqlanadi"""
        self.order.status = Order.Status.ARCHIVED
        self.order.save(update_fields=['status'])
        res = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertFalse(res['can_release'])
        self.assertEqual(res['status_code'], 'ORDER_ARCHIVED')

        self.order.status = Order.Status.COMPLETED
        self.order.save(update_fields=['status'])
        res2 = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertFalse(res2['can_release'])
        self.assertEqual(res2['status_code'], 'ORDER_COMPLETED')

    def test_verify_partially_cancelled_pastal_rejected(self):
        """Pastalda 1 ta quti bekor qilingan (re-split yoki qisman atmen) bo'lsa: BLOKLANADI"""
        cancelled_box = self.boxes_m[0]
        cancelled_box.status = Box.Status.CANCELLED
        cancelled_box.save(update_fields=['status'])
        cancelled_box.tickets.update(status=Ticket.Status.CANCELLED)

        # Pasport orqali tekshirish
        res = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertFalse(res['can_release'])
        self.assertIn("QAYTA TAQSIMLANGAN", res['message'])
        self.assertIn("ATMEN", res['message'])

    def test_verify_frozen_ticket_rejected(self):
        """Muzlatilgan (is_frozen=True) stiker bo'lsa: PATOKKA BERIB BO'LMAYDI"""
        tkt = self.boxes_m[0].tickets.first()
        tkt.is_frozen = True
        tkt.save(update_fields=['is_frozen'])

        res = verify_pastal_for_sewing(f"TICKET:{tkt.ticket_code}")
        self.assertFalse(res['can_release'])
        self.assertEqual(res['status_code'], 'FROZEN_TICKET')
        self.assertIn("MUZLATILGAN", res['message'])

    def test_manager_order_cancel_cascades_to_boxes_and_tickets(self):
        """Menejer zakazni CANCELLED qilganda barcha quti va biletlar CANCELLED bo'lishi kerak"""
        from django.test import RequestFactory
        from production.manager_views import manager_order_update_status
        from accounts.models import User

        factory = RequestFactory()
        req = factory.post(f"/manager/orders/{self.order.id}/status/", {'status': Order.Status.CANCELLED})
        mgr = User.objects.create_superuser('mgr_user', 'mgr@example.com', 'mgrpass')
        req.user = mgr

        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(req, 'session', {})
        setattr(req, '_messages', FallbackStorage(req))

        manager_order_update_status(req, self.order.id)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        # Barcha qutilar va biletlar CANCELLED bo'lishi kerak
        self.assertEqual(self.order.boxes.exclude(status=Box.Status.CANCELLED).count(), 0)
        self.assertEqual(Ticket.objects.filter(box__order=self.order, status=Ticket.Status.PENDING).count(), 0)

    def test_verify_incomplete_box_tickets_rejected(self):
        """Qutida operatsiyalar biletlari chala bo'lsa: PATOKKA BERIB BO'LMAYDI"""
        # op2 bileti o'chirilgan holat
        tkt = self.boxes_m[0].tickets.filter(article_operation=self.art_op2).first()
        tkt.delete()

        res = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertFalse(res['can_release'])
        self.assertEqual(res['status_code'], 'INCOMPLETE_BOX_TICKETS')
        self.assertIn("BILETLAR TO'LIQ EMAS", res['message'])

    def test_verify_empty_box_tickets_rejected_for_single_box(self):
        """Biletlari umuman yo'q quti tekshirilganda: NO_TICKETS"""
        empty_box = Box.objects.create(
            order=self.order,
            article=self.article,
            cutting_batch_item=self.batch_item_m,
            box_number=99,
            box_code="E99-999",
            quantity=50
        )
        res = verify_pastal_for_sewing(f"BOX:{empty_box.box_code}")
        self.assertFalse(res['can_release'])
        self.assertEqual(res['status_code'], 'NO_TICKETS')
        self.assertIn("HALI BILETLAR GENERATSIYA QILINMAGAN", res['message'])

    def test_verify_unknown_sticker_candidate_diagnostic(self):
        """Noma'lum 8 xonali stiker kodi kiritilganda aniq ogohlantirish berish"""
        res = verify_pastal_for_sewing("#UNKNOWN8")
        self.assertFalse(res['can_release'])
        self.assertEqual(res['status_code'], 'NOT_FOUND')
        self.assertIn("USHBU STIKER TIZIMDA TOPILMADI", res['message'])
        self.assertIn("tikuv patogiga chiqarmang", res['message'])

    def test_build_patoks_keyboard_uychi_range(self):
        """Uychi klaviaturasi barcha 27 ta patok (14..40: U1..U27) bo'lishini tekshirish"""
        from production.management.commands.run_passport_bot import build_patoks_keyboard
        kb = build_patoks_keyboard(self.batch.id, 'UYCHI')
        # Row-lar ichidagi patok tugmalarini yig'ish
        patok_buttons = []
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith('dp_pat:'):
                    patok_buttons.append(btn)
        self.assertEqual(len(patok_buttons), 27)
        self.assertEqual(patok_buttons[0].text, "U1-Patok")
        self.assertEqual(patok_buttons[-1].text, "U27-Patok")

    async def test_dispatch_batch_async_guards(self):
        """Kutilayotgan (DISPATCHED) pastal qayta jo'natilishi mumkin, ammo qabul qilingan (ACCEPTED) bloklanadi"""
        from production.management.commands.run_passport_bot import dispatch_batch_async
        # 1. Muvaffaqiyatli jo'natish
        res1 = await dispatch_batch_async(self.batch.id, 'HQ', 1, 'Tester')
        self.assertFalse(res1['error'])
        self.assertEqual(res1['patok_code'], 'K1')

        # 2. Hali qabul qilinmagan (DISPATCHED) bo'lsa, patokni qayta tanlash mumkin
        res2 = await dispatch_batch_async(self.batch.id, 'HQ', 2, 'Tester')
        self.assertFalse(res2['error'])
        self.assertEqual(res2['patok_code'], 'K2')

        # 3. ACCEPTED holatida esa qat'iy bloklanishi kerak
        self.batch.dispatch_status = CuttingBatch.DispatchStatus.ACCEPTED
        await self.batch.asave(update_fields=['dispatch_status'])

        res3 = await dispatch_batch_async(self.batch.id, 'HQ', 3, 'Tester')
        self.assertTrue(res3['error'])
        self.assertIn("qabul qilingan", res3['message'])

    def test_rejected_pastal_preserves_weight_and_notes(self):
        """Master pastalni qaytarganda vazn o'chib ketmasligi va bot tekshiruvida qaytarilganligi ko'rinishi"""
        self.batch.actual_weight_kg = Decimal('48.50')
        self.batch.dispatch_status = CuttingBatch.DispatchStatus.WAITING
        self.batch.rejection_reason = "[Ali Master] (K1-Patok): Choklarda xatolik bor"
        self.batch.save()

        res = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertTrue(res['success'])
        self.assertEqual(res['details']['actual_weight_kg'], 48.5)
        self.assertEqual(res['details']['rejection_reason'], "[Ali Master] (K1-Patok): Choklarda xatolik bor")
        self.assertIn("Choklarda xatolik bor", res['message'])

    def test_dispatched_and_accepted_and_closed_pastal_verification_guards(self):
        """
        Pastal holatini tekshirish:
        1. Agar DISPATCHED (kutilmoqda) bo'lsa -> can_release=True (buttonlar chiqishi uchun), DISPATCHED_WAITING
        2. Agar ACCEPTED (qabul qilingan) bo'lsa -> can_release=False, ALREADY_ACCEPTED
        3. Agar barcha qutilar yopilgan bo'lsa -> can_release=False, PASTAL_CLOSED
        """
        # 1. DISPATCHED holati
        self.batch.dispatch_status = CuttingBatch.DispatchStatus.DISPATCHED
        self.batch.assigned_screen = 2
        self.batch.save()

        res_disp = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertTrue(res_disp['can_release'])
        self.assertEqual(res_disp['status_code'], 'DISPATCHED_WAITING')
        self.assertIn("KUTILMOQDA", res_disp['message'])
        self.assertIn("K2-Patok", res_disp['message'])

        # 2. ACCEPTED holati
        self.batch.dispatch_status = CuttingBatch.DispatchStatus.ACCEPTED
        self.batch.save()

        res_acc = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertFalse(res_acc['can_release'])
        self.assertEqual(res_acc['status_code'], 'ALREADY_ACCEPTED')
        self.assertIn("ALLAQACHON QABUL QILINGAN", res_acc['message'])
        self.assertIn("K2-Patok", res_acc['message'])

        # 3. YOPILGAN holati: Barcha qutilar nazoratdan o'tgan
        for item in self.batch.items.all():
            item.boxes.update(is_controlled=True, controlled_first_sort_qty=50, controlled_repair_qty=0)

        res_closed = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertFalse(res_closed['can_release'])
        self.assertEqual(res_closed['status_code'], 'PASTAL_CLOSED')
        self.assertIn("TO'LIQ YOPILGAN", res_closed['message'])

    def test_flexible_pastal_resolution_and_priority(self):
        """
        Foydalanuvchi turli formatlarda pastal raqamini yozganda to'g'ri topilishi:
        - "1" yoki "12"
        - "PASTAL 12", "KESIM 12", "P-12", "12-pastal"
        - Eski ACCEPTED va yangi WAITING batchlar bo'lsa, WAITING ustuvor bo'lishi
        """
        from production.passport_bot_service import resolve_code_to_entity

        # 1. Mavjud batch raqamini turli formatlarda tekshirish
        b_num = str(self.batch.batch_number)
        for cand in [b_num, f"PASTAL {b_num}", f"KESIM {b_num}", f"P-{b_num}", f"{b_num}-pastal", f"PC:{b_num}"]:
            b_found, _, _, res_type, _ = resolve_code_to_entity(cand)
            self.assertIsNotNone(b_found, f"Failed for candidate: {cand}")
            self.assertEqual(b_found.id, self.batch.id)

        # 2. Prioritet tekshiruvi:
        # Eski batch ACCEPTED, yangi batch WAITING bir xil batch_number (yoki pastal_code) bilan
        from production.models import CuttingBatchItem
        old_batch = self.batch
        old_batch.dispatch_status = CuttingBatch.DispatchStatus.ACCEPTED
        old_batch.pastal_code = "TEST-PC-99"
        old_batch.save()

        new_batch = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=old_batch.batch_number + 1,
            pastal_code="TEST-PC-99",
            dispatch_status=CuttingBatch.DispatchStatus.WAITING
        )

        # "TEST-PC-99" qidirilganda yangi WAITING batch topilishi shart
        b_prio, _, _, _, _ = resolve_code_to_entity("TEST-PC-99")
        self.assertIsNotNone(b_prio)
        self.assertEqual(b_prio.id, new_batch.id)
        self.assertEqual(b_prio.dispatch_status, CuttingBatch.DispatchStatus.WAITING)

    def test_pastal_card_and_action_keyboard(self):
        """
        Foydalanuvchi talabi bo'yicha:
        - Agar kg kiritilmagan bo'lsa: [⚖️ Kg kiritish] va [🚀 Jo'natish], [🔍 Tekshirish]
        - Agar kg kiritilgan bo'lsa: [⚖️ Kg kiritish] chiqmaydi, faqat [🚀 Jo'natish], [🔍 Tekshirish]
        """
        from production.management.commands.run_passport_bot import (
            build_pastal_card_text,
            build_pastal_action_keyboard
        )

        batch_info = {
            'batch_id': 10,
            'batch_number': 5,
            'pastal_code': 'P-05',
            'article_name': 'Test Model',
            'order_number': 'ORD-55',
            'customer_name': 'Client A',
            'units': 200,
            'weight': 0.0,
            'dispatch_status': 'WAITING',
            'rejection_reason': ''
        }

        # 1. Vazn kiritilmagan holat
        card_text_no_wt = build_pastal_card_text(batch_info)
        self.assertIn("*PASTAL PASPORTI:* #5", card_text_no_wt)
        self.assertIn("Kiritilmagan", card_text_no_wt)

        kb_no_wt = build_pastal_action_keyboard(10, has_weight=False)
        buttons_no_wt = [btn.text for row in kb_no_wt.inline_keyboard for btn in row]
        self.assertIn("⚖️ Kg kiritish", buttons_no_wt)
        self.assertIn("🚀 Jo'natish", buttons_no_wt)
        self.assertIn("🔍 Tekshirish", buttons_no_wt)

        # 2. Vazn kiritilgan holat: "bu yerda bitta kg kiritilgan bolsa bu button chiqmaydi"
        batch_info['weight'] = 45.5
        card_text_wt = build_pastal_card_text(batch_info)
        self.assertIn("45.5 kg", card_text_wt)

        kb_wt = build_pastal_action_keyboard(10, has_weight=True)
        buttons_wt = [btn.text for row in kb_wt.inline_keyboard for btn in row]
        self.assertNotIn("⚖️ Kg kiritish", buttons_wt)
        self.assertIn("🚀 Jo'natish", buttons_wt)
        self.assertIn("🔍 Tekshirish", buttons_wt)

    def test_verify_batch_with_clean_sizes_not_blocked_by_stray_cancelled_boxes(self):
        """
        10-276 holati bo'yicha:
        Pastaldagi barcha razmerlar to'g'ri va faol bo'lsa, bazada eski atmen qutilar bo'lsa ham
        soxta 'AYRIM RAZMERLAR QAYTA TAQSIMLANGAN' xatosi bilan to'xtab qolmasligi shart!
        """
        # 1. Stray (begona / eski) CANCELLED quti yaratish
        Box.objects.create(
            order=self.order,
            article=self.article,
            pastal_number="10-276",
            razmer="48",
            quantity=50,
            status=Box.Status.CANCELLED,
            box_number=9999
        )

        # Batchning pastal kodini "10-276" qilish
        self.batch.pastal_code = "10-276"
        self.batch.save(update_fields=['pastal_code'])

        # 2. "10-276" kodi orqali tekshirish
        res = verify_pastal_for_sewing("10-276")
        self.assertTrue(res['success'])
        self.assertTrue(res['can_release'])
        self.assertEqual(res['status_code'], 'APPROVED')
        self.assertIn("PATOKKA BERISH MUMKIN", res['message'])
        self.assertNotIn("AYRIM RAZMERLAR QAYTA TAQSIMLANGAN", res['message'])

        # 3. PASTAL:<batch_id> orqali tekshirish
        res_passport = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertTrue(res_passport['success'])
        self.assertTrue(res_passport['can_release'])
        self.assertEqual(res_passport['status_code'], 'APPROVED')

    def test_re_split_covered_batch_is_approved_and_can_release(self):
        """
        Metoda qutilar qayta bo'lingan (re-split), ammo yangi aktiv qutilar soni
        to'liq (100%) qoplangan bo'lsa, 'AYRIM RAZMERLAR QAYTA TAQSIMLANGAN' deb bloklamasdan,
        'PATOKKA BERISH MUMKIN' deb ruxsat berishi shart!
        """
        # Eski bekor qilingan quti qo'shamiz (Metoda bo'lingan deb)
        Box.objects.create(
            order=self.order,
            article=self.article,
            cutting_batch_item=self.batch_item_m,
            pastal_number=self.batch.pastal_code,
            razmer=self.batch_item_m.order_item_size.size_name,
            quantity=100,
            status=Box.Status.CANCELLED,
            box_number=8888
        )
        # self.boxes_m (2 ta 50 donalik aktiv qutilar) mavjud, jami: 100 dona == target_qty
        res = verify_pastal_for_sewing(f"PASTAL:{self.batch.id}")
        self.assertTrue(res['success'])
        self.assertTrue(res['can_release'])
        self.assertEqual(res['status_code'], 'APPROVED')
        self.assertIn("PATOKKA BERISH MUMKIN", res['message'])






