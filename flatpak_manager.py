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

DEFAULT_CONFIG = {"lang": "system"}

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
    return "de" if loc.lower().startswith("de") else "en"


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

        # HeaderBar
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = t(s, "app_title")
        self.set_titlebar(header)

        self._lang_options = [("de", "lang_de"), ("en", "lang_en"),
                               ("system", "lang_system")]
        self.lang_menu_btn = Gtk.MenuButton()
        self.lang_menu_btn.set_size_request(130, -1)
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
        self.tv.get_selection().set_mode(Gtk.SelectionMode.SINGLE)

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
        self.btn_install = Gtk.Button(label=t(s, "btn_install"))
        self.btn_install.connect("clicked", lambda _b: self._on_install())
        self.btn_uninstall = Gtk.Button(label=t(s, "btn_uninstall"))
        self.btn_uninstall.connect("clicked", lambda _b: self._on_uninstall())
        self.btn_info = Gtk.Button(label=t(s, "btn_info"))
        self.btn_info.connect("clicked", lambda _b: self._on_info())
        self.btn_update_single = Gtk.Button(label=t(s, "btn_update_single"))
        self.btn_update_single.connect("clicked", lambda _b: self._on_update_single())
        self.btn_run = Gtk.Button(label=t(s, "btn_run"))
        self.btn_run.connect("clicked", lambda _b: self._on_run())
        self.chk_terminal = Gtk.CheckButton(label=t(s, "chk_terminal"))
        for w in [self.btn_install, self.btn_uninstall, self.btn_info,
                  self.btn_update_single, self.btn_run]:
            app_row.pack_start(w, False, False, 0)
        app_row.pack_start(self.chk_terminal, False, False, 12)
        vbox.pack_start(app_row, False, False, 0)

        # Extension actions
        lbl_ext = Gtk.Label(label=t(s, "section_ext_management"), xalign=0)
        lbl_ext.get_style_context().add_class("dim-label")
        vbox.pack_start(lbl_ext, False, False, 0)

        ext_row = Gtk.Box(spacing=6)
        self.btn_find_ext = Gtk.Button(label=t(s, "btn_find_extensions"))
        self.btn_find_ext.connect("clicked", lambda _b: self._on_find_extensions())
        self.btn_install_all_ext = Gtk.Button(label=t(s, "btn_install_all_extensions"))
        self.btn_install_all_ext.connect("clicked", lambda _b: self._on_install_all_extensions())
        self.btn_update_all = Gtk.Button(label=t(s, "btn_update_all"))
        self.btn_update_all.connect("clicked", lambda _b: self._on_update_all())
        for w in [self.btn_find_ext, self.btn_install_all_ext, self.btn_update_all]:
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

    def _get_selected_app_id(self):
        s = self.strings
        model, it = self.tv.get_selection().get_selected()
        if it is None:
            self._set_status(t(s, "warn_no_selection"))
            return None
        return model.get_value(it, 1)

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

    def _on_install(self):
        s = self.strings
        app_id = self._get_selected_app_id()
        if not app_id: return

        if app_id in self.installed_apps:
            self._info_dialog(t(s, "info_already_installed", id=app_id))
            return

        if not self._confirm(t(s, "confirm_install", id=app_id)):
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

        run_shell_live(f"flatpak install -y flathub {app_id}", on_line, on_done)

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

        run_shell_async(
            f"flatpak info {app_id} 2>/dev/null || flatpak remote-info flathub {app_id}", done)

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
        if not self._confirm(t(s, "confirm_install_extensions", n=len(to_install)) + "\n\n" + preview):
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

        run_shell_live(f"flatpak install -y flathub {ext_ids}", on_line, on_done)

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


def main():
    win = FlatpakManagerWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
