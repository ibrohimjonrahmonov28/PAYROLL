# Architecture

Statements marked **(unverified)** were inferred from names, docstrings or partial reads and were not traced end-to-end.

## Runtime topology (docker-compose.yml)
- `web`: gunicorn (2 workers × 4 threads, gthread). It runs `collectstatic` and `migrate --fake-initial` on start. App dirs are bind-mounted, so a restart picks up code changes.
- `scheduler`: `manage.py run_scheduler`, a polling loop. 18:00–18:15 → `daily_image_reports.send_daily_1800_reports`. 00:00–00:15 → `close_daily_payroll.perform_daily_closing(yesterday)` + `telegram_reports.send_daily_excel_report`.
- `passport_bot`: `manage.py run_passport_bot` (python-telegram-bot). There is also a webhook route at `/bot/passport-webhook/`.
- `db`: postgres:16. `nginx` serves static and media and proxies to web.

## Request pipeline
`SecurityMiddleware … AuthenticationMiddleware → accounts.middleware.MasterTerminalAccessRestrictionMiddleware → view`.
The middleware restricts these roles to allow-listed path prefixes: MASTER, ADMIN, CONTROL, SCREEN, BRANCH_ADMIN, PLAN and CUTTER. Blocked requests get a 403 JSON response if they are AJAX/JSON, otherwise a redirect to the role's home. A `'/api/'` substring test also applies for some roles. Other roles (MANAGER, METO, STICKER, TECHNOLOGIST, SUPER_ADMIN) are not path-restricted; views check the `User.is_*()` helpers instead. Superusers bypass everything.
URL roots are in `config/urls.py`. `production.urls` is mounted at `/` (namespace `production`), and the portals each have their own urlconf under `production/*_urls.py`. Full list: `api-map.json` (182 routes).

## Domain pipeline (data flow)
```
Customer → Order → OrderItem(Article) → OrderItemSize
                         │
Cutting  (/cutting/)  CuttingBatch "pastal" (code P{MM}-{N}) → CuttingBatchItem ──auto──► Box(es)
Meto     (/meto/, /meto-hub/)  numbering ranges on boxes; Waybill groups batches for transfer (DRAFT→IN_TRANSIT→RECEIVED/REJECTED)
Stickers (/stickers/)  print Ticket stickers (100x60 PDF, sticker_generator.py)
Master tablet (/master-tablet/)  patok accepts/rejects pastals (CuttingBatch.dispatch_status, Box.is_received_by_patok)  (unverified: exact state machine)
Terminal (/terminal/)  master scans worker QR, then ticket QRs → session list → finalize: atomic + select_for_update → Ticket SCANNED(worker, scanned_at, screen_number)
OTK      (/control/)  close boxes as controlled: 1st grade / repair / defect (services.close_boxes_as_controlled, BoxQualityInspectionLog, DefectReason, ControlSetting)
Stats    (/sewing-statistics/, /statistics/, /screens/)  read-side aggregation
```
- **Ticket = unit of pay.** `services.generate_box_tickets` creates one ticket per active `ArticleOperation` × split. `allocate_ticket_quantities` handles the split. Regeneration keeps existing `ticket_code`/`stiker_code` and marks obsolete tickets CANCELLED; it never deletes them.
- **Payroll aggregates** = sums of `Ticket.total_amount` where status is SCANNED, by worker and date. `DailyWorkerClosing` seals each day at 00:00. `WorkerPayout` records payments.
- **Freeze:** `MonthlyClosing` OPEN → PENDING_FREEZE (7-day timer, `process_freeze_timers`) → FROZEN. `Ticket.is_frozen` locks past-month amounts against price recalculation (`superadmin_recalculate_unfrozen_tickets`). `freeze_services.auto_freeze_past_months` freezes every past month that an admin did not reopen. The migration `accounts/0018_auto_freeze_past_months` also does this.
- **Norma/progress:** `DailyModelProgress` (per ProductModel, `norma_services`) and `DailyPatokProgress` (per patok) hold daily norm vs. done, with carry-over percentages (business rules in `NEED.TXT`).

## Patoks / screens
`screen_number` 1–40 is the canonical line id. Display codes: 1–13 → K1..K13 (HQ branch), 14+ → U1.. (Uychi). Logins: `patokk{n}`/`patoku{n}` (CONTROL), `ekrank{n}`/`ekranu{n}` (SCREEN). Always convert via `production.services.get_patok_code / get_patok_name / parse_patok_number`. Screens: `screens/views.get_screen_data` builds the payload, and `templates/screens/monitor.html` polls `/screens/api/<n|code>/` with `setInterval`.

## Coupling and circular-import notes
- `accounts.models` ↔ `production.models/services` reference each other. The cycle is broken with **lazy in-function imports** (see `lazy` in `dependencies.json`). Keep new cross-app imports lazy, otherwise you get import cycles at startup.
- `production.reports` and `production.sewing_statistics` import each other and `screens` (shared aggregation helpers).
- `production/apps.py` monkey-patches Django's `Template.__init__` to collapse whitespace in `{{ }}` / `{% %}`. Templates rely on this for multi-line tags.

## External integrations
- Telegram: the report bot (`TELEGRAM_BOT_TOKEN`, chat ids `TELEGRAM_*_CHAT_ID` normalized to the `-100…` form in settings) and the passport bot (`TELEGRAM_PASSPORT_BOT_TOKEN`). Used via `httpx`/`python-telegram-bot`.
- OpenCV + numpy: `passport_bot_service` decodes QR codes from sticker photos (`cv2.QRCodeDetector`).
- Pillow: report images (`daily_image_reports`, fonts in `production/fonts/`). openpyxl: Excel. qrcode: QR generation (user/worker/ticket).
- Android `android_control` kiosk APK is served from `/apk/` and `/control/download-apk`. It has no code link to Django (unverified: it likely just wraps the `/control/` web UI).
