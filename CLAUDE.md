# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working workflow (project knowledge graph)

A precomputed map of this repo lives in `.claude/project-knowledge/`. Use it instead of scanning the codebase:

1. Read `.claude/project-knowledge/overview.md` first, then only the graph entries relevant to the task (`modules.json`, `api-map.json`, `database-map.json`, `dependencies.json`, `file-index.json`, `architecture.md`).
2. Locate the affected module(s), routes and models from the graph **before** opening source files.
3. Read only the files (and line ranges — use `symbols` in `file-index.json`) that the change needs, plus their direct dependencies.
4. Make minimal changes; do not refactor unrelated code.
5. Run focused tests only (single module/class, `USE_SQLITE=1`, see Commands).
6. After changing URLs, models, imports, or adding/removing files, regenerate the graph: `USE_SQLITE=1 .venv/bin/python .claude/project-knowledge/build_graph.py`; update `architecture.md`/`overview.md` by hand if a described flow changed.
7. Never perform another full-repository scan unless the user explicitly asks for one.

## Project

**Business spec:** `SYSTEM_ARCHITECTURE_GUIDE.md` (Uzbek) is the owner-written master description of the business process (9 production stages) and the **10 golden rules** (price auto-sync for unfrozen tickets, frozen-period immutability, no scanner transliteration, patok attribution via `assigned_screen`, only final ironing counted, template sums must match exactly, Patok column + AutoFilter in monthly tabel, DB-level aggregation for OOM safety, CANCELLED boxes never pay, nightly 00:00 report + backup). Read its relevant section before changing business logic; those rules plus the sticker-immutability rule below are non-negotiable. Note: its role/URL names are descriptive (e.g. `QUALITY_CONTROL`, `/master/`, `/screen/`) — the code uses `CONTROL`, `/master-tablet/`, `/screens/`.

TERRY JAR — piece-rate payroll and production tracking for a garment (sewing) factory. Django 5.1 / Python 3.12 / PostgreSQL 16, server-rendered templates (Bootstrap 5 + vanilla JS). The UI text, comments, docstrings and commit-body descriptions are in **Uzbek (Latin)**; keep new user-facing strings in Uzbek to match. Timezone is `Asia/Tashkent` — use `timezone.localtime()` for any date/day-boundary logic.

## Commands

```bash
# Local dev (uses .venv; DB chosen by env — see "Database selection" below)
.venv/bin/python manage.py runserver
.venv/bin/python manage.py migrate

# Tests — Django's test runner (no pytest). USE_SQLITE=1 forces an in-memory SQLite DB,
# so tests don't need Postgres running.
USE_SQLITE=1 .venv/bin/python manage.py test                           # all
USE_SQLITE=1 .venv/bin/python manage.py test production                # one app
USE_SQLITE=1 .venv/bin/python manage.py test production.test_terminal  # one module
USE_SQLITE=1 .venv/bin/python manage.py test production.tests.AllocationAlgorithmTest.test_srs_single_split

# Production (Docker): services web, db, passport_bot, scheduler, nginx
docker compose up -d --build
docker compose logs -f web
docker compose restart web
```

Tests live in `accounts/tests.py`, `screens/tests.py`, `production/tests.py` and `production/test_*.py`. There is no linter/formatter configured.

`docker-compose.yml` bind-mounts `config/`, `accounts/`, `production/`, `screens/`, `templates/` into the containers, so code changes on the server only need `docker compose restart web` (and `scheduler`/`passport_bot` if they touch those). The `web` container runs `collectstatic` and `migrate --fake-initial` on startup.

### Database selection (`config/settings.py`)
1. `USE_SQLITE=1` → in-memory SQLite (tests).
2. `DB_ENGINE=django.db.backends.postgresql` **or** `DB_NAME` set → PostgreSQL.
3. Otherwise → `db.sqlite3` file.

The local `.env` sets `DB_NAME`, so plain `runserver`/`test` will try Postgres unless `USE_SQLITE=1` is given.

