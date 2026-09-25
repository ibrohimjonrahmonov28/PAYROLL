from django.shortcuts import redirect
from django.http import JsonResponse
from accounts.models import User


class MasterTerminalAccessRestrictionMiddleware:
    """
    Master roli (role == 'MASTER') egalarini faqat /terminal/ sahifasi
    va uning API'lariga cheklovchi xavfsizlik middleware'i.
    
    Boshqa barcha sahifalar (/orders/, /boxes/, /superadmin/, /admin/ va h.k.)
    ga kirish qat'iy cheklanadi va avtomatik ravishda /terminal/ ga yo'naltiriladi.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.is_superuser:
            if getattr(request.user, 'role', None) == User.Role.MASTER:
                path = request.path_info
                
                # Master uchun ruxsat etilgan yo'llar
                allowed_prefixes = (
                    '/terminal/',
                    '/terminal',
                    '/login/',
                    '/logout/',
                    '/accounts/login/',
                    '/accounts/logout/',
                    '/admin/logout/',
                    '/static/',
                    '/media/',
                )
                
                is_allowed = any(path.startswith(prefix) for prefix in allowed_prefixes)
                if not is_allowed:
                    # Agar API / AJAX yoki JSON so'rov bo'lsa, 403 Forbidden qaytarish
                    is_ajax = (
                        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
                        'application/json' in request.headers.get('accept', '') or
                        request.content_type == 'application/json'
                    )
                    if is_ajax:
                        return JsonResponse({
                            'status': 'FORBIDDEN',
                            'message': "Ruxsat etilmagan! Master hisobi faqat Skanerlash Terminalidan foydalana oladi."
                        }, status=403)
                    
                    # Brauzer orqali ochilganda to'g'ridan-to'g'ri terminal sahifasiga qaytarish
                    return redirect('production:terminal_home')

            # ADMIN (Buyurtmalar admini) uchun faqat /orders/ va /boxes/ ga ruxsat
            elif getattr(request.user, 'role', None) == User.Role.ADMIN:
                path = request.path_info
                allowed_prefixes = (
                    '/orders/',
                    '/orders',
                    '/boxes/',
                    '/login/',
                    '/logout/',
                    '/accounts/login/',
                    '/accounts/logout/',
                    '/admin/logout/',
                    '/static/',
                    '/media/',
                )
                is_allowed = any(path.startswith(prefix) for prefix in allowed_prefixes)
                if not is_allowed:
                    is_ajax = (
                        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
                        'application/json' in request.headers.get('accept', '') or
                        request.content_type == 'application/json'
                    )
                    if is_ajax:
                        return JsonResponse({
                            'status': 'FORBIDDEN',
                            'message': "Ruxsat etilmagan! Ushbu admin faqat buyurtmalar (orders) bo'limidan foydalana oladi."
                        }, status=403)
                    
                    return redirect('production:order_list')

            elif getattr(request.user, 'role', None) == User.Role.CONTROL:
                path = request.path_info
                allowed_prefixes = (
                    '/control/',
                    '/control',
                    '/login/',
                    '/logout/',
                    '/accounts/login/',
                    '/accounts/logout/',
                    '/admin/logout/',
                    '/static/',
                    '/media/',
                )
                is_allowed = any(path.startswith(prefix) for prefix in allowed_prefixes)
                if not is_allowed:
                    is_ajax = (
                        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
                        'application/json' in request.headers.get('accept', '') or
                        request.content_type == 'application/json'
                    )
                    if is_ajax:
                        return JsonResponse({
                            'status': 'FORBIDDEN',
                            'message': "Ruxsat etilmagan! Kontrolchi hisobi faqat Sifat Nazorati (Control) sahifasidan foydalana oladi."
                        }, status=403)
                    
                    return redirect('production:control_home')

            elif getattr(request.user, 'role', None) == User.Role.SCREEN:
                path = request.path_info
                screen_num = request.user.assigned_screen_number or 1

                exact_allowed_paths = (
                    f'/screens/{screen_num}/',
                    f'/screens/{screen_num}',
                    f'/screens/api/{screen_num}/',
                    f'/screens/api/{screen_num}',
                )
                allowed_prefixes = (
                    '/login/',
                    '/logout/',
                    '/accounts/login/',
                    '/accounts/logout/',
                    '/admin/logout/',
                    '/static/',
                    '/media/',
                )

                is_allowed = (path in exact_allowed_paths) or any(path.startswith(prefix) for prefix in allowed_prefixes)
                if not is_allowed:
                    is_ajax = (
                        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
                        'application/json' in request.headers.get('accept', '') or
                        request.content_type == 'application/json' or
                        '/api/' in path
                    )
                    if is_ajax:
                        return JsonResponse({
                            'status': 'FORBIDDEN',
                            'message': f"Ruxsat etilmagan! Ushbu hisob faqat {screen_num}-ekrandan foydalana oladi."
                        }, status=403)

                    return redirect('screens:screen_view', screen_number=screen_num)

            elif getattr(request.user, 'role', None) == User.Role.BRANCH_ADMIN:
                path = request.path_info
                allowed_prefixes = (
                    '/superadmin/payroll/',
                    '/superadmin/payroll',
                    '/superadmin/workers/',
                    '/statistics/',
                    '/statistics',
                    '/terminal/',
                    '/terminal',
                    '/login/',
                    '/logout/',
                    '/accounts/login/',
                    '/accounts/logout/',
                    '/admin/logout/',
                    '/static/',
                    '/media/',
                )
                is_allowed = any(path.startswith(prefix) for prefix in allowed_prefixes)
                if not is_allowed:
                    is_ajax = (
                        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
                        'application/json' in request.headers.get('accept', '') or
                        request.content_type == 'application/json' or
                        '/api/' in path
                    )
                    if is_ajax:
                        return JsonResponse({
                            'status': 'FORBIDDEN',
                            'message': "Ruxsat etilmagan! Filial admini faqat Oyliklar, Statistika va Skanerlash Terminalidan foydalana oladi."
                        }, status=403)

                    return redirect('superadmin_payroll')

        return self.get_response(request)

