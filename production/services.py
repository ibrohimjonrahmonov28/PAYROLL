"""
Production business logic and SRS Ticket Allocation Algorithm.
"""
from decimal import Decimal
from django.db import transaction
from django.db.models import Max
from .models import Box, Order, Ticket, ArticleOperation


def allocate_ticket_quantities(box_quantity: int, split_count: int) -> list[int]:
    """
    SRS 3.2 bo'yicha butun sonli bilet taqsimoti:
    Ticket Allocation_i = floor(Q / N) + (1 if i < (Q mod N) else 0)
    Hech qanday qoldiq yoki kasrli kiyim hosil bo'lmaydi.
    Yig'indisi aniq box_quantity ga teng bo'ladi.
    """
    if split_count <= 0:
        split_count = 1
    if box_quantity <= 0:
        return []

    q = box_quantity
    n = split_count

    base = q // n
    remainder = q % n

    allocations = []
    for i in range(n):
        alloc = base + (1 if i < remainder else 0)
        if alloc > 0:
            allocations.append(alloc)
            
    return allocations


@transaction.atomic
def generate_box_tickets(box: Box, operation_splits: dict[int, int] = None) -> list[Ticket]:
    """
    Quti uchun barcha operatsiyalarni ko'rsatilgan splitlar bo'yicha biletlarga (stikerlarga) ajratish.
    operation_splits: {article_operation_id: split_count}
    Agar split ko'rsatilmagan bo'lsa, mavjud biletlardagi splitlar saqlanadi yoki default = 1 ta bilet.
    """
    if box.status == Box.Status.CANCELLED:
        return []

    article = box.target_article
    if not article:
        return []

    # Har safar stiker yaratilganda / yangilanganda Narxlar matritsasidagi eng so'nggi ma'lumotlar bilan sinxronlash
    if article.operation_group:
        article.sync_operations_from_group(sync_unscanned_boxes=False)

    # Agar operation_splits berilmagan bo'lsa, qutida avval bo'lgan split konfiguratsiyasini saqlab qolish
    if operation_splits is None:
        operation_splits = {}
        for t in box.tickets.all():
            if t.article_operation_id and t.total_splits > 1:
                operation_splits[t.article_operation_id] = max(
                    operation_splits.get(t.article_operation_id, 1),
                    t.total_splits
                )

    # Mavjud biletlarni tozalash:
    # Agar bironta ham bilet skanerlanmagan bo'lsa, barchasini tozalab yangidan yaratish
    has_scanned = box.tickets.filter(status=Ticket.Status.SCANNED).exists()
    if not has_scanned:
        box.tickets.exclude(status=Ticket.Status.CANCELLED).delete()
    else:
        # Agar qisman skanerlangan bo'lsa, faqat kutilayotgan (PENDING) biletlarni tozalaymiz
        box.tickets.filter(status=Ticket.Status.PENDING).delete()

    article_ops = article.article_operations.filter(is_active=True).order_by('sequence', 'id')
    created_tickets = []

    for art_op in article_ops:
        # Agar bu operatsiya allaqachon skanerlangan bo'lsa, uni qayta yaratmaymiz
        if has_scanned and box.tickets.filter(article_operation=art_op, status=Ticket.Status.SCANNED).exists():
            continue

        split_count = int(operation_splits.get(art_op.id, 1))
        if split_count < 1:
            split_count = 1

        allocations = allocate_ticket_quantities(box.quantity, split_count)
        total_splits = len(allocations)

        for index, qty in enumerate(allocations, start=1):
            ticket = Ticket(
                box=box,
                article_operation=art_op,
                quantity=qty,
                split_index=index,
                total_splits=total_splits,
                price_per_unit=art_op.price_per_unit,
                status=Ticket.Status.PENDING
            )
            ticket.save()
            created_tickets.append(ticket)

    return created_tickets


def ensure_box_tickets_fresh(box: Box) -> None:
    """
    Qutidagi stikerlar eng so'nggi narxlar matritsasiga to'liq mosligini tekshiradi va
    agar narxlar yoki operatsiyalar o'zgargan bo'lsa, avtomatik yangilaydi (agar hali skanerlanmagan bo'lsa).
    """
    if box.status == Box.Status.CANCELLED:
        return
    art = box.target_article
    if not art:
        return
    if box.tickets.filter(status=Ticket.Status.SCANNED).exists():
        return

    if art.operation_group:
        art.sync_operations_from_group(sync_unscanned_boxes=False)

    existing_tickets = list(box.tickets.exclude(status=Ticket.Status.CANCELLED))
    art_ops = list(art.article_operations.filter(is_active=True).order_by('sequence', 'id'))

    if len(existing_tickets) != len(art_ops):
        generate_box_tickets(box)
        return

    existing_op_map = {t.article_operation_id: t.price_per_unit for t in existing_tickets}
    needs_refresh = False
    for ao in art_ops:
        if ao.id not in existing_op_map or existing_op_map[ao.id] != ao.price_per_unit:
            needs_refresh = True
            break

    if needs_refresh:
        generate_box_tickets(box)


