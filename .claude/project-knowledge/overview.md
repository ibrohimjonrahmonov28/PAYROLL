# Project knowledge — overview (read this first)

TERRY JAR: piece-rate payroll + production tracking for a sewing factory. One Django 5.1 project (`config`), three apps (`accounts`, `production`, `screens`), ~176 source files / ~82k lines (templates ≈ 41k, tests ≈ 9.7k). PostgreSQL in prod, in-memory SQLite for tests (`USE_SQLITE=1`). UI/comments in Uzbek; TZ `Asia/Tashkent`.

## How to use this folder
| Need | Open |
|---|---|
| Which module owns a feature, its files, what it depends on | `modules.json` |
| URL → view function (full path, reverse name, decorators) | `api-map.json` |
| Model fields, FKs, on_delete, related_names, defining line | `database-map.json` |
| File → module, line count, content hash, top-level `def`/`class` with line numbers | `file-index.json` |
| Who imports whom (incl. lazy in-function imports), view → template, template → url names / fetch paths | `dependencies.json` |
| Request flow, domain pipeline, cross-cutting rules | `architecture.md` |
| Business process (9 stages) and the 10 golden rules, by the owner | `/SYSTEM_ARCHITECTURE_GUIDE.md` |
| Last validation result | `_validation.json` |

Use `symbols` in `file-index.json` (`name:line`) to Read only the needed range of very large files (`accounts/superadmin_views.py` 4.3k lines, `production/sewing_statistics_views.py` 3.5k, `production/tests.py` 4.6k, `templates/production/control_home.html` 2.9k).

## Module map (owner → main files)
- `accounts.core`: models.py (User, Worker, payouts, closings), views.py (login), middleware.py (role path guard)
- `accounts.superadmin`: superadmin_views.py + superadmin_urls.py (`/superadmin/`)
- `accounts.freeze`: freeze_services.py (salary freeze)
- `production.models`: models.py (all production models)
- `production.services`: services.py (tickets, boxes, patok codes, OTK close, pastal codes)
- Portals (`production.<x>`): orders (`views.py`), terminal, master, control, sewing_statistics, cutting, meto (+ meto_hub), stickers, managers, norma, passport
- `production.reports`: excel_reports.py, telegram_reports.py, daily_image_reports.py, daily_dazmol.py (single source for asosiy / patok / egasiz daily dazmol)
- `production.commands` / `accounts.commands`: management commands (scheduler daemon, bots, closings)
- `screens`: TV monitors per patok
- `android_control`: separate Java kiosk app (not part of Django)

## Regenerating
`USE_SQLITE=1 .venv/bin/python .claude/project-knowledge/build_graph.py` rewrites all `*.json` (descriptions live in the script's `MODULES` dict). It is read-only for the app. Run it after changing URLs, models, imports or adding/removing files. `overview.md` / `architecture.md` are hand-maintained.

JSON last regenerated: 2026-10-28, on top of commit `fb9806d` (Kun/Tun theme).
