"""
Regenerates the machine-derived parts of the project knowledge graph.

    USE_SQLITE=1 .venv/bin/python .claude/project-knowledge/build_graph.py

Writes modules.json, dependencies.json, api-map.json, database-map.json, file-index.json
next to this script. overview.md / architecture.md are hand-written and not touched.
Read-only with respect to the application: it only imports Django, walks the URL
resolver / model registry and parses source with `ast`.
"""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("USE_SQLITE", "1")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.apps import apps  # noqa: E402
from django.urls import URLPattern, URLResolver, get_resolver  # noqa: E402

# ---------------------------------------------------------------- file selection
EXCLUDE_RE = re.compile(
    r"(^|/)migrations/|\.(json|sql|gz|dump|apk|idsig|keystore|ttf|png|jpg|pdf)$"
    r"|^(data_backup|initial_data)|^\.env|^\.gitignore|^\.dockerignore|\.gitkeep$"
)

# Hand-maintained module descriptions (kept here so regeneration preserves them).
MODULES = {
    "config": "Django project: settings (env-driven DB selection, Telegram IDs), root URLconf, WSGI/ASGI.",
    "accounts.core": "Custom User (roles, branch, uid/QR), Worker, payouts, daily/monthly closing, master history; login/logout; role-based path restriction middleware.",
    "accounts.superadmin": "Superadmin portal (/superadmin/): users & badges, payroll, payouts, month freeze, pricing, orders CRUD, defect reasons, orders balance.",
    "accounts.freeze": "Salary freeze service: auto-freeze past months, 7-day freeze timers (MonthlyClosing / Ticket.is_frozen).",
    "accounts.commands": "accounts management commands (daily payroll close, freeze timers, user seeding).",
    "production.models": "All production domain models (Customer → Order → OrderItem → Box → Ticket, cutting batches, waybills, OTK, progress).",
    "production.services": "Core business logic: ticket allocation/generation, box creation, patok code helpers, OTK close, pastal code generation.",
    "production.orders": "Main dashboard, orders, box split/print, sticker PDFs, articles catalog, pipeline statistics (/ , /orders/, /boxes/).",
    "production.terminal": "Master scanning terminal (/terminal/) — scanning tickets assigns them to workers (payroll source).",
    "production.master": "Master tablet (accept/reject pastals per patok) and master statistics.",
    "production.control": "OTK quality control portal (/control/) and Android control APK download.",
    "production.sewing_statistics": "Sewing pipeline statistics, daily report (image/Telegram/Excel), OTK and repairs views.",
    "production.cutting": "Cutting portal (/cutting/): cutting batches (pastals) and auto-box generation.",
    "production.meto": "Meto (numbering) portal (/meto/) and meto hub with waybills (/meto-hub/).",
    "production.stickers": "Sticker printing portal (/stickers/) and 100x60 sticker PDF generator.",
    "production.managers": "Manager portal (/managers/): order creation and overview.",
    "production.norma": "Norma (daily norm) portal (/norma/): models, operations catalog, canvas, worker norm breakdown.",
    "production.passport": "Pastal passport checker (web + Telegram webhook) and the passport Telegram bot service.",
    "production.reports": "Excel reports, Telegram daily Excel report, 18:00 branch image reports.",
    "production.commands": "production management commands (scheduler daemon, passport bot, reports, backups, cleanup).",
    "production.platform": "AppConfig (template whitespace monkey-patch) and Django admin registrations.",
    "screens": "Shop-floor TV monitors per patok (/screens/) with JSON polling API.",
    "templates": "Server-rendered Django templates, grouped by portal.",
    "tests": "Django TestCase suites.",
    "android_control": "Standalone Android (Java) kiosk app for control tablets; built separately (build_apk.sh). Not imported by Django.",
    "ops": "Docker / nginx / restore and one-off scripts.",
}


