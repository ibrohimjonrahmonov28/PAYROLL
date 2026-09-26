from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from .models import Worker, User


def login_view(request):
    """
    Foydalanuvchi tizimga kirish sahifasi.
    Master roli egalari kirgach avtomatik ravishda /terminal/ ga yo'naltiriladi.
    """
    if request.user.is_authenticated:
        if getattr(request.user, 'role', None) == User.Role.SCREEN:
            screen_num = request.user.assigned_screen_number or 1
            return redirect('screens:screen_view', screen_number=screen_num)
        if getattr(request.user, 'role', None) == User.Role.CONTROL:
            return redirect('production:control_home')
        if getattr(request.user, 'role', None) == User.Role.MASTER:
            return redirect('production:terminal_home')
        if getattr(request.user, 'role', None) == User.Role.MANAGER:
            return redirect('manager_dashboard')
        if getattr(request.user, 'role', None) == User.Role.BRANCH_ADMIN:
            return redirect('superadmin_payroll')
        if request.user.is_superadmin():
            return redirect('superadmin_dashboard')
        return redirect('production:order_list')

    next_url = request.GET.get('next', '')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        if not username or not password:
            messages.error(request, "Iltimos, login va parolni kiriting.")
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                # Agar username katta-kichik harf (masalan: Patok12), 6 xonali UID yoki telefon orqali kiritilgan bo'lsa
                from django.db.models import Q
                candidate = User.objects.filter(
                    Q(username__iexact=username) | Q(uid=username) | Q(phone_number=username)
                ).first()
                if candidate and candidate.check_password(password):
                    user = candidate
                    user.backend = 'django.contrib.auth.backends.ModelBackend'
                elif candidate and candidate.username:
                    user = authenticate(request, username=candidate.username, password=password)

            if user is not None:
                auth_login(request, user)
                
                # Ekran, Kontrolchi va Master rollari faqat o'z sahifasiga yo'naltiriladi
                if user.role == User.Role.SCREEN:
                    screen_num = user.assigned_screen_number or 1
                    return redirect('screens:screen_view', screen_number=screen_num)
                if user.role == User.Role.CONTROL:
                    return redirect('production:control_home')
                if user.role == User.Role.MASTER:
                    return redirect('production:terminal_home')
                if user.role == User.Role.BRANCH_ADMIN:
                    return redirect('superadmin_payroll')
                
                if next_url and next_url.startswith('/'):
                    return redirect(next_url)
                
                if user.role == User.Role.MANAGER:
                    return redirect('manager_dashboard')
                if user.role == User.Role.CUTTER:
                    return redirect('cutting_dashboard')
                if user.role == User.Role.METO:
                    return redirect('meto_dashboard')
                if user.role == User.Role.STICKER:
                    return redirect('sticker_dashboard')
                if user.is_superadmin():
                    return redirect('superadmin_dashboard')
                return redirect('production:order_list')
            else:
                messages.error(request, "Login yoki parol noto'g'ri!")

    return render(request, 'accounts/login.html', {'next_url': next_url})


def logout_view(request):
    """
    Tizimdan chiqish.
    """
    auth_logout(request)
    messages.info(request, "Tizimdan muvaffaqiyatli chiqdingiz.")
    return redirect('accounts:login')


def worker_list_view(request):
    if request.method == 'POST':
        worker_id = request.POST.get('worker_id', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()

        branch = request.POST.get('branch', User.Branch.HQ)
        if branch not in User.Branch.values:
            branch = User.Branch.HQ

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
                branch=branch,
                is_active=True
            )
            messages.success(request, f"Tikuvchi {worker.full_name} ({worker.worker_id}, {worker.get_branch_display()}) muvaffaqiyatli qo'shildi va QR kodi yaratildi!")
            return redirect('accounts:worker_list')

    workers = Worker.objects.all().order_by('worker_id')
    return render(request, 'accounts/worker_list.html', {'workers': workers})


def worker_badges_print_view(request):
    workers = Worker.objects.filter(is_active=True).order_by('worker_id')
    return render(request, 'accounts/worker_badges_print.html', {'workers': workers})
