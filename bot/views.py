from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from accounts.models import Worker
from production.models import Ticket
from .services import (
    identify_worker,
    scan_ticket_item,
    finalize_and_route,
    clear_session,
    get_or_create_session
)


def bot_simulator_view(request):
    workers = Worker.objects.filter(is_active=True).order_by('worker_id')
    pending_tickets = Ticket.objects.filter(status=Ticket.Status.PENDING).select_related(
        'box__order', 'article_operation__operation'
    ).order_by('box__box_number', 'split_index')[:30]

    return render(request, 'bot/simulator.html', {
        'workers': workers,
        'pending_tickets': pending_tickets
    })


@csrf_exempt
def simulator_action_api(request):
    import json
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST allowed'}, status=405)

    data = json.loads(request.body.decode('utf-8'))
    action = data.get('action')
    session_key = data.get('session_key', 'web_simulator_master')

    if action == 'identify_worker':
        worker_input = data.get('worker_input', '')
        success, msg, worker = identify_worker(session_key, worker_input)
        return JsonResponse({
            'success': success,
            'message': msg,
            'worker': {
                'id': worker.id,
                'worker_id': worker.worker_id,
                'name': worker.full_name
            } if worker else None
        })

    elif action == 'scan_ticket':
        ticket_input = data.get('ticket_input', '')
        success, msg, ticket_data = scan_ticket_item(session_key, ticket_input)
        return JsonResponse({
            'success': success,
            'message': msg,
            'ticket_data': ticket_data
        })

    elif action == 'finalize':
        screen_number = int(data.get('screen_number', 1))
        master_user = request.user if request.user.is_authenticated else None
        success, msg, summary = finalize_and_route(session_key, screen_number, master_user)
        return JsonResponse({
            'success': success,
            'message': msg,
            'summary': summary
        })

    elif action == 'cancel':
        clear_session(session_key)
        return JsonResponse({'success': True, 'message': 'Sessiya bekor qilindi.'})

    return JsonResponse({'error': 'Invalid action'}, status=400)