@transaction.atomic
def create_box_with_tickets(order: Order, article, quantity: int, count: int = 1, razmer: str = None, pastal_number: str = '') -> list[Box]:
    """
    Admin uchun quti(lar) yaratish va har bir quti uchun darhol QR biletlarni avtomatik generatsiya qilish.
    Admin umumiy zakaz hajmiga qaramasdan, kerakli model va dona soni (masalan 100 talik) bo'yicha qutilarni chiqaradi.
    """
    if count < 1:
        count = 1
    if quantity < 1:
        quantity = 1

    if not article:
        article = order.article or (order.items.first().article if order.items.exists() else None)

    if article and article.operation_group:
        article.sync_operations_from_group(sync_unscanned_boxes=False)

    max_box = order.boxes.aggregate(max_num=Max('box_number'))['max_num'] or 0
    next_number = max_box + 1
    created_boxes = []

    for _ in range(count):
        while order.boxes.filter(box_number=next_number).exists():
            next_number += 1
        box = Box.objects.create(
            order=order,
            article=article,
            box_number=next_number,
            quantity=quantity,
            razmer=razmer,
            pastal_number=pastal_number,
            status=Box.Status.CREATED
        )
        # Har bir quti uchun barcha operatsiyalar bo'yicha darhol QR biletlar tayyor bo'ladi!
        generate_box_tickets(box)
        created_boxes.append(box)
        next_number += 1

    return created_boxes


@transaction.atomic
def create_boxes_for_order(
    order: Order, 
    box_sizes: list[int], 
    article=None, 
    razmer: str = None,
    cutting_batch_item=None,
    meto_range: str = '',
    pastal_number: str = ''
) -> list[Box]:
    """
    Buyurtmani qutilarga (boxes/bundles) ajratish va biletlarni generatsiya qilish.
    box_sizes: har bir qutining miqdori, masalan [100, 100, 100, ...]
    """
    if not article:
        article = order.article or (order.items.first().article if order.items.exists() else None)

    if article and article.operation_group:
        article.sync_operations_from_group(sync_unscanned_boxes=False)

    max_box = order.boxes.aggregate(max_num=Max('box_number'))['max_num'] or 0
    next_number = max_box + 1
    created_boxes = []

    for qty in box_sizes:
        while order.boxes.filter(box_number=next_number).exists():
            next_number += 1
        box = Box.objects.create(
            order=order,
            article=article,
            cutting_batch_item=cutting_batch_item,
            box_number=next_number,
            quantity=qty,
            razmer=razmer,
            pastal_number=pastal_number,
            meto_range=meto_range,
            status=Box.Status.CREATED
        )
        generate_box_tickets(box)
        created_boxes.append(box)
        next_number += 1

    return created_boxes


@transaction.atomic
def auto_generate_boxes_for_batch_item(
    batch_item,
    split_count: int = 1,
    box_capacity: int = None,
    pastal_number: str = None,
    box_count: int = None
) -> list[Box]:
    """
    Meto ishni tugatishi bilanoq avtomatik stikerlar va qutilarni generatsiya qilish:
    - Foydalanuvchi talabi:
      "U TUGATGANDA AVTOMATIK STIKERLAR GENERATISYA BOLADI VA STIKER CHIQARADIGAN ODAM OZI CHIQARADI VA TIKUVGA BERADI"
    """
    if box_count is not None:
        split_count = box_count
    order = batch_item.batch.order_item.order
    article = batch_item.batch.order_item.article
    size_name = batch_item.order_item_size.size_name
    available_qty = batch_item.remaining_to_box
    if available_qty <= 0:
        return []

    if box_capacity and box_capacity > 0:
        full_boxes = available_qty // box_capacity
        rem = available_qty % box_capacity
        box_sizes = [box_capacity] * full_boxes
        if rem > 0:
            box_sizes.append(rem)
    elif split_count and split_count > 1:
        box_sizes = allocate_ticket_quantities(available_qty, split_count)
    else:
        box_sizes = [available_qty]

    meto_range = ""
    if batch_item.meto_number_start or batch_item.meto_number_end:
        meto_range = f"#{batch_item.meto_number_start or '1'}-#{batch_item.meto_number_end or batch_item.effective_quantity}"

    if pastal_number is None and batch_item.batch:
        pastal_number = batch_item.batch.pastal_code or str(batch_item.batch.batch_number)

    boxes = create_boxes_for_order(
        order=order,
        box_sizes=box_sizes,
        article=article,
        razmer=size_name,
        cutting_batch_item=batch_item,
        meto_range=meto_range,
        pastal_number=pastal_number or ''
    )
    batch_item.boxes_created_qty = sum(b.quantity for b in batch_item.boxes.all())
    batch_item.status = batch_item.Status.METO_CONFIRMED
    batch_item.save(update_fields=['boxes_created_qty', 'status'])
    return boxes


