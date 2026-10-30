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
- `run_passport_bot` — Telegram bot (the `passport_bot` container) for pastal/sticker verification (`production/passport_bot_service.py`).
- `close_daily_payroll`, `process_freeze_timers`, `send_daily_report`, `send_daily_patok_report`, `send_db_backup`, `sync_article_operations`, `close_unprinted_boxes`, `create_patok_users`, `create_ekran_users`, `setup_branch_admin`, `seed_demo_data`.

## Architecture

Three Django apps: `accounts` (users, workers, payroll closing, superadmin panel), `production` (everything about orders → boxes → tickets, plus all role-specific portals), `screens` (live shop-floor TV monitors). `production` is very large and split by portal into `*_views.py` + `*_urls.py` pairs mounted in `config/urls.py` (`/managers/`, `/cutting/`, `/meto/`, `/meto-hub/`, `/stickers/`, `/norma/`, `/control/`, `/superadmin/` → `accounts/superadmin_*`). Business logic is in `production/services.py`, `production/norma_services.py`, `accounts/freeze_services.py`; reports in `excel_reports.py`, `telegram_reports.py`, `daily_image_reports.py`.

### Domain flow
`Customer` → `Order` → `OrderItem` (an `Article`) → `OrderItemSize`. Cutting creates a `CuttingBatch` (a "pastal", code `P{MM}-{N}` from `generate_next_pastal_code`) with `CuttingBatchItem`s, which auto-generate `Box`es. `ProductModel` groups articles that share the same sewing operations; `Operation` is the global catalog, `ArticleOperation` is the per-article operation with `price_per_unit`, `sequence`, `is_active`.