### Management commands worth knowing
- `run_scheduler` — long-running daemon (the `scheduler` container). At 18:00 sends branch daily image reports (`production/daily_image_reports.py`); at 00:00 closes the previous day's payroll (`close_daily_payroll`) and sends the Excel report to Telegram (`production/telegram_reports.py`). `--now` / `--date YYYY-MM-DD` run immediately.
- `run_passport_bot` — Telegram bot (the `passport_bot` container) for pastal/sticker verification (`production/passport_bot_service.py`) and dispatch to patoks (kg → branch → patok → confirm → `DISPATCHED`). Until the master accepts, a DISPATCHED pastal may be re-sent to another patok (decision of `14cf5ee`); the confirm button carries the pastal's state (`batch_dispatch_state`, e.g. `D3`) and `dispatch_batch_async` refuses it if the state changed, so stale buttons never re-route silently. While the bot asks for kg, any number is a weight (`parse_weight_text`); a typed code that matches several pastals (e.g. pyjama top and shorts cut under one code) gets a choice list (`find_pastal_candidates`) instead of a silent pick.
- `close_daily_payroll`, `process_freeze_timers`, `send_daily_report`, `send_daily_patok_report`, `send_db_backup`, `sync_article_operations`, `close_unprinted_boxes`, `create_patok_users`, `create_ekran_users`, `setup_branch_admin`, `seed_demo_data`.

## Architecture

Three Django apps: `accounts` (users, workers, payroll closing, superadmin panel), `production` (everything about orders → boxes → tickets, plus all role-specific portals), `screens` (live shop-floor TV monitors). `production` is very large and split by portal into `*_views.py` + `*_urls.py` pairs mounted in `config/urls.py` (`/managers/`, `/managers-new/`, `/cutting/`, `/meto/`, `/meto-hub/`, `/stickers/`, `/norma/`, `/control/`, `/gifts/`, `/superadmin/` → `accounts/superadmin_*`). Business logic is in `production/services.py`, `production/norma_services.py`, `accounts/freeze_services.py`; reports in `excel_reports.py`, `telegram_reports.py`, `daily_image_reports.py`.

### Domain flow
`Customer` → `Order` → `OrderItem` (an `Article`) → `OrderItemSize`. Cutting creates a `CuttingBatch` (a "pastal", code `P{MM}-{N}` from `generate_next_pastal_code`) with `CuttingBatchItem`s, which auto-generate `Box`es. `ProductModel` groups articles that share the same sewing operations; `Operation` is the global catalog, `ArticleOperation` is the per-article operation with `price_per_unit`, `sequence`, `is_active`.