# Alias for backwards compatibility with tests
auto_split_boxes_for_batch_item = auto_generate_boxes_for_batch_item


# ==============================================================================
# EKRANLAR VA PATOKLARNI QAYTA NOMLASH FUNKSIYALARI
# Qoida:
# 1 dan 13 gacha (13 ham) -> K1, K2, ..., K13
# 14 va undan keyingilari -> U1, U2, U3, ... (U1 dan boshlab ketadi)
# ==============================================================================

def get_patok_code(screen_number: int | None) -> str:
    """
    Ekran / Patok raqamini yangi nomlash qoidasiga ko'ra kodga o'girish:
    1 dan 13 gacha (13 ham) -> K1, K2, ..., K13
    14 va undan keyingilari -> U1, U2, U3, ... (U1 dan boshlab ketadi)
    """
    if not screen_number:
        return ""
    try:
        sn = int(screen_number)
    except (ValueError, TypeError):
        return ""
    if sn < 1:
        return ""
    if sn <= 13:
        return f"K{sn}"
    else:
        return f"U{sn - 13}"


def get_patok_name(screen_number: int | None) -> str:
    """
    Ekran / Patok to'liq nomi: masalan, 'K1-Patok', 'K13-Patok', 'U1-Patok', ...
    """
    code = get_patok_code(screen_number)
    return f"{code}-Patok" if code else ""


def get_patok_login(screen_number: int | None) -> str:
    """
    Patok Sifat Nazorati (CONTROL) foydalanuvchisi logini:
    1 dan 13 gacha -> patokk1, patokk2, ..., patokk13
    14 va undan keyingilari -> patoku1, patoku2, ..., patoku27
    """
    if not screen_number:
        return ""
    try:
        sn = int(screen_number)
    except (ValueError, TypeError):
        return ""
    if sn < 1:
        return ""
    if sn <= 13:
        return f"patokk{sn}"
    else:
        return f"patoku{sn - 13}"


def get_screen_login(screen_number: int | None) -> str:
    """
    Sex Ekran / Monitor (SCREEN) foydalanuvchisi logini:
    1 dan 13 gacha -> ekrank1, ekrank2, ..., ekrank13
    14 va undan keyingilari -> ekranu1, ekranu2, ..., ekranu27
    """
    if not screen_number:
        return ""
    try:
        sn = int(screen_number)
    except (ValueError, TypeError):
        return ""
    if sn < 1:
        return ""
    if sn <= 13:
        return f"ekrank{sn}"
    else:
        return f"ekranu{sn - 13}"


def parse_patok_number(val) -> int | None:
    """
    K1..K13, U1..U27, patokk1..patokk13, patoku1..patoku27 yoki 1..40 ni screen_number (1..40) int ga aylantirish.
    """
    if val is None:
        return None
    val_str = str(val).strip().upper()
    if not val_str:
        return None

    # Check PATOKK1..PATOKK13 (masalan: patokk3 -> 3)
    if val_str.startswith('PATOKK'):
        num_str = val_str[6:]
        if num_str.isdigit():
            num = int(num_str)
            if 1 <= num <= 13:
                return num

    # Check PATOKU1..PATOKU27 (masalan: patoku1 -> 14)
    if val_str.startswith('PATOKU'):
        num_str = val_str[6:]
        if num_str.isdigit():
            num = int(num_str)
            if num >= 1:
                return 13 + num

    # Check EKRANK1..EKRANK13
    if val_str.startswith('EKRANK'):
        num_str = val_str[6:]
        if num_str.isdigit():
            num = int(num_str)
            if 1 <= num <= 13:
                return num

    # Check EKRANU1..EKRANU27
    if val_str.startswith('EKRANU'):
        num_str = val_str[6:]
        if num_str.isdigit():
            num = int(num_str)
            if num >= 1:
                return 13 + num

    # Check EKRAN1..EKRAN40
    if val_str.startswith('EKRAN'):
        num_str = val_str[5:]
        if num_str.isdigit():
            num = int(num_str)
            if 1 <= num <= 40:
                return num

    # Check PATOK1..PATOK40 (eski patok1..patok40)
    if val_str.startswith('PATOK'):
        num_str = val_str[5:]
        if num_str.isdigit():
            num = int(num_str)
            if 1 <= num <= 40:
                return num

    # Check K1..K13
    if val_str.startswith('K'):
        num_str = val_str[1:]
        if num_str.isdigit():
            num = int(num_str)
            if 1 <= num <= 13:
                return num

    # Check U1..U27
    if val_str.startswith('U'):
        num_str = val_str[1:]
        if num_str.isdigit():
            num = int(num_str)
            if num >= 1:
                return 13 + num

    # Check plain number (1..40)
    if val_str.isdigit():
        num = int(val_str)
        if 1 <= num <= 40:
            return num

    return None



