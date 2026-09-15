"""
Production business logic and SRS Ticket Allocation Algorithm.
"""
from decimal import Decimal
from django.db import transaction
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

    article_ops = box.order.article.article_operations.all().order_by('sequence', 'id')
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
def create_boxes_for_order(order: Order, box_sizes: list[int]) -> list[Box]:
    """
    Buyurtmani qutilarga (boxes/bundles) ajratish.
    box_sizes: har bir qutining miqdori, masalan [100, 100, 100, ...]
    """
    existing_max_number = order.boxes.count()
    created_boxes = []

    for idx, qty in enumerate(box_sizes, start=1):
        box_number = existing_max_number + idx
        box = Box.objects.create(
            order=order,
            box_number=box_number,
            quantity=qty,
            status=Box.Status.CREATED
        )
        created_boxes.append(box)

    return created_boxes