> **OLTIN QOIDA (golden rule — the system's core pillar):** once created/printed, a sticker's `ticket_code` / `stiker_code` / QR and its box's `box_code` are **never regenerated, re-coded or deleted** — not when prices change, not when operations change, not in migrations, management commands or bug fixes. The QR only identifies the ticket; the scanner reads the latest price/operation data from the DB at scan time, so a price change updates DB fields only. A past bug that regenerated all old stickers caused major real-world losses. If a task seems to require touching existing sticker/box identity, stop and ask first.
>
> **Stable zone:** sticker generation/printing, terminal scanning and price templates (`ArticleOperation` pricing) work well and are considered finished — only small, minimal tweaks there. New development happens elsewhere; don't refactor these areas or change them as a side effect of other work.

Each `Box` gets one or more `Ticket`s (stickers, 8-char `stiker_code`) per active `ArticleOperation` via `services.generate_box_tickets`, which splits the box quantity with `allocate_ticket_quantities` (remainder goes to the first splits; never fractional). Regeneration **preserves existing ticket/sticker codes** and marks obsolete tickets `CANCELLED` rather than deleting them — keep that invariant, printed stickers must stay valid.

**Payroll is ticket-driven:** a master scans a worker's QR then ticket QRs at `/terminal/` (`terminal_views.py`); the ticket becomes `SCANNED` with `worker`, `scanned_at`, `screen_number`, and its `total_amount` is the worker's earnings. Daily/monthly pay are aggregates over scanned tickets. `MonthlyClosing` (OPEN → PENDING_FREEZE with a 7-day timer → FROZEN) and `Ticket.is_frozen` lock past months so price recalculations don't change already-paid salaries; `accounts/freeze_services.auto_freeze_past_months` freezes every past month not deliberately reopened by an admin. Respect `is_frozen` when changing any price/recalc logic.

Never mass-update tickets in one statement inside a web request. On 2026-11-11 the payroll page's "Muzlatish" button froze October's ~208k tickets with a single `UPDATE`. `production_ticket` has 21 indexes and the server has 4 GB, so the request outlived nginx and every gunicorn thread got stuck, putting the whole site, terminal included, on 504.

Freezing therefore now works like this:
- `freeze_services.update_in_batches` updates 5000 rows per short statement, and month filters use `month_bounds` date ranges (the `scanned_at` index) instead of `__year`/`__month`.
- The month freeze/unfreeze button sets the `MonthlyClosing` status at once and runs the ticket update via `run_in_background` (a thread). It runs synchronously when `settings.FREEZE_IN_BACKGROUND` is False, i.e. under `USE_SQLITE=1` tests.
- `superadmin_payroll` no longer calls `auto_freeze_past_months` / `check_and_apply_freeze_timers` on page load. `run_scheduler` runs them every hour (`run_freeze_check`), which also finishes any freeze an interrupted thread left half-done.

The payroll page (`/superadmin/payroll/`) used to render all ~1000 workers in 7.4 MB of HTML. Now:
- it pages 100 rows at a time (`PAYROLL_PAGE_SIZE`, `?page=`);
- by default it hides workers with no work, payout or balance this month (`?all=1` shows them);
- search is server-side over all pages: every word of `?q=` must match the ID, first/last name, UID or phone;
- the summary cards still total all filtered workers.

`production/test_payroll_page.py` covers this. **Intended business rule:** a price change (`ArticleOperation.save()` → `sync_price_to_tickets`) reprices *all* unfrozen tickets, including ones already scanned earlier in the month — e.g. work done at month start is paid at the new price if the price rises at month end. Do not restrict this to PENDING tickets; freezing is the only lock.

After sewing, OTK quality control (`control_views.py`, `services.close_boxes_as_controlled`, `BoxQualityInspectionLog`, `DefectReason`) closes boxes as 1st grade / repair / defect. OTK's initial inspection overwrites `Box.quantity` with the counted total while the box's tickets keep their original split, so the sum of box quantities (the model page's "Qutilarda") is the real order count and can exceed the cutter's `CuttingBatchItem.quantity` — never regenerate tickets to reconcile this. The "Pastal bo'yicha qidirish" block on `/statistics/` (`?pastal_info=<code | PASTAL:id | box code>`) shows a read-only full pastal card built by `production/pastal_info.py` (`find_pastal_batches`, `build_pastal_info`): cut/meto/boxes, bot dispatch and tablet acceptance, waybill, sewing, final DAZMOL, OTK and a timeline; a code shared by two batches shows both. `sewing_statistics_views.py` builds the pipeline statistics (in sewing → ironed ("dazmol") → waiting for OTK → OTK-closed) per patok/pastal/size. Pipeline identity (owner's requirement): **Dazmol = OTK kutmoqda + Ta'mir + 1-sort + 2-sort** on the model, order and factory pages — a box counts as ironed if its DAZMOL ticket is scanned, or all its active tickets are scanned (DAZMOL added after printing), or OTK has inspected it (`box_dazmol_time`, `evaluate_box_progress`; such boxes are flagged "dazmol stikeri urilmagan/yo'q"), and the already-sorted part of a box in repair counts in 1/2-sort (`controlled_at` set, not only `is_controlled`). The order and model pages compute it from prefetched ticket objects; the factory total on `/sewing-statistics/` ("Fabrika bo'yicha jami") uses `production/sewing_pipeline_summary.py` instead (per-box SQL aggregation, ~2 s instead of ~15 s on real data) and must equal the sum of the order page's `order_totals` — `production/test_pipeline_summary.py` enforces this, so change both together.

**Final ironing = only the catalog operation named exactly "DAZMOL"** (owner's rule 2026-10-30; it is the model's last operation). PLANKA / TASMA / CHO'NTAK and any other "… DAZMOL" are intermediate, not finished work. Use `daily_dazmol.is_main_dazmol_operation` / `main_dazmol_operation_ids()` everywhere (daily page, 18:00 report, order/model statistics, TV screens, master tablet, superadmin) — never re-implement name matching.

**Daily dazmol has one source: `production/daily_dazmol.py::compute_daily_dazmol`.** *Asosiy dazmol* = final dazmol of boxes really accepted by a patok, credited to that patok wherever it was ironed. Main path (both branches): the Telegram passport bot (`run_passport_bot`) dispatches a pastal to a patok (`DISPATCHED`, K1–K13 / U1–U27), it appears on that patok's master tablet, and acceptance sets `Box.is_received_by_patok=True`. Extra Uychi path (owner's decision 2026-10-29): the Uychi warehouse in `meto_hub_views` (waybill receipt → batch `ACCEPTED`, `assigned_branch='UYCHI'`) hands the pastal to a U-patok (`Box.assigned_screen` ≥ 14) with no tablet step — that hand-out counts as acceptance. The batch must not be WAITING/DISPATCHED/REJECTED (`get_box_owner_screen` / `owned_box_q`); *patok dazmoli* = dazmol scanned at that patok's terminal; *egasiz* = dazmol of pastals nobody accepted (credited to no one, not in the 18:00 report). `/sewing-statistics/daily/` (asosiy per patok) and `/sewing-statistics/daily-report/` + the 18:00 image (`daily_image_reports.get_branch_daily_report_data`, per price template) must both read it so a patok's asosiy dazmol always equals the sum of its template lines — `production/test_daily_dazmol.py` enforces this. Both pages write `DailyPatokProgress.dazmol_qty` (monthly Excel) with the same asosiy value. The 18:00 report (page + image) has 7 columns — Паток, Иш номи, Норма, Иш сони, Давомат, Операцион, Фоиз: one line per price template × article norm (`DailyDazmol.template_lines`, norm = `get_article_norm`: article → model → 1200); **Фоиз = Σ иш сони / mean(norms of the lines)** (owner's formula, e.g. (100 + 900) / ((800 + 1000) / 2) = 111%, `daily_image_reports.patok_foiz`); branch % = mean of active patoks, factory % = mean of the two branch means.

**Managerlar NEW (`/managers-new/`, owner's request 2026-11-11, in testing).** This is the new order entry; once fully tested it replaces the old `/managers/`, which is then hidden. Code: `production/manager_new_views.py`, `manager_new_excel.py`, `templates/managers_new/`. Planned next stages: fabric warehouse (TMO) → cutting ("finish pastal" → meto); they are not built yet.
- **Access:** a MANAGER sees and edits only orders where `Order.manager` is them. The role middleware limits MANAGER users to `/managers-new/`, and login redirects them there. The superadmin sees everything, reassigns orders and archives them.
- **Hidden from old pages:** new orders have `Order.is_new_flow=True` and are filtered out (`is_new_flow=False`) of the old lists while testing. The filtered lists are `/orders/`, the dashboard, the old `/managers/`, norma, the sewing-statistics orders, and the superadmin order list, balance and counts.
- **Articles:** every article is created new. `Article.code` is globally unique, so it is stored as `"<manager's code> · <order number>"`, and `OrderItem.article_code` keeps the typed code.
- **Sizes are per article** (owner's change 2026-11-11): one order can mix adult and children articles. The order form asks no sizes; each article has its own list in `OrderItem.size_labels` (comma-separated, ordered; presets adults 46–56 and children 110–146, or typed by hand). `Order.size_labels` is a legacy field that is no longer written; migration 0038 copied it into the articles.
- **Item and fabric data:** `OrderItem` also stores `prosjyot` (Просчет: order plus extra pieces, e.g. samples) and `print_image`. Articles have no shipment date (owner's decision 2026-11-11): `OrderItem.ship_date` is a legacy field that is no longer asked or shown, and the Excel files have no per-article date. The manager pages are laid out for full desktop width; the "Yangi zakaz" button is only on the order list. `OrderItemFabric` holds one row per fabric part with its own colour. Finished kg = kg per piece × Просчет; raw kg = finished + raw % (default 10).
- **Statuses:** DRAFT → CONFIRMED → IN_CUTTING → IN_SEWING → COMPLETED, or CANCELLED / ARCHIVED (new `Order.Status` choices). From IN_CUTTING on only the superadmin may edit. `OrderChangeLog` records every change.
- **Deleting:** never delete an article or order that has boxes or cutting batches. `Article` deletion cascades to its boxes and stickers; `_has_production` guards against it.
- **Excel export:** "Zakaz list" and "Mato zakazi", with the Russian headers of the owner's sample files. No packaging or barcodes for now.

### Patoks (production lines) and screens
Lines are identified by an integer `screen_number` 1–40, but displayed with codes: 1–13 → `K1..K13` (HQ), 14+ → `U1..` (Uychi branch). Patok pickers (Telegram bot, master tablet, Uychi warehouse hand-out, superadmin user form) offer all of U1–U27 (screens 14–40); the 18:00 report and monthly Excel always list U1–U21 and add higher patoks when they have data. Always convert via `get_patok_code` / `get_patok_name` / `parse_patok_number` in `production/services.py`; login names follow `patokk{n}`/`patoku{n}` (CONTROL users) and `ekrank{n}`/`ekranu{n}` (SCREEN users). `User.assigned_screen_number` resolves a user's patok.

**Patok awards (owner's request 2026-11-09):** `PatokAward` stores the patok (`screen_number`), the month (`month` = the 1st of the month) and the name. It optionally links a `worker`, and also holds the nomination, the reason and worker/gift photos; uploads are re-saved as JPEG of at most 1600 px. nginx caches `/media/` for 7 days. So every upload gets a new unique file name (`photo_<uuid>.jpg`), and templates show images through `photo_src` / `gift_photo_src` (URL + `?v=<updated_at>`). Never reuse a name: on 2026-11-11 a deleted test photo's freed `photo.jpg` was given to another worker, and TVs kept showing the old picture. Only the superadmin enters awards, at `/gifts/boshqaruv/` (sidebar "Taqdirlash"). The name is typed or picked from the workers who scanned at that patok that month, sorted by average daily norm % (`gift_views.worker_candidates`, same norm rule as the TV monitor). `/gifts/<patok>/` (e.g. `/gifts/k1/`) is a public kiosk page like `/screens/`:
- it opens the latest month that has awards; ◀ ▶ step through months with awards;
- several awards rotate every 5 s (`gift_views.SLIDE_SECONDS`, owner's change 2026-11-11, was 15 s);
- it reloads itself when `/gifts/api/<patok>/version/` changes.

A SCREEN user may open only its own patok's pages (exact paths in the role middleware); BRANCH_ADMIN has the `/gifts/` prefix. Tests: `production/test_gifts.py`.

### Roles and access control
`accounts.User` (custom `AUTH_USER_MODEL`) has a `role` (SUPER_ADMIN, ADMIN, BRANCH_ADMIN, MANAGER, TECHNOLOGIST, CUTTER, METO, STICKER, MASTER, CONTROL, SCREEN, PLAN, USER) and a `branch` (HQ / UYCHI). Users and `Worker`s auto-generate a 6-digit `uid` and QR code on save. Path-level restriction for MASTER, ADMIN, CONTROL, SCREEN, CUTTER etc. is enforced centrally by `accounts/middleware.py::MasterTerminalAccessRestrictionMiddleware` via allow-listed URL prefixes (403 JSON for AJAX, redirect otherwise). **When adding a new URL a restricted role must reach, add its prefix to that middleware's allow-list**; views additionally use the `User.is_*()` helpers.

### Gotchas
- **Kun/Tun (day/night) theme:** pages are written dark-first with the stock Tailwind palette (`bg-slate-900`, `text-emerald-400`, …). `templates/includes/theme_head.html` (included in every app page's `<head>`, after the Tailwind CDN) routes Tailwind colour utilities through CSS variables whose fallbacks are the original colours (dark mode unchanged), and `html.theme-light` remaps them to readable light-mode values (≥4.5:1). So new markup gets light mode for free if it uses palette classes; hard-coded hex colours (`bg-[#...]`, inline `style`, page `<style>`) need an entry in that file's `ARBITRARY_BG` list or a `html.theme-light` rule. Toggle: `includes/theme_toggle.html` (`compact=True`, `label_class=...`); choice is stored in `localStorage.payroll_theme`, default dark. Print pages (stickers, badges, waybill, pastal passport) intentionally don't include the theme.
- **Frozen pages (owner's strict requirement):** `/control/` (`production/control_home.html`), `/screens/` (`screens/overview.html`) and all 40 TV monitors `/screens/<n>/` (`screens/monitor.html`) must stay exactly as they were. They include `includes/theme_head_legacy.html` (verbatim pre-redesign copy) instead of `theme_head.html`. Do not edit that file or apply design/theme changes to these pages unless the owner explicitly asks. Owner-requested tweaks on `/control/` (2026-10-30): the +1/qty/−1 stack sits left (clear of the accept button on landscape tablets), and ta'mir is yellow (amber) while 2-sort is red (rose) everywhere on the page — keep that scheme. Owner-requested change on the 40 TV monitors (2026-11-04): instead of PATOK QUVVATI, the header shows PATOK NORMASI, ASOSIY DAZMOL and PATOK FOIZI (`screens.views.get_screen_patok_metrics`, the whole day computed once and cached 30 s). These must equal the 18:00 report for every patok (owner's rule), so the screen reuses the report's own functions, `daily_image_reports.patok_report_lines` and `patok_foiz`. PATOK NORMASI is the mean norm of the report lines: the asosiy dazmol's template × norm lines, or, on a day without dazmol, the model of the last pastal the patok accepted. ASOSIY DAZMOL is the 18:00 asosiy dazmol, and PATOK FOIZI equals the report's Фоиз. `screens/tests.py` checks screen = report for K1–K13. The monitor's JS polling (`fetchScreenData`) must update the same ids. The `/screens/` overview still shows quvvat. Owner-requested addition on the 40 monitors (2026-11-09): a gift button `#btn-gifts` in the header links to the patok's `/gifts/<patok>/` page. Its badge is the number of awards in the patok's latest awarded month (`screens.views.get_screen_gift_badge`). The stack is `relative z-10` (so −1 stays tappable) but `pointer-events-none` with `pointer-events-auto` on its three controls (2026-11-02): in repair-return mode the banner and taller qty block push the stack over the accept button, and without this its empty area swallowed every tap on low-height tablets (each tablet has its own zoom in `localStorage.control_zoom`).
- **Brand / favicon:** the Terry Jar Textile logo is vectorized in `production/static/brand/` (`terryjar-logo.svg` full logo with text, `terryjar-icon.svg` tab icon on a white tile, `favicon.ico`, `apple-touch-icon.png`). `includes/theme_head.html` links them, and `/favicon.ico` redirects there (passed through in the role middleware), so pages without `theme_head` — the frozen `/control/` and `/screens/` pages and print pages — get the icon without their templates being touched.
- `production/apps.py` monkey-patches `django.template.base.Template.__init__` to normalize whitespace inside `{{ }}` and `{% %}` (so multi-line tags and `|default: x` work). Templates may rely on this.
- Large monthly Excel exports (`excel_reports.py`) have previously OOM-crashed the 2-worker gunicorn container — stream/iterate querysets rather than materializing everything.
- Barcode scanner input: do not add Cyrillic→Latin keyboard transliteration (it was intentionally removed).
- `CHANGELOG.md` is a hand-maintained Uzbek log of significant commits (ID, files changed, features); it is updated alongside notable feature work.
- The repo root contains large DB/media backups (`*.sql.gz`, `*.dump`, `data_backup.json`); `restore_data.sh` loads them into the Docker stack. Don't read or modify them unless asked.
