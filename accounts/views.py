from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import Worker, User


def worker_list_view(request):
    if request.method == 'POST':
        worker_id = request.POST.get('worker_id', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()

        if not worker_id or not first_name or not last_name:
            messages.error(request, "Iltimos, xodim ID, ism va familiyasini to'ldiring.")
        elif Worker.objects.filter(worker_id=worker_id).exists():
            messages.error(request, f"{worker_id} raqamli xodim allaqachon mavjud!")
        else:
            worker = Worker.objects.create(
                worker_id=worker_id,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone_number,
                is_active=True
            )
            messages.success(request, f"Tikuvchi {worker.full_name} ({worker.worker_id}) muvaffaqiyatli qo'shildi va QR kodi yaratildi!")
            return redirect('accounts:worker_list')

    workers = Worker.objects.all().order_by('worker_id')
    return render(request, 'accounts/worker_list.html', {'workers': workers})


def worker_badges_print_view(request):
    workers = Worker.objects.filter(is_active=True).order_by('worker_id')
    return render(request, 'accounts/worker_badges_print.html', {'workers': workers})