def module_of(path: str) -> str:
    p = path
    if p.startswith("config/"):
        return "config"
    if p.startswith("templates/"):
        return "templates"
    if p.startswith("android_control/"):
        return "android_control"
    if re.search(r"(^|/)tests?\.py$|/test_[^/]+\.py$", p):
        return "tests"
    if p.startswith("accounts/management/"):
        return "accounts.commands"
    if p.startswith("production/management/"):
        return "production.commands"
    if p.startswith("accounts/superadmin"):
        return "accounts.superadmin"
    if p == "accounts/freeze_services.py":
        return "accounts.freeze"
    if p.startswith("accounts/"):
        return "accounts.core"
    if p.startswith("screens/"):
        return "screens"
    if p.startswith("production/"):
        name = p.split("/", 1)[1]
        table = [
            ("models.py", "production.models"),
            ("services.py", "production.services"),
            ("terminal_", "production.terminal"),
            ("master_", "production.master"),
            ("control_", "production.control"),
            ("sewing_statistics", "production.sewing_statistics"),
            ("sewing_pipeline", "production.sewing_statistics"),
            ("cutting_", "production.cutting"),
            ("meto", "production.meto"),
            ("sticker", "production.stickers"),
            ("manager_", "production.managers"),
            ("norma_", "production.norma"),
            ("passport", "production.passport"),
            ("excel_reports", "production.reports"),
            ("telegram_reports", "production.reports"),
            ("daily_image_reports", "production.reports"),
            ("daily_dazmol", "production.reports"),
            ("views.py", "production.orders"),
            ("urls.py", "production.orders"),
            ("apps.py", "production.platform"),
            ("admin.py", "production.platform"),
        ]
        for prefix, mod in table:
            if name.startswith(prefix):
                return mod
        return "production.platform"
    return "ops"