> **OLTIN QOIDA (golden rule — the system's core pillar):** once created/printed, a sticker's `ticket_code` / `stiker_code` / QR and its box's `box_code` are **never regenerated, re-coded or deleted** — not when prices change, not when operations change, not in migrations, management commands or bug fixes. The QR only identifies the ticket; the scanner reads the latest price/operation data from the DB at scan time, so a price change updates DB fields only. A past bug that regenerated all old stickers caused major real-world losses. If a task seems to require touching existing sticker/box identity, stop and ask first.
>
> **Stable zone:** sticker generation/printing, terminal scanning and price templates (`ArticleOperation` pricing) work well and are considered finished — only small, minimal tweaks there. New development happens elsewhere; don't refactor these areas or change them as a side effect of other work.

Each `Box` gets one or more `Ticket`s (stickers, 8-char `stiker_code`) per active `ArticleOperation` via `services.generate_box_tickets`, which splits the box quantity with `allocate_ticket_quantities` (remainder goes to the first splits; never fractional). Regeneration **preserves existing ticket/sticker codes** and marks obsolete tickets `CANCELLED` rather than deleting them — keep that invariant, printed stickers must stay valid.

**Payroll is ticket-driven:** a master scans a worker's QR then ticket QRs at `/terminal/` (`terminal_views.py`); the ticket becomes `SCANNED` with `worker`, `scanned_at`, `screen_number`, and its `total_amount` is the worker's earnings. Daily/monthly pay are aggregates over scanned tickets. `MonthlyClosing` (OPEN → PENDING_FREEZE with a 7-day timer → FROZEN) and `Ticket.is_frozen` lock past months so price recalculations don't change already-paid salaries; `accounts/freeze_services.auto_freeze_past_months` freezes every past month not deliberately reopened by an admin. Respect `is_frozen` when changing any price/recalc logic. **Intended business rule:** a price change (`ArticleOperation.save()` → `sync_price_to_tickets`) reprices *all* unfrozen tickets, including ones already scanned earlier in the month — e.g. work done at month start is paid at the new price if the price rises at month end. Do not restrict this to PENDING tickets; freezing is the only lock.

After sewing, OTK quality control (`control_views.py`, `services.close_boxes_as_controlled`, `BoxQualityInspectionLog`, `DefectReason`) closes boxes as 1st grade / repair / defect. OTK's initial inspection overwrites `Box.quantity` with the counted total while the box's tickets keep their original split, so the sum of box quantities (the model page's "Qutilarda") is the real order count and can exceed the cutter's `CuttingBatchItem.quantity` — never regenerate tickets to reconcile this. `sewing_statistics_views.py` builds the pipeline statistics (in sewing → ironed ("dazmol") → waiting for OTK → OTK-closed) per patok/pastal/size. The order and model pages compute it from prefetched ticket objects; the factory total on `/sewing-statistics/` ("Fabrika bo'yicha jami") uses `production/sewing_pipeline_summary.py` instead (per-box SQL aggregation, ~2 s instead of ~15 s on real data) and must equal the sum of the order page's `order_totals` — `production/test_pipeline_summary.py` enforces this, so change both together.

**Final ironing = only the catalog operation named exactly "DAZMOL"** (owner's rule 2026-10-30; it is the model's last operation). PLANKA / TASMA / CHO'NTAK and any other "… DAZMOL" are intermediate, not finished work. Use `daily_dazmol.is_main_dazmol_operation` / `main_dazmol_operation_ids()` everywhere (daily page, 18:00 report, order/model statistics, TV screens, master tablet, superadmin) — never re-implement name matching.

**Daily dazmol has one source: `production/daily_dazmol.py::compute_daily_dazmol`.** *Asosiy dazmol* = final dazmol of boxes really accepted by a patok, credited to that patok wherever it was ironed. Main path (both branches): the Telegram passport bot (`run_passport_bot`) dispatches a pastal to a patok (`DISPATCHED`, K1–K13 / U1–U27), it appears on that patok's master tablet, and acceptance sets `Box.is_received_by_patok=True`. Extra Uychi path (owner's decision 2026-10-29): the Uychi warehouse in `meto_hub_views` (waybill receipt → batch `ACCEPTED`, `assigned_branch='UYCHI'`) hands the pastal to a U-patok (`Box.assigned_screen` ≥ 14) with no tablet step — that hand-out counts as acceptance. The batch must not be WAITING/DISPATCHED/REJECTED (`get_box_owner_screen` / `owned_box_q`); *patok dazmoli* = dazmol scanned at that patok's terminal; *egasiz* = dazmol of pastals nobody accepted (credited to no one, not in the 18:00 report). `/sewing-statistics/daily/` (asosiy per patok) and `/sewing-statistics/daily-report/` + the 18:00 image (`daily_image_reports.get_branch_daily_report_data`, per price template) must both read it so a patok's asosiy dazmol always equals the sum of its template lines — `production/test_daily_dazmol.py` enforces this. Both pages write `DailyPatokProgress.dazmol_qty` (monthly Excel) with the same asosiy value.

### Patoks (production lines) and screens
Lines are identified by an integer `screen_number` 1–40, but displayed with codes: 1–13 → `K1..K13` (HQ), 14+ → `U1..` (Uychi branch). Patok pickers (Telegram bot, master tablet, Uychi warehouse hand-out, superadmin user form) offer all of U1–U27 (screens 14–40); the 18:00 report and monthly Excel always list U1–U21 and add higher patoks when they have data. Always convert via `get_patok_code` / `get_patok_name` / `parse_patok_number` in `production/services.py`; login names follow `patokk{n}`/`patoku{n}` (CONTROL users) and `ekrank{n}`/`ekranu{n}` (SCREEN users). `User.assigned_screen_number` resolves a user's patok.

### Roles and access control
`accounts.User` (custom `AUTH_USER_MODEL`) has a `role` (SUPER_ADMIN, ADMIN, BRANCH_ADMIN, MANAGER, TECHNOLOGIST, CUTTER, METO, STICKER, MASTER, CONTROL, SCREEN, PLAN, USER) and a `branch` (HQ / UYCHI). Users and `Worker`s auto-generate a 6-digit `uid` and QR code on save. Path-level restriction for MASTER, ADMIN, CONTROL, SCREEN, CUTTER etc. is enforced centrally by `accounts/middleware.py::MasterTerminalAccessRestrictionMiddleware` via allow-listed URL prefixes (403 JSON for AJAX, redirect otherwise). **When adding a new URL a restricted role must reach, add its prefix to that middleware's allow-list**; views additionally use the `User.is_*()` helpers.

### Gotchas
- **Kun/Tun (day/night) theme:** pages are written dark-first with the stock Tailwind palette (`bg-slate-900`, `text-emerald-400`, …). `templates/includes/theme_head.html` (included in every app page's `<head>`, after the Tailwind CDN) routes Tailwind colour utilities through CSS variables whose fallbacks are the original colours (dark mode unchanged), and `html.theme-light` remaps them to readable light-mode values (≥4.5:1). So new markup gets light mode for free if it uses palette classes; hard-coded hex colours (`bg-[#...]`, inline `style`, page `<style>`) need an entry in that file's `ARBITRARY_BG` list or a `html.theme-light` rule. Toggle: `includes/theme_toggle.html` (`compact=True`, `label_class=...`); choice is stored in `localStorage.payroll_theme`, default dark. Print pages (stickers, badges, waybill, pastal passport) intentionally don't include the theme.
- **Frozen pages (owner's strict requirement):** `/control/` (`production/control_home.html`), `/screens/` (`screens/overview.html`) and all 40 TV monitors `/screens/<n>/` (`screens/monitor.html`) must stay exactly as they were. They include `includes/theme_head_legacy.html` (verbatim pre-redesign copy) instead of `theme_head.html`. Do not edit that file or apply design/theme changes to these pages unless the owner explicitly asks. Owner-requested tweaks on `/control/` (2026-10-30): the +1/qty/−1 stack sits left (clear of the accept button on landscape tablets), and ta'mir is yellow (amber) while 2-sort is red (rose) everywhere on the page — keep that scheme.
- `production/apps.py` monkey-patches `django.template.base.Template.__init__` to normalize whitespace inside `{{ }}` and `{% %}` (so multi-line tags and `|default: x` work). Templates may rely on this.
- Large monthly Excel exports (`excel_reports.py`) have previously OOM-crashed the 2-worker gunicorn container — stream/iterate querysets rather than materializing everything.
- Barcode scanner input: do not add Cyrillic→Latin keyboard transliteration (it was intentionally removed).
- `CHANGELOG.md` is a hand-maintained Uzbek log of significant commits (ID, files changed, features); it is updated alongside notable feature work.
- The repo root contains large DB/media backups (`*.sql.gz`, `*.dump`, `data_backup.json`); `restore_data.sh` loads them into the Docker stack. Don't read or modify them unless asked.
