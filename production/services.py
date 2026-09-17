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
    Agar split ko'rsatilmagan bo'lsa, default = 1 ta bilet (qutining to'liq miqdori).
    """
    if operation_splits is None:
        operation_splits = {}

    # Mavjud generatsiya qilinmagan eski biletlarni tozalash (agar pending bo'lsa)
    box.tickets.filter(status=Ticket.Status.PENDING).delete()

    article = box.target_article
    if not article:
        return []

    article_ops = article.article_operations.all().order_by('sequence', 'id')
    created_tickets = []

    for art_op in article_ops:
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


@transaction.atomic
def create_box_with_tickets(order: Order, article, quantity: int, count: int = 1) -> list[Box]:
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
            status=Box.Status.CREATED
        )
        # Har bir quti uchun barcha operatsiyalar bo'yicha darhol QR biletlar tayyor bo'ladi!
        generate_box_tickets(box)
        created_boxes.append(box)
        next_number += 1

    return created_boxes


@transaction.atomic
def create_boxes_for_order(order: Order, box_sizes: list[int], article=None) -> list[Box]:
    """
    Buyurtmani qutilarga (boxes/bundles) ajratish va biletlarni generatsiya qilish.
    box_sizes: har bir qutining miqdori, masalan [100, 100, 100, ...]
    """
    if not article:
        article = order.article or (order.items.first().article if order.items.exists() else None)

    max_box = order.boxes.aggregate(max_num=Max('box_number'))['max_num'] or 0
    next_number = max_box + 1
    created_boxes = []

    for qty in box_sizes:
        while order.boxes.filter(box_number=next_number).exists():
            next_number += 1
        box = Box.objects.create(
            order=order,
            article=article,
            box_number=next_number,
            quantity=qty,
            status=Box.Status.CREATED
        )
        generate_box_tickets(box)
        created_boxes.append(box)
        next_number += 1

    return created_boxes