def tracked_files():
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    return [f for f in out if not EXCLUDE_RE.search(f) and (ROOT / f).is_file()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def dotted(path: str) -> str:
    return path[:-3].replace("/", ".").removesuffix(".__init__")


# ---------------------------------------------------------------- python analysis
INTERNAL_TOP = {"config", "accounts", "production", "screens"}


def analyze_py(path: str, src: str):
    tree = ast.parse(src)
    symbols, imports = [], []
    pkg = dotted(path).rsplit(".", 1)[0]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(f"{node.name}:{node.lineno}")

    top_level_nodes = set(id(n) for n in tree.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lazy = id(node) not in top_level_nodes
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports.append((a.name, [], lazy))
            else:
                mod = node.module or ""
                if node.level:
                    base = pkg.split(".")
                    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                    mod = ".".join(base + ([mod] if mod else []))
                names = [a.name for a in node.names]
                if not node.module and node.level:
                    # `from . import views` -> modules
                    for n in names:
                        imports.append((f"{mod}.{n}", [], lazy))
                else:
                    imports.append((mod, names, lazy))

    templates = sorted(set(re.findall(r"['\"]([\w/\-]+\.html)['\"]", src)))
    decorators = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
            decs = []
            for d in node.decorator_list:
                target = d.func if isinstance(d, ast.Call) else d
                decs.append(ast.unparse(target))
            decorators[node.name] = decs
    return symbols, imports, templates, decorators


# ---------------------------------------------------------------- build
files = tracked_files()
existing = set(files)
file_index = {}
internal_edges = defaultdict(lambda: {"imports": set(), "lazy": set()})
external = defaultdict(set)
py_templates = {}
view_decorators = {}
template_refs = {}
unverified = []

TEMPLATE_DIR = ROOT / "templates"

for f in files:
    p = ROOT / f
    text = p.read_text(errors="replace")
    entry = {"module": module_of(f), "lines": text.count("\n") + 1, "sha256_16": sha(p)}
    if f.endswith(".py"):
        try:
            symbols, imports, templates, decs = analyze_py(f, text)
        except SyntaxError as e:
            unverified.append({"file": f, "issue": f"unparseable: {e}"})
            symbols, imports, templates, decs = [], [], [], {}
        if symbols:
            entry["symbols"] = symbols
        me = dotted(f)
        for mod, names, lazy in imports:
            top = mod.split(".")[0]
            if top in INTERNAL_TOP:
                internal_edges[me]["lazy" if lazy else "imports"].add(mod)
            elif mod and top not in sys.stdlib_module_names:
                external[top].add(f)
        if templates:
            py_templates[f] = templates
            for t in templates:
                if not (TEMPLATE_DIR / t).exists():
                    unverified.append({"file": f, "issue": f"template string '{t}' not found under templates/ (may be a filename, not a template)"})
        for fn, d in decs.items():
            view_decorators[f"{me}.{fn}"] = d
    elif f.startswith("templates/") and f.endswith(".html"):
        extends = re.findall(r"{%\s*extends\s+['\"]([^'\"]+)['\"]", text)
        includes = re.findall(r"{%\s*include\s+['\"]([^'\"]+)['\"]", text)
        url_names = sorted(set(re.findall(r"{%\s*url\s+['\"]([^'\"]+)['\"]", text)))
        fetches = sorted(set(x for x in re.findall(r"""fetch\(\s*[`'"]([^`'"$]+)""", text)
                             if x.startswith("/")))
        refs = {k: v for k, v in {"extends": extends, "includes": includes,
                                   "url_names": url_names, "fetch_paths": fetches}.items() if v}
        if refs:
            template_refs[f] = refs
    file_index[f] = entry

# ---------------------------------------------------------------- URLs
routes = []


def walk(patterns, prefix="", ns=None, urlconf=None):
    for p in patterns:
        if isinstance(p, URLResolver):
            if p.namespace == "admin":
                routes.append({"path": "/" + prefix + str(p.pattern), "name": "admin:*",
                               "view": "django.contrib.admin (registrations in */admin.py)", "urlconf": "config.urls"})
                continue
            sub_ns = ":".join(x for x in [ns, p.namespace] if x) or None
            walk(p.url_patterns, prefix + str(p.pattern), sub_ns, p.urlconf_name if isinstance(p.urlconf_name, str) else getattr(p.urlconf_name, "__name__", None))
        elif isinstance(p, URLPattern):
            cb = p.callback
            view = getattr(cb, "view_class", None)
            if view is not None:
                target = f"{view.__module__}.{view.__name__}"
            else:
                inner, wrappers = cb, []
                for _ in range(10):
                    if hasattr(inner, "__wrapped__"):
                        inner = inner.__wrapped__
                    elif "<locals>" in inner.__qualname__ and inner.__closure__:
                        # project decorators don't use functools.wraps; the view is in the closure
                        fns = [c.cell_contents for c in inner.__closure__
                               if callable(getattr(c, "cell_contents", None))
                               and hasattr(c.cell_contents, "__code__")]
                        if not fns:
                            break
                        wrappers.append(inner.__qualname__.split(".<locals>")[0])
                        inner = fns[0]
                    else:
                        break
                target = f"{inner.__module__}.{inner.__qualname__}"
            name = f"{ns}:{p.name}" if ns and p.name else p.name
            r = {"path": "/" + prefix + str(p.pattern), "name": name, "view": target,
                 "urlconf": urlconf or "config.urls"}
            if target in view_decorators:
                r["decorators"] = view_decorators[target]
            if "<lambda>" in target:
                r["unverified"] = "lambda view defined inline in urlconf"
            routes.append(r)


walk(get_resolver().url_patterns)
route_names = {r["name"] for r in routes if r["name"]}
route_regexes = []
for r in routes:
    if "(?P" in r["path"] or "^" in r["path"]:
        r["regex_pattern"] = True
        continue
    parts = re.split(r"<[^>]+>", r["path"])
    route_regexes.append(re.compile("^" + "[^/]+".join(re.escape(x) for x in parts) + "$"))
literal_paths = [r["path"] for r in routes if not r.get("regex_pattern")]

fetch_checked = {"exact": 0, "dynamic_prefix": 0, "unmatched": 0}
for t, refs in template_refs.items():
    for n in refs.get("url_names", []):
        if n not in route_names:
            unverified.append({"file": t, "issue": f"{{% url '{n}' %}} not found in resolver"})
    for fp in refs.get("fetch_paths", []):
        path = fp.split("?")[0]
        if any(rx.match(path) for rx in route_regexes):
            fetch_checked["exact"] += 1
        elif any(lp.startswith(path) for lp in literal_paths) or any(
                rx.match(path + "1/") for rx in route_regexes):
            fetch_checked["dynamic_prefix"] += 1  # JS appends ids/segments at runtime
        else:
            fetch_checked["unmatched"] += 1
            unverified.append({"file": t, "issue": f"fetch path '{fp}' matches no route (built dynamically or stale)"})

# Middleware allow-list prefixes vs real routes
mw = (ROOT / "accounts/middleware.py").read_text()
mw_prefixes = sorted(set(re.findall(r"'(/[a-z0-9\-_/]*)'", mw)))
all_paths = [r["path"] for r in routes] + ["/static/", "/media/"]
# '/api/' is used as a substring test (`'/api/' in path`), not a prefix
mw_prefixes = [p for p in mw_prefixes if p != "/api/"]
dead_prefixes = [p for p in mw_prefixes if not any(ap.startswith(p) or p.startswith(ap.rstrip("/")) and ap != "/" for ap in all_paths)]

# ---------------------------------------------------------------- models
models_out = {}
for model in apps.get_models():
    app = model._meta.app_label
    if app not in INTERNAL_TOP:
        continue
    fields = {}
    relations = []
    for fld in model._meta.get_fields():
        if fld.auto_created and not fld.concrete:
            continue
        if fld.is_relation and fld.concrete or getattr(fld, "many_to_many", False) and not fld.auto_created:
            rel = {"field": fld.name, "type": type(fld).__name__,
                   "to": f"{fld.related_model._meta.app_label}.{fld.related_model.__name__}" if fld.related_model else None}
            od = getattr(getattr(fld, "remote_field", None), "on_delete", None)
            if od:
                rel["on_delete"] = od.__name__
            rn = getattr(getattr(fld, "remote_field", None), "related_name", None)
            if rn:
                rel["related_name"] = rn
            relations.append(rel)
        elif fld.concrete:
            flags = []
            if getattr(fld, "unique", False) and not fld.primary_key:
                flags.append("unique")
            if getattr(fld, "db_index", False):
                flags.append("idx")
            if getattr(fld, "choices", None):
                flags.append("choices")
            fields[fld.name] = type(fld).__name__ + (f"[{','.join(flags)}]" if flags else "")
    src = __import__(model.__module__, fromlist=["x"]).__file__
    import inspect
    try:
        line = inspect.getsourcelines(model)[1]
    except OSError:
        line = None
    models_out[f"{app}.{model.__name__}"] = {
        "table": model._meta.db_table,
        "file": f"{Path(src).relative_to(ROOT)}:{line}",
        "fields": fields,
        "relations": relations,
        **({"unique_together": [list(u) for u in model._meta.unique_together]} if model._meta.unique_together else {}),
    }

migrations = {}
for app in sorted(INTERNAL_TOP - {"config"}):
    d = ROOT / app / "migrations"
    names = sorted(x.stem for x in d.glob("0*.py")) if d.exists() else []
    migrations[app] = {"count": len(names), "latest": names[-1] if names else None}

# ---------------------------------------------------------------- modules
mod_files = defaultdict(list)
for f, e in file_index.items():
    mod_files[e["module"]].append(f)
mod_deps = defaultdict(set)
for src_mod, edges in internal_edges.items():
    src_file = src_mod.replace(".", "/") + ".py"
    if src_file not in file_index:
        src_file = src_mod.replace(".", "/") + "/__init__.py"
    owner = file_index.get(src_file, {}).get("module")
    if not owner:
        continue
    for target in edges["imports"] | edges["lazy"]:
        cand = [target.replace(".", "/") + ".py", target.rsplit(".", 1)[0].replace(".", "/") + ".py"]
        for c in cand:
            if c in file_index:
                tm = file_index[c]["module"]
                if tm != owner:
                    mod_deps[owner].add(tm)
                break

modules_json = {
    m: {
        "description": MODULES.get(m, "UNVERIFIED: no description"),
        "files": sorted(fs),
        "lines": sum(file_index[f]["lines"] for f in fs),
        "depends_on": sorted(mod_deps.get(m, [])),
    }
    for m, fs in sorted(mod_files.items())
}

# ---------------------------------------------------------------- write
req = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines() if l.strip()]


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + "\n")


