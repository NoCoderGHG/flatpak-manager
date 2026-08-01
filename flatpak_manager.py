#!/usr/bin/env python3
"""
Flatpak Manager - GTK3 frontend for flatpak
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

import json
import locale
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

CONFIG_DIR  = Path.home() / ".config" / "flatpak-manager"
CONFIG_FILE = CONFIG_DIR / "config.json"
I18N_DIR    = Path(__file__).parent / "i18n"

SUPPORTED_LANGUAGES = {
    "de": "Deutsch",
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "it": "Italiano",
    "pt": "Português",
    "nl": "Nederlands",
    "pl": "Polski",
    "ru": "Русский",
    "tr": "Türkçe",
    "zh": "中文",
    "ja": "日本語",
}


DEFAULT_CONFIG = {"lang": "system"}

APP_VERSION = "1.2"

PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

TERMINALS = ["gnome-terminal", "konsole", "xfce4-terminal",
              "mate-terminal", "terminator", "xterm"]


# ── Config & i18n ─────────────────────────────────────────────────────────────

def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def detect_system_lang():
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        loc = ""
    if not loc:
        loc = os.environ.get("LANG", "")
    code = loc.lower().split("_")[0].split(".")[0]
    if code in SUPPORTED_LANGUAGES and (I18N_DIR / f"{code}.json").exists():
        return code
    return "de" if code == "de" else "en"


def resolve_lang(setting):
    if setting == "system":
        return detect_system_lang()
    return setting


def load_i18n(lang):
    en = {}
    en_path = I18N_DIR / "en.json"
    if en_path.exists():
        with open(en_path) as f:
            en = json.load(f)
    if lang == "en":
        return en
    path = I18N_DIR / f"{lang}.json"
    if not path.exists():
        return en
    with open(path) as f:
        strings = json.load(f)
    for k, v in en.items():
        strings.setdefault(k, v)
    return strings

def build_lang_options(strings):
    """Liste (code, label) fuer das Sprachmenue. Sprachen ohne eigene
    i18n-Datei werden mit "(EN)" markiert (Fallback auf Englisch)."""
    opts = [("system", t(strings, "lang_system")),
            ("de", t(strings, "lang_de")),
            ("en", t(strings, "lang_en"))]
    for code, name in SUPPORTED_LANGUAGES.items():
        if code in ("de", "en"):
            continue
        label = name if (I18N_DIR / f"{code}.json").exists() else f"{name} (EN)"
        opts.append((code, label))
    return opts


def build_lang_lists(strings):
    """Wie build_lang_options, aber als getrennte Listen (codes, labels)."""
    codes, items = [], []
    for code, label in build_lang_options(strings):
        codes.append(code)
        items.append(label)
    return codes, items



def t(strings, key, **kwargs):
    s = strings.get(key, key)
    for k, v in kwargs.items():
        s = s.replace("{" + k + "}", str(v))
    return s


# ── Flatpak helpers ───────────────────────────────────────────────────────────

def run_shell(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def run_shell_async(cmd, on_done):
    def worker():
        rc, out, err = run_shell(cmd)
        GLib.idle_add(on_done, rc, out, err)
    threading.Thread(target=worker, daemon=True).start()


def run_shell_live(cmd, on_line, on_done):
    """Runs cmd, calling on_line(text) for each output line and
    on_done(returncode, full_output) when finished."""
    def worker():
        lines = []
        try:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line.strip():
                    lines.append(line)
                    GLib.idle_add(on_line, line)
            proc.wait()
            GLib.idle_add(on_done, proc.returncode, "\n".join(lines))
        except Exception as e:
            GLib.idle_add(on_done, -1, str(e))
    threading.Thread(target=worker, daemon=True).start()


def load_installed_apps():
    installed = set()
    rc, out, _ = run_shell("flatpak list --app --columns=application")
    if rc == 0:
        for line in out.strip().splitlines():
            line = line.strip()
            if line and not line.lower().startswith("application"):
                installed.add(line)
    rc, out, _ = run_shell("flatpak list --runtime --columns=application")
    if rc == 0:
        for line in out.strip().splitlines():
            line = line.strip()
            if line and not line.lower().startswith("application"):
                installed.add(line)
    return installed


def parse_search_output(output):
    apps = []
    lines = output.strip().splitlines()
    if not lines:
        return apps
    start = 1 if lines[0].startswith(("Name", "Application")) else 0
    for line in lines[start:]:
        if not line.strip():
            continue
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 3:
                app_id = parts[2].strip()
                if app_id and app_id.count(".") >= 2:
                    apps.append({
                        "name": parts[0].strip(), "id": app_id,
                        "version": parts[3].strip() if len(parts) > 3 else "",
                        "branch": parts[4].strip() if len(parts) > 4 else "stable",
                    })
                    continue
                for part in parts:
                    if "." in part and part.count(".") >= 2:
                        apps.append({"name": parts[0].strip(), "id": part.strip(),
                                     "version": "", "branch": "stable"})
                        break
        else:
            for part in line.split():
                if "." in part and part.count(".") >= 2:
                    apps.append({"name": line.split()[0], "id": part,
                                 "version": "", "branch": "stable"})
                    break
    return apps


def parse_list_output(output):
    apps = []
    for line in output.strip().splitlines():
        if not line.strip() or line.startswith("Name"):
            continue
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 2:
                apps.append({
                    "name": parts[0].strip(), "id": parts[1].strip(),
                    "version": parts[2].strip() if len(parts) > 2 else "",
                    "branch": parts[3].strip() if len(parts) > 3 else "stable",
                })
    return apps


def parse_runtime_extensions(output):
    extensions = []
    for line in output.strip().splitlines():
        if "Extension" in line or "extension" in line or "Plugin" in line:
            if "\t" in line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    extensions.append({
                        "name": parts[0].strip(), "id": parts[1].strip(),
                        "version": parts[2].strip() if len(parts) > 2 else "",
                        "branch": parts[3].strip() if len(parts) > 3 else "stable",
                    })
    return extensions


def find_terminal():
    for term in TERMINALS:
        if shutil.which(term):
            return term
    return None


SIZE_UNITS = {"B": 1, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
              "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4}

SIZE_RE = re.compile(r"([\d.,]+)\s*([KMGT]?i?B)", re.IGNORECASE)


def parse_size_to_bytes(text):
    """'12.3 MB' -> Bytes (0 wenn nicht parsebar)."""
    if not text:
        return 0
    m = SIZE_RE.search(text)
    if not m:
        return 0
    num = m.group(1).replace(",", ".")
    try:
        value = float(num)
    except ValueError:
        return 0
    return int(value * SIZE_UNITS.get(m.group(2).upper(), 1))


def human_size(num_bytes):
    if num_bytes <= 0:
        return "?"
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if num_bytes < 1000 or unit == "TB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes:.0f} B"
        num_bytes /= 1000.0
    return "?"


def fetch_entry_info(entry):
    """Ermittelt Download-/Installationsgroesse und Runtime fuer einen Queue-Eintrag.
    Laeuft im Worker-Thread. LC_ALL=C sorgt fuer stabile Feldnamen."""
    app_id = entry["id"]
    if entry["action"] == "install":
        cmd = (f"LC_ALL=C flatpak remote-info --system flathub {app_id} 2>/dev/null || "
               f"LC_ALL=C flatpak remote-info --user flathub {app_id} 2>/dev/null")
    else:
        cmd = f"LC_ALL=C flatpak info {app_id} 2>/dev/null"
    rc, out, _ = run_shell(cmd)
    size_text, runtime = "", ""
    for line in out.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("download:") and entry["action"] == "install":
            size_text = stripped.split(":", 1)[1].strip()
        elif low.startswith("installed:") and (entry["action"] == "remove" or not size_text):
            size_text = stripped.split(":", 1)[1].strip()
        elif low.startswith("runtime:"):
            runtime = stripped.split(":", 1)[1].strip()
    entry["size"] = size_text
    entry["bytes"] = parse_size_to_bytes(size_text)
    entry["runtime"] = runtime
    return rc == 0


# ── Main window ───────────────────────────────────────────────────────────────

class FlatpakManagerWindow(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.set_default_size(1000, 720)

        self.cfg = load_config()
        self.strings = load_i18n(resolve_lang(self.cfg.get("lang", "system")))
        s = self.strings

        self.set_title(t(s, "app_title"))
        self.installed_apps = set()
        self.current_extensions = []
        self.queue = []            # nur zur Laufzeit, nicht persistent
        self.queue_dialog = None

        # HeaderBar
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = t(s, "app_title")
        self.set_titlebar(header)

        self.btn_queue_open = Gtk.Button(label=t(s, "btn_queue_open", n=0))
        self.btn_queue_open.set_tooltip_text(t(s, "tooltip_queue"))
        self.btn_queue_open.connect("clicked", lambda _b: self._open_queue_dialog())
        header.pack_start(self.btn_queue_open)

        about_btn = Gtk.Button()
        about_btn.set_image(Gtk.Image.new_from_icon_name("help-about-symbolic", Gtk.IconSize.BUTTON))
        about_btn.set_tooltip_text(t(self.strings, "tooltip_about"))
        about_btn.connect("clicked", self._on_about)
        header.pack_end(about_btn)

        self._lang_options = build_lang_options(self.strings)
        self.lang_menu_btn = Gtk.MenuButton()
        self.lang_menu_btn.set_size_request(170, -1)
        self._lang_label = Gtk.Label()
        self.lang_menu_btn.add(self._lang_label)
        lang_menu = Gtk.Menu()
        group = []
        current_lang = self.cfg.get("lang", "system")
        for code, key in self._lang_options:
            item = Gtk.RadioMenuItem.new_with_label(group, t(s, key))
            group = item.get_group()
            if code == current_lang:
                item.set_active(True)
                self._lang_label.set_text(t(s, key))
            item.connect("activate", self._on_lang_menu_item, code)
            lang_menu.append(item)
        lang_menu.show_all()
        self.lang_menu_btn.set_popup(lang_menu)
        header.pack_end(self.lang_menu_btn)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        vbox.set_margin_start(10)
        vbox.set_margin_end(10)
        self.add(vbox)

        # Search row
        search_row = Gtk.Box(spacing=6)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(t(s, "search_placeholder"))
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", lambda _e: self._on_search())
        btn_search = Gtk.Button(label=t(s, "btn_search"))
        btn_search.get_style_context().add_class("suggested-action")
        btn_search.connect("clicked", lambda _b: self._on_search())
        search_row.pack_start(self.search_entry, True, True, 0)
        search_row.pack_start(btn_search, False, False, 0)
        vbox.pack_start(search_row, False, False, 0)

        # Quick actions row
        quick_row = Gtk.Box(spacing=6)
        for key, cb in [
            ("btn_show_installed",  self._on_show_installed),
            ("btn_show_extensions", self._on_show_extensions),
            ("btn_check_updates",   self._on_check_updates),
        ]:
            btn = Gtk.Button(label=t(s, key))
            btn.connect("clicked", lambda _, f=cb: f())
            quick_row.pack_start(btn, False, False, 0)
        vbox.pack_start(quick_row, False, False, 0)

        # TreeView: name, id, version, branch, status, weight
        self.store = Gtk.ListStore(str, str, str, str, str, int)
        self.tv = Gtk.TreeView(model=self.store)
        self.tv.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)

        for i, (key, expand, min_w) in enumerate([
            ("col_name", True, 200), ("col_id", True, 260),
            ("col_version", False, 80), ("col_branch", False, 70),
            ("col_status", False, 100),
        ]):
            r = Gtk.CellRendererText()
            r.set_property("ellipsize", Pango.EllipsizeMode.END)
            r.set_property("weight-set", True)
            col = Gtk.TreeViewColumn(t(s, key), r, text=i, weight=5)
            col.set_expand(expand)
            col.set_resizable(True)
            col.set_min_width(min_w)
            self.tv.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(200)
        scroll.add(self.tv)
        vbox.pack_start(scroll, True, True, 0)

        # App actions
        lbl_app = Gtk.Label(label=t(s, "section_app_management"), xalign=0)
        lbl_app.get_style_context().add_class("dim-label")
        vbox.pack_start(lbl_app, False, False, 0)

        app_row = Gtk.Box(spacing=6)
        self.btn_install_system = Gtk.Button(label=t(s, "btn_install_system"))
        self.btn_install_system.connect("clicked", lambda _b: self._on_install("--system"))
        self.btn_install_user = Gtk.Button(label=t(s, "btn_install_user"))
        self.btn_install_user.connect("clicked", lambda _b: self._on_install("--user"))
        self.btn_uninstall = Gtk.Button(label=t(s, "btn_uninstall"))
        self.btn_uninstall.connect("clicked", lambda _b: self._on_uninstall())
        self.btn_info = Gtk.Button(label=t(s, "btn_info"))
        self.btn_info.connect("clicked", lambda _b: self._on_info())
        self.btn_update_single = Gtk.Button(label=t(s, "btn_update_single"))
        self.btn_update_single.connect("clicked", lambda _b: self._on_update_single())
        self.btn_run = Gtk.Button(label=t(s, "btn_run"))
        self.btn_run.connect("clicked", lambda _b: self._on_run())
        self.chk_terminal = Gtk.CheckButton(label=t(s, "chk_terminal"))
        for w in [self.btn_install_system, self.btn_install_user, self.btn_uninstall,
                  self.btn_info, self.btn_update_single, self.btn_run]:
            app_row.pack_start(w, False, False, 0)
        app_row.pack_start(self.chk_terminal, False, False, 12)
        vbox.pack_start(app_row, False, False, 0)

        # Queue actions
        lbl_queue = Gtk.Label(label=t(s, "section_queue"), xalign=0)
        lbl_queue.get_style_context().add_class("dim-label")
        vbox.pack_start(lbl_queue, False, False, 0)

        queue_row = Gtk.Box(spacing=6)
        self.btn_queue_add = Gtk.Button(label=t(s, "btn_queue_add"))
        self.btn_queue_add.set_tooltip_text(t(s, "tooltip_queue_add"))
        self.btn_queue_add.connect("clicked", lambda _b: self._on_queue_add(None))
        self.btn_queue_add_install = Gtk.Button(label=t(s, "btn_queue_add_install"))
        self.btn_queue_add_install.connect("clicked", lambda _b: self._on_queue_add("install"))
        self.btn_queue_add_remove = Gtk.Button(label=t(s, "btn_queue_add_remove"))
        self.btn_queue_add_remove.connect("clicked", lambda _b: self._on_queue_add("remove"))
        self.btn_queue_show = Gtk.Button(label=t(s, "btn_queue_show"))
        self.btn_queue_show.get_style_context().add_class("suggested-action")
        self.btn_queue_show.connect("clicked", lambda _b: self._open_queue_dialog())
        for w in [self.btn_queue_add, self.btn_queue_add_install,
                  self.btn_queue_add_remove, self.btn_queue_show]:
            queue_row.pack_start(w, False, False, 0)
        vbox.pack_start(queue_row, False, False, 0)

        # Extension actions
        lbl_ext = Gtk.Label(label=t(s, "section_ext_management"), xalign=0)
        lbl_ext.get_style_context().add_class("dim-label")
        vbox.pack_start(lbl_ext, False, False, 0)

        ext_row = Gtk.Box(spacing=6)
        self.btn_find_ext = Gtk.Button(label=t(s, "btn_find_extensions"))
        self.btn_find_ext.connect("clicked", lambda _b: self._on_find_extensions())
        self.btn_install_all_ext = Gtk.Button(label=t(s, "btn_install_all_extensions"))
        self.btn_install_all_ext.connect("clicked", lambda _b: self._on_install_all_extensions())
        self.btn_queue_all_ext = Gtk.Button(label=t(s, "btn_queue_all_extensions"))
        self.btn_queue_all_ext.connect("clicked", lambda _b: self._on_queue_all_extensions())
        self.btn_update_all = Gtk.Button(label=t(s, "btn_update_all"))
        self.btn_update_all.connect("clicked", lambda _b: self._on_update_all())
        for w in [self.btn_find_ext, self.btn_install_all_ext,
                  self.btn_queue_all_ext, self.btn_update_all]:
            ext_row.pack_start(w, False, False, 0)
        vbox.pack_start(ext_row, False, False, 0)

        # Progress bar
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(False)
        self.progress.set_no_show_all(True)
        self.progress.set_visible(False)
        vbox.pack_start(self.progress, False, False, 0)

        # Output
        lbl_out = Gtk.Label(label=t(s, "frame_output"), xalign=0)
        lbl_out.get_style_context().add_class("dim-label")
        vbox.pack_start(lbl_out, False, False, 0)

        out_scroll = Gtk.ScrolledWindow()
        out_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        out_scroll.set_min_content_height(140)
        self.output_view = Gtk.TextView()
        self.output_view.set_editable(False)
        self.output_view.set_monospace(True)
        self.output_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.output_buf = self.output_view.get_buffer()
        out_scroll.add(self.output_view)
        vbox.pack_start(out_scroll, False, False, 0)

        # Statusbar
        self.statusbar = Gtk.Statusbar()
        self.ctx = self.statusbar.get_context_id("main")
        vbox.pack_start(self.statusbar, False, False, 0)

        self._set_status(t(s, "status_ready"))
        GLib.idle_add(self._initial_load)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, text):
        self.statusbar.pop(self.ctx)
        self.statusbar.push(self.ctx, text)

    def _set_output(self, text):
        self.output_buf.set_text(text)
        end = self.output_buf.get_end_iter()
        self.output_view.scroll_to_iter(end, 0, False, 0, 0)

    def _append_output(self, text):
        end = self.output_buf.get_end_iter()
        self.output_buf.insert(end, text + "\n")
        end = self.output_buf.get_end_iter()
        self.output_view.scroll_to_iter(end, 0, False, 0, 0)
        m = PERCENT_RE.search(text)
        if m:
            try:
                self.progress.set_fraction(min(1.0, float(m.group(1)) / 100))
            except ValueError:
                pass
        return False

    def _begin_busy(self):
        self.progress.set_visible(True)
        self.progress.set_fraction(0.0)

    def _end_busy(self):
        self.progress.set_visible(False)
        self.progress.set_fraction(0.0)

    def _populate_tree(self, apps):
        s = self.strings
        self.store.clear()
        for app in apps:
            is_installed = app["id"] in self.installed_apps
            status = t(s, "status_installed_label") if is_installed else ""
            weight = Pango.Weight.BOLD if is_installed else Pango.Weight.NORMAL
            self.store.append([app["name"], app["id"], app.get("version", ""),
                               app.get("branch", "stable"), status, weight])

    def _get_selected_rows(self):
        """Liste aus (name, id) aller markierten Zeilen."""
        model, paths = self.tv.get_selection().get_selected_rows()
        rows = []
        for path in paths:
            it = model.get_iter(path)
            rows.append((model.get_value(it, 0), model.get_value(it, 1)))
        return rows

    def _get_selected_app_id(self):
        s = self.strings
        rows = self._get_selected_rows()
        if not rows:
            self._set_status(t(s, "warn_no_selection"))
            return None
        return rows[0][1]

    def _confirm(self, text):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=text,
        )
        resp = dialog.run()
        dialog.destroy()
        return resp == Gtk.ResponseType.YES

    def _info_dialog(self, text):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK, text=text,
        )
        dialog.run()
        dialog.destroy()

    def _initial_load(self):
        self.installed_apps = load_installed_apps()
        self._on_show_installed()
        return False

    # ── Queue ─────────────────────────────────────────────────────────────────

    def _update_queue_button(self):
        s = self.strings
        pending = sum(1 for e in self.queue if e["state"] in ("pending", "failed"))
        self.btn_queue_open.set_label(t(s, "btn_queue_open", n=pending))

    def _queue_entry(self, app_id):
        for entry in self.queue:
            if entry["id"] == app_id:
                return entry
        return None

    def _queue_add_entry(self, name, app_id, action):
        """Fuegt hinzu oder aktualisiert die Aktion. Rueckgabe: 'added' | 'updated'."""
        entry = self._queue_entry(app_id)
        if entry:
            if entry["action"] == action:
                return "exists"
            entry["action"] = action
            entry["state"] = "pending"
            entry["size"] = ""
            entry["bytes"] = 0
            entry["log"] = ""
            return "updated"
        self.queue.append({
            "name": name, "id": app_id, "action": action,
            "size": "", "bytes": 0, "runtime": "", "state": "pending", "log": "",
        })
        return "added"

    def _on_queue_add(self, action=None):
        """action=None -> automatisch (installiert = entfernen, sonst installieren)."""
        s = self.strings
        rows = self._get_selected_rows()
        if not rows:
            self._set_status(t(s, "warn_no_selection"))
            return

        added = updated = skipped = 0
        for name, app_id in rows:
            act = action
            if act is None:
                act = "remove" if app_id in self.installed_apps else "install"
            result = self._queue_add_entry(name, app_id, act)
            if result == "added":
                added += 1
            elif result == "updated":
                updated += 1
            else:
                skipped += 1

        self._update_queue_button()
        if self.queue_dialog:
            self.queue_dialog.refresh()
        self._set_status(t(s, "queue_added_status", added=added,
                           updated=updated, skipped=skipped))

    def _on_queue_all_extensions(self):
        s = self.strings
        if not self.current_extensions:
            self._info_dialog(t(s, "info_no_extensions_to_install"))
            return
        to_add = [e for e in self.current_extensions if e["id"] not in self.installed_apps]
        if not to_add:
            self._info_dialog(t(s, "info_all_extensions_installed"))
            return
        added = 0
        for ext in to_add:
            if self._queue_add_entry(ext["name"], ext["id"], "install") == "added":
                added += 1
        self._update_queue_button()
        if self.queue_dialog:
            self.queue_dialog.refresh()
        self._set_status(t(s, "queue_added_status", added=added, updated=0,
                           skipped=len(to_add) - added))

    def _open_queue_dialog(self):
        if self.queue_dialog:
            self.queue_dialog.present()
            return
        dlg = QueueDialog(self)
        self.queue_dialog = dlg
        dlg.connect("destroy", self._on_queue_dialog_closed)
        dlg.show_all()

    def _on_queue_dialog_closed(self, _dlg):
        self.queue_dialog = None
        self._update_queue_button()

    def queue_finished(self):
        """Wird vom Dialog nach dem Abarbeiten aufgerufen."""
        self.installed_apps = load_installed_apps()
        self._update_queue_button()
        self._on_show_installed()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_search(self):
        s = self.strings
        query = self.search_entry.get_text().strip()
        if not query:
            self._set_status(t(s, "warn_no_query"))
            return

        self.installed_apps = load_installed_apps()
        self._set_output(t(s, "status_searching", query=query))
        self._set_status(t(s, "status_searching", query=query))

        def done(rc, out, err):
            if rc == 0 and out.strip():
                apps = parse_search_output(out)
                if apps:
                    self._populate_tree(apps)
                    self._set_output(t(s, "status_apps_found", n=len(apps), query=query))
                    self._set_status(t(s, "status_apps_found_short", n=len(apps)))
                else:
                    self._set_output(t(s, "status_parse_failed", out=out))
                    self._set_status(t(s, "status_parse_error"))
            else:
                if err.strip():
                    self._set_output(t(s, "err_generic", err=err))
                    self._set_status(t(s, "err_search_failed"))
                else:
                    self._set_output(t(s, "status_no_apps_found", query=query))
                    self._set_status(t(s, "status_no_apps_found_short"))

        run_shell_async(f"flatpak search {query}", done)

    def _on_show_installed(self):
        s = self.strings
        self._set_output(t(s, "status_loading_installed"))
        self._set_status(t(s, "status_loading_installed"))

        def done(rc, out, err):
            if rc == 0:
                apps = parse_list_output(out)
                if apps:
                    self.installed_apps = {a["id"] for a in apps}
                    self._populate_tree(apps)
                    self._set_output(t(s, "status_installed_count", n=len(apps)))
                    self._set_status(t(s, "status_installed_count", n=len(apps)))
                else:
                    self._set_output(t(s, "status_no_installed"))
                    self._set_status(t(s, "status_no_installed"))
            else:
                self._set_output(t(s, "err_loading", err=err))
                self._set_status(t(s, "err_loading_short"))

        run_shell_async("flatpak list --app", done)

    def _on_show_extensions(self):
        s = self.strings
        self._set_output(t(s, "status_loading_extensions"))
        self._set_status(t(s, "status_loading_extensions"))

        def done(rc, out, err):
            if rc == 0:
                exts = parse_runtime_extensions(out)
                if exts:
                    self._populate_tree(exts)
                    self._set_output(t(s, "status_extensions_count", n=len(exts)))
                    self._set_status(t(s, "status_extensions_count", n=len(exts)))
                else:
                    self._set_output(t(s, "status_no_extensions"))
                    self._set_status(t(s, "status_no_extensions"))
            else:
                self._set_output(t(s, "err_loading", err=err))
                self._set_status(t(s, "err_loading_short"))

        run_shell_async("flatpak list --runtime", done)

    def _on_install(self, scope="--system"):
        s = self.strings
        app_id = self._get_selected_app_id()
        if not app_id: return

        if app_id in self.installed_apps:
            self._info_dialog(t(s, "info_already_installed", id=app_id))
            return

        scope_label = t(s, "scope_system") if scope == "--system" else t(s, "scope_user")
        if not self._confirm(t(s, "confirm_install", id=app_id) + f"\n({scope_label})"):
            return

        self._set_output(t(s, "status_install_starting", id=app_id))
        self._set_status(t(s, "status_installing", id=app_id))
        self._begin_busy()

        def on_line(line):
            self._append_output(line)

        def on_done(rc, out):
            self._end_busy()
            if rc == 0:
                self._append_output(t(s, "status_install_success", id=app_id))
                self._info_dialog(t(s, "msg_installed", id=app_id))
                self._set_status(t(s, "status_install_success_short"))
                self.installed_apps = load_installed_apps()
                if "Extension" in app_id or "Plugin" in app_id:
                    self._on_show_extensions()
            else:
                self._append_output(t(s, "status_install_failed", id=app_id))
                self._set_status(t(s, "status_install_failed_short"))

        run_shell_live(f"flatpak install -y {scope} flathub {app_id}", on_line, on_done)

    def _on_uninstall(self):
        s = self.strings
        app_id = self._get_selected_app_id()
        if not app_id: return

        if app_id not in self.installed_apps:
            self._info_dialog(t(s, "info_not_installed", id=app_id))
            return

        if not self._confirm(t(s, "confirm_uninstall", id=app_id)):
            return

        self._set_output(t(s, "status_uninstalling", id=app_id))
        self._set_status(t(s, "status_uninstalling", id=app_id))
        self._begin_busy()

        def on_line(line):
            self._append_output(line)

        def on_done(rc, out):
            self._end_busy()
            if rc == 0:
                self._append_output(t(s, "status_uninstall_success", id=app_id))
                self._info_dialog(t(s, "msg_uninstalled", id=app_id))
                self._set_status(t(s, "status_uninstall_success_short"))
                self._on_show_installed()
            else:
                self._append_output(t(s, "status_uninstall_failed", id=app_id))
                self._set_status(t(s, "status_uninstall_failed_short"))

        run_shell_live(f"flatpak uninstall -y {app_id}", on_line, on_done)

    def _on_info(self):
        s = self.strings
        app_id = self._get_selected_app_id()
        if not app_id: return

        self._set_output(t(s, "status_loading_info", id=app_id))
        self._set_status(t(s, "status_loading_info", id=app_id))

        def done(rc, out, err):
            text = out if out.strip() else err
            self._set_output(text)
            self._set_status(t(s, "status_info_loaded"))

        # flatpak info für installierte Apps; remote-info mit explizitem Scope für nicht installierte
        run_shell_async(
            f"flatpak info {app_id} 2>/dev/null || "
            f"flatpak remote-info --system flathub {app_id} 2>/dev/null || "
            f"flatpak remote-info --user flathub {app_id}",
            done)

    def _on_check_updates(self):
        s = self.strings
        self._set_output(t(s, "status_checking_updates"))
        self._set_status(t(s, "status_checking_updates_short"))

        def after_appstream(rc1, out1, err1):
            def after_updates(rc2, out2, err2):
                if not out2.strip():
                    self._set_output(t(s, "status_all_uptodate"))
                    self._set_status(t(s, "status_all_uptodate"))
                    return
                updates = []
                for line in out2.strip().splitlines():
                    if not line.strip(): continue
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        name, app_id = parts[0].strip(), parts[1].strip()
                        if "." in app_id:
                            updates.append((name, app_id))
                if updates:
                    text = t(s, "status_updates_available", n=len(updates))
                    text += "\n".join(f"• {n} ({i})" for n, i in updates)
                    text += t(s, "status_updates_hint")
                    self._set_output(text)
                    self._set_status(t(s, "status_updates_count", n=len(updates)))
                else:
                    self._set_output(t(s, "status_all_uptodate"))
                    self._set_status(t(s, "status_all_uptodate"))

            run_shell_async("flatpak remote-ls --updates", after_updates)

        run_shell_async("flatpak update --appstream", after_appstream)

    def _on_update_all(self):
        s = self.strings
        if not self._confirm(t(s, "confirm_update_all")):
            return

        self._set_output(t(s, "status_updating_all"))
        self._set_status(t(s, "status_updating_all"))
        self._begin_busy()

        def on_line(line):
            self._append_output(line)

        def on_done(rc, out):
            self._end_busy()
            if rc == 0:
                self._append_output(t(s, "status_update_all_success"))
                self._info_dialog(t(s, "msg_update_all_success"))
                self._set_status(t(s, "status_update_all_success_short"))
            else:
                self._append_output(t(s, "status_update_all_failed"))
                self._set_status(t(s, "status_update_all_failed_short"))

        run_shell_live("flatpak update -y", on_line, on_done)

    def _on_update_single(self):
        s = self.strings
        app_id = self._get_selected_app_id()
        if not app_id: return

        if app_id not in self.installed_apps:
            self._info_dialog(t(s, "info_not_installed", id=app_id))
            return

        if not self._confirm(t(s, "confirm_update_single", id=app_id)):
            return

        self._set_output(t(s, "status_updating", id=app_id))
        self._set_status(t(s, "status_updating", id=app_id))
        self._begin_busy()

        def on_line(line):
            self._append_output(line)

        def on_done(rc, out):
            self._end_busy()
            if rc == 0:
                if "Nothing to do" in out or "Nichts zu tun" in out:
                    self._append_output(t(s, "status_already_uptodate", id=app_id))
                    self._set_status(t(s, "status_already_uptodate_short"))
                else:
                    self._append_output(t(s, "status_update_success", id=app_id))
                    self._info_dialog(t(s, "msg_update_success", id=app_id))
                    self._set_status(t(s, "status_update_success_short"))
            else:
                self._append_output(t(s, "status_update_failed", id=app_id))
                self._set_status(t(s, "status_update_failed_short"))

        run_shell_live(f"flatpak update -y {app_id}", on_line, on_done)

    def _on_find_extensions(self):
        s = self.strings
        app_id = self._get_selected_app_id()
        if not app_id: return

        base_id = app_id
        parts = app_id.split(".")
        for marker in ("Plugin", "Extension"):
            if marker in parts:
                idx = parts.index(marker)
                base_id = ".".join(parts[:idx])
                break

        self._set_output(t(s, "status_searching_extensions", id=base_id))
        self._set_status(t(s, "status_searching_extensions", id=base_id))

        patterns = [f"{base_id}.Plugin", f"{base_id}.Extension", f"{base_id}.Addon"]
        all_exts = []
        remaining = [0]

        def collect(rc, out, err):
            if rc == 0 and out.strip():
                all_exts.extend(parse_search_output(out))
            remaining[0] -= 1
            if remaining[0] == 0:
                seen = set()
                unique = []
                for ext in all_exts:
                    if ext["id"] not in seen:
                        seen.add(ext["id"])
                        unique.append(ext)
                if unique:
                    self._populate_tree(unique)
                    self._set_output(t(s, "status_extensions_found", n=len(unique), id=base_id))
                    self._set_status(t(s, "status_extensions_found_short", n=len(unique)))
                    self.current_extensions = unique
                else:
                    self._set_output(t(s, "status_no_extensions_found", id=base_id))
                    self._set_status(t(s, "status_no_extensions_found_short"))
                    self.current_extensions = []

        remaining[0] = len(patterns)
        for pattern in patterns:
            run_shell_async(f"flatpak search {pattern}", collect)

    def _on_install_all_extensions(self):
        s = self.strings
        if not self.current_extensions:
            self._info_dialog(t(s, "info_no_extensions_to_install"))
            return

        to_install = [e for e in self.current_extensions if e["id"] not in self.installed_apps]
        if not to_install:
            self._info_dialog(t(s, "info_all_extensions_installed"))
            return

        preview = "\n".join(f"• {e['name']}" for e in to_install[:5])
        if len(to_install) > 5:
            preview += "\n• ..."

        # Scope-Auswahl per Dialog
        scope_dlg = Gtk.Dialog(
            title=t(s, "confirm_install_extensions", n=len(to_install)),
            transient_for=self, modal=True,
        )
        scope_dlg.add_button(t(s, "btn_install_system"), 1)
        scope_dlg.add_button(t(s, "btn_install_user"), 2)
        scope_dlg.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        lbl = Gtk.Label(label=preview)
        lbl.set_margin_start(16); lbl.set_margin_end(16)
        lbl.set_margin_top(12); lbl.set_margin_bottom(12)
        scope_dlg.get_content_area().add(lbl)
        scope_dlg.show_all()
        resp = scope_dlg.run()
        scope_dlg.destroy()

        if resp == 1:
            scope = "--system"
        elif resp == 2:
            scope = "--user"
        else:
            return

        self._set_output(t(s, "status_installing_extensions", n=len(to_install)))
        self._set_status(t(s, "status_installing_extensions", n=len(to_install)))
        self._begin_busy()

        ext_ids = " ".join(e["id"] for e in to_install)

        def on_line(line):
            self._append_output(line)

        def on_done(rc, out):
            self._end_busy()
            if rc == 0:
                self._append_output(t(s, "status_install_extensions_success"))
                self._info_dialog(t(s, "msg_extensions_installed", n=len(to_install)))
                self._set_status(t(s, "status_install_extensions_success_short"))
                self.installed_apps = load_installed_apps()
                self._on_find_extensions()
            else:
                self._append_output(t(s, "status_install_extensions_failed"))
                self._set_status(t(s, "status_install_extensions_failed_short"))

        run_shell_live(f"flatpak install -y {scope} flathub {ext_ids}", on_line, on_done)

    def _on_run(self):
        s = self.strings
        app_id = self._get_selected_app_id()
        if not app_id: return

        if app_id not in self.installed_apps:
            self._info_dialog(t(s, "info_not_installed", id=app_id))
            return

        self._set_status(t(s, "status_starting_app", id=app_id))

        if self.chk_terminal.get_active():
            term = find_terminal()
            if not term:
                self._info_dialog(t(s, "err_no_terminal"))
                self._set_status(t(s, "err_no_terminal"))
                return
            cmd = f"{term} -- flatpak run {app_id}" if term == "gnome-terminal" else f"{term} -e flatpak run {app_id}"
            self._append_output(t(s, "status_run_terminal", id=app_id))
        else:
            cmd = f"flatpak run {app_id} &"
            self._append_output(t(s, "status_starting_app", id=app_id))

        try:
            subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
            self._set_status(t(s, "status_app_started", id=app_id))
            self._append_output(t(s, "status_app_started", id=app_id))
        except Exception as e:
            self._append_output(t(s, "err_app_start", id=app_id, err=e))
            self._set_status(t(s, "err_app_start_short"))

    # ── Language ──────────────────────────────────────────────────────────────

    def _on_about(self, _btn):
        dlg = Gtk.AboutDialog(transient_for=self, modal=True)
        dlg.set_program_name(t(self.strings, "app_title"))
        dlg.set_version(APP_VERSION)
        dlg.set_comments(t(self.strings, "about_comments"))
        dlg.set_license_type(Gtk.License.MIT_X11)
        dlg.run()
        dlg.destroy()

    def _on_lang_menu_item(self, item, code):
        if not item.get_active(): return
        if code == self.cfg.get("lang"): return
        self.cfg["lang"] = code
        save_config(self.cfg)
        for c, key in self._lang_options:
            if c == code:
                self._lang_label.set_text(t(self.strings, key))
                break
        new_strings = load_i18n(resolve_lang(code))
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=t(new_strings, "restart_hint"),
        )
        dlg.run()
        dlg.destroy()


# ── Queue window ──────────────────────────────────────────────────────────────

class QueueDialog(Gtk.Window):
    """Merkliste: mehrere Apps/Extensions vormerken und gesammelt
    installieren bzw. entfernen. Existiert nur zur Laufzeit."""

    STATE_KEYS = {
        "pending":  "queue_state_pending",
        "running":  "queue_state_running",
        "ok":       "queue_state_ok",
        "failed":   "queue_state_failed",
        "skipped":  "queue_state_skipped",
    }

    def __init__(self, parent):
        super().__init__()
        self.parent_win = parent
        self.strings = parent.strings
        s = self.strings

        self.set_transient_for(parent)
        self.set_destroy_with_parent(True)
        self.set_default_size(880, 620)
        self.set_title(t(s, "queue_title"))

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = t(s, "queue_title")
        self.set_titlebar(header)

        self.running = False
        self.cancelled = False

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for setter in ("set_margin_top", "set_margin_bottom",
                       "set_margin_start", "set_margin_end"):
            getattr(vbox, setter)(10)
        self.add(vbox)

        # Liste
        self.store = Gtk.ListStore(str, str, str, str, str, int)
        self.tv = Gtk.TreeView(model=self.store)
        self.tv.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        self.tv.get_selection().connect("changed", self._on_row_selected)

        for i, (key, expand, min_w) in enumerate([
            ("queue_col_action", False, 110), ("col_name", True, 180),
            ("col_id", True, 240), ("queue_col_size", False, 90),
            ("queue_col_status", False, 110),
        ]):
            r = Gtk.CellRendererText()
            r.set_property("ellipsize", Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(t(s, key), r, text=i)
            col.set_expand(expand)
            col.set_resizable(True)
            col.set_min_width(min_w)
            self.tv.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(200)
        scroll.add(self.tv)
        vbox.pack_start(scroll, True, True, 0)

        # Aktionen
        action_row = Gtk.Box(spacing=6)
        self.btn_fetch = Gtk.Button(label=t(s, "btn_queue_fetch_info"))
        self.btn_fetch.connect("clicked", lambda _b: self._on_fetch_info())
        self.btn_remove = Gtk.Button(label=t(s, "btn_queue_remove_entry"))
        self.btn_remove.connect("clicked", lambda _b: self._on_remove_entry())
        self.btn_clear = Gtk.Button(label=t(s, "btn_queue_clear"))
        self.btn_clear.connect("clicked", lambda _b: self._on_clear())
        for w in (self.btn_fetch, self.btn_remove, self.btn_clear):
            action_row.pack_start(w, False, False, 0)

        action_row.pack_start(Gtk.Label(label=t(s, "queue_scope_label")), False, False, 12)
        self.scope_combo = Gtk.ComboBoxText()
        self.scope_combo.append("--system", t(s, "scope_system"))
        self.scope_combo.append("--user", t(s, "scope_user"))
        self.scope_combo.set_active_id("--system")
        action_row.pack_start(self.scope_combo, False, False, 0)

        self.btn_run = Gtk.Button(label=t(s, "btn_queue_run"))
        self.btn_run.get_style_context().add_class("suggested-action")
        self.btn_run.connect("clicked", lambda _b: self._on_run())
        self.btn_cancel = Gtk.Button(label=t(s, "btn_queue_cancel"))
        self.btn_cancel.set_sensitive(False)
        self.btn_cancel.connect("clicked", lambda _b: self._on_cancel())
        action_row.pack_end(self.btn_run, False, False, 0)
        action_row.pack_end(self.btn_cancel, False, False, 0)
        vbox.pack_start(action_row, False, False, 0)

        # Zusammenfassung + Fortschritt
        self.summary = Gtk.Label(xalign=0)
        self.summary.get_style_context().add_class("dim-label")
        vbox.pack_start(self.summary, False, False, 0)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        vbox.pack_start(self.progress, False, False, 0)

        # Log
        lbl_log = Gtk.Label(label=t(s, "queue_log_label"), xalign=0)
        lbl_log.get_style_context().add_class("dim-label")
        vbox.pack_start(lbl_log, False, False, 0)

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_min_content_height(150)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_buf = self.log_view.get_buffer()
        log_scroll.add(self.log_view)
        vbox.pack_start(log_scroll, False, False, 0)

        self.refresh()

    # ── Anzeige ───────────────────────────────────────────────────────────────

    @property
    def queue(self):
        return self.parent_win.queue

    def _selected_index(self):
        model, it = self.tv.get_selection().get_selected()
        if it is None:
            return -1
        return model.get_value(it, 5)

    def refresh(self, keep_index=None):
        s = self.strings
        if keep_index is None:
            keep_index = self._selected_index()
        self.store.clear()
        for idx, e in enumerate(self.queue):
            action_label = t(s, "queue_action_install" if e["action"] == "install"
                             else "queue_action_remove")
            state_label = t(s, self.STATE_KEYS.get(e["state"], "queue_state_pending"))
            self.store.append([action_label, e["name"], e["id"],
                               e["size"] or "–", state_label, idx])
        if 0 <= keep_index < len(self.queue):
            self.tv.get_selection().select_path(Gtk.TreePath(keep_index))
        self._update_summary()
        self.parent_win._update_queue_button()

    def _update_summary(self):
        s = self.strings
        installs = sum(1 for e in self.queue if e["action"] == "install")
        removes = sum(1 for e in self.queue if e["action"] == "remove")
        dl_bytes = sum(e.get("bytes", 0) for e in self.queue if e["action"] == "install")
        free_bytes = sum(e.get("bytes", 0) for e in self.queue if e["action"] == "remove")
        self.summary.set_text(t(s, "queue_summary",
                                total=len(self.queue), installs=installs,
                                removes=removes, download=human_size(dl_bytes),
                                freed=human_size(free_bytes)))

    def _set_log(self, text):
        self.log_buf.set_text(text)
        self.log_view.scroll_to_iter(self.log_buf.get_end_iter(), 0, False, 0, 0)

    def _on_row_selected(self, _sel):
        if self.running:
            return
        idx = self._selected_index()
        s = self.strings
        if idx < 0 or idx >= len(self.queue):
            self._set_log("")
            return
        entry = self.queue[idx]
        header = ""
        if entry.get("runtime"):
            header = t(s, "queue_runtime", runtime=entry["runtime"]) + "\n"
        self._set_log(header + (entry.get("log") or t(s, "queue_log_empty")))

    def _set_buttons_running(self, running):
        for w in (self.btn_fetch, self.btn_remove, self.btn_clear,
                  self.btn_run, self.scope_combo):
            w.set_sensitive(not running)
        self.btn_cancel.set_sensitive(running)

    # ── Aktionen ──────────────────────────────────────────────────────────────

    def _on_remove_entry(self):
        idx = self._selected_index()
        if idx < 0 or idx >= len(self.queue):
            return
        del self.queue[idx]
        self.refresh(keep_index=min(idx, len(self.queue) - 1))
        self._set_log("")

    def _on_clear(self):
        if not self.queue:
            return
        self.queue.clear()
        self.refresh(keep_index=-1)
        self._set_log("")

    def _on_fetch_info(self):
        s = self.strings
        pending = [e for e in self.queue if not e.get("size")]
        if not pending:
            self._update_summary()
            return
        self._set_buttons_running(True)
        self.progress.set_fraction(0.0)
        self.progress.set_text(t(s, "queue_fetching_info"))
        total = len(pending)

        def worker():
            for i, entry in enumerate(pending, 1):
                fetch_entry_info(entry)
                GLib.idle_add(self._fetch_progress, i, total)
            GLib.idle_add(self._fetch_done)

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_progress(self, done, total):
        self.progress.set_fraction(done / total)
        return False

    def _fetch_done(self):
        s = self.strings
        self._set_buttons_running(False)
        self.progress.set_fraction(0.0)
        self.progress.set_text(t(s, "queue_info_done"))
        self.refresh()
        return False

    def _on_cancel(self):
        self.cancelled = True
        self.btn_cancel.set_sensitive(False)

    def _on_run(self):
        s = self.strings
        todo = [e for e in self.queue if e["state"] in ("pending", "failed")]
        if not todo:
            dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                    message_type=Gtk.MessageType.INFO,
                                    buttons=Gtk.ButtonsType.OK,
                                    text=t(s, "queue_nothing_to_do"))
            dlg.run()
            dlg.destroy()
            return

        installs = sum(1 for e in todo if e["action"] == "install")
        removes = len(todo) - installs
        dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.YES_NO,
                                text=t(s, "queue_confirm_run",
                                       installs=installs, removes=removes))
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.YES:
            return

        scope = self.scope_combo.get_active_id() or "--system"
        self.running = True
        self.cancelled = False
        self._set_buttons_running(True)
        self.progress.set_fraction(0.0)

        for entry in todo:
            entry["state"] = "pending"
            entry["log"] = ""
        self.refresh()

        total = len(todo)

        def worker():
            for i, entry in enumerate(todo):
                if self.cancelled:
                    entry["state"] = "skipped"
                    GLib.idle_add(self._entry_updated, entry, i, total, 0.0)
                    continue
                entry["state"] = "running"
                GLib.idle_add(self._entry_started, entry, i, total)

                if entry["action"] == "install":
                    cmd = f"flatpak install -y {scope} flathub {entry['id']}"
                else:
                    cmd = f"flatpak uninstall -y {entry['id']}"
                entry["log"] += f"$ {cmd}\n"
                GLib.idle_add(self._append_log_line, entry, f"$ {cmd}")

                try:
                    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, bufsize=1)
                    for line in proc.stdout:
                        line = line.rstrip("\n")
                        if not line.strip():
                            continue
                        entry["log"] += line + "\n"
                        GLib.idle_add(self._append_log_line, entry, line)
                        m = PERCENT_RE.search(line)
                        if m:
                            try:
                                pct = min(1.0, float(m.group(1)) / 100)
                            except ValueError:
                                pct = 0.0
                            GLib.idle_add(self._entry_progress, i, total, pct)
                    proc.wait()
                    rc = proc.returncode
                except Exception as exc:               # noqa: BLE001
                    entry["log"] += str(exc) + "\n"
                    rc = -1

                entry["state"] = "ok" if rc == 0 else "failed"
                GLib.idle_add(self._entry_updated, entry, i, total, 1.0)

            GLib.idle_add(self._run_finished, todo)

        threading.Thread(target=worker, daemon=True).start()

    # ── Callbacks aus dem Worker ──────────────────────────────────────────────

    def _entry_started(self, entry, index, total):
        s = self.strings
        self.progress.set_text(t(s, "queue_running_entry", id=entry["id"],
                                 n=index + 1, total=total))
        self._set_log(entry.get("log") or "")
        self._sync_row_state(entry)
        return False

    def _entry_progress(self, index, total, pct):
        self.progress.set_fraction((index + pct) / total)
        return False

    def _entry_updated(self, entry, index, total, pct):
        self.progress.set_fraction((index + pct) / total)
        self._sync_row_state(entry)
        return False

    def _append_log_line(self, entry, line):
        if entry["state"] != "running":
            return False
        end = self.log_buf.get_end_iter()
        self.log_buf.insert(end, line + "\n")
        self.log_view.scroll_to_iter(self.log_buf.get_end_iter(), 0, False, 0, 0)
        return False

    def _sync_row_state(self, entry):
        s = self.strings
        for row in self.store:
            if row[2] == entry["id"]:
                row[4] = t(s, self.STATE_KEYS.get(entry["state"], "queue_state_pending"))
                break
        return False

    def _run_finished(self, todo):
        s = self.strings
        self.running = False
        self.cancelled = False
        self._set_buttons_running(False)
        ok = sum(1 for e in todo if e["state"] == "ok")
        failed = sum(1 for e in todo if e["state"] == "failed")
        skipped = sum(1 for e in todo if e["state"] == "skipped")
        self.progress.set_fraction(1.0 if not failed and not skipped else self.progress.get_fraction())
        self.progress.set_text(t(s, "queue_finished", ok=ok, failed=failed, skipped=skipped))
        self.parent_win.queue_finished()
        self.refresh()
        return False


def main():
    win = FlatpakManagerWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