dump("file-index.json", {
    "_note": "sha256_16 = first 16 hex chars of sha256 of file content at generation time. symbols = top-level def/class name:line. Excludes migrations, backups, binaries, secrets.",
    "files": file_index,
})
dump("modules.json", modules_json)
dump("dependencies.json", {
    "_note": "Static analysis via ast/regex. 'lazy' = imported inside a function (often to avoid circular imports). Template refs are regex-extracted.",
    "requirements_txt": req,
    "external_packages_used_by": {k: sorted(v) for k, v in sorted(external.items())},
    "internal_imports": {k: {"imports": sorted(v["imports"]), **({"lazy": sorted(v["lazy"])} if v["lazy"] else {})}
                         for k, v in sorted(internal_edges.items())},
    "python_to_templates": py_templates,
    "template_refs": template_refs,
})
dump("api-map.json", {
    "_note": "Generated from Django's URL resolver (authoritative). Paths are full; 'name' is reverse() name incl. namespace. Access is additionally filtered by accounts/middleware.py allow-lists per role.",
    "route_count": len(routes),
    "routes": routes,
    "middleware_allowlist_prefixes": mw_prefixes,
    "middleware_prefixes_without_route": dead_prefixes,
})
dump("database-map.json", {
    "_note": "Generated from Django model registry. Field flags: unique, idx (db_index), choices. Relations list concrete FK/O2O/M2M only.",
    "migrations": migrations,
    "models": models_out,
})
dump("_validation.json", {"fetch_path_check": fetch_checked,
                          "middleware_prefixes_without_route": dead_prefixes,
                          "unverified": unverified})
print(f"files={len(file_index)} routes={len(routes)} models={len(models_out)} unverified={len(unverified)} dead_mw_prefixes={dead_prefixes}")
