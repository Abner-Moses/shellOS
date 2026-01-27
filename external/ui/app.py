#!/usr/bin/env python3
"""Minimal GTK IDE-style launcher with file explorer + terminal + browser + split mode."""

import json
import os
import subprocess
import mimetypes
from pathlib import Path
from shutil import which

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")

try:
    gi.require_version("WebKit2", "4.1")
    from gi.repository import WebKit2
    HAS_WEBKIT2 = True
except (ValueError, ImportError):
    WebKit2 = None
    HAS_WEBKIT2 = False

from gi.repository import Gtk, Vte, GLib, Gdk


APP_NAME = "Minimal IDE Launcher"
CONFIG_PATH = Path.home() / ".config" / "minimal-ide-launcher" / "config.json"
LOGO_PATH = Path(__file__).resolve().parent / "logo" / "logo.svg"

TERMINAL_SHELL = os.environ.get("SHELL", "/bin/bash")
BROWSER_CANDIDATES = ["brave-browser", "brave"]
FILE_MANAGER_CANDIDATES = [
    "nautilus",
    "thunar",
    "dolphin",
    "nemo",
    "pcmanfm",
    "caja",
    "lxqt-filedialog",
]


def find_exec(candidates):
    for name in candidates:
        path = which(name)
        if path:
            return path
    return None


def launch_brave():
    brave = find_exec(BROWSER_CANDIDATES)
    if brave:
        subprocess.Popen([brave])
        return True
    return False


def xdg_open(path):
    opener = which("xdg-open")
    if opener:
        result = subprocess.run([opener, path])
        return result.returncode == 0
    return False


def open_in_files(path):
    manager = find_exec(FILE_MANAGER_CANDIDATES)
    if manager:
        subprocess.Popen([manager, path])
        return True
    return xdg_open(path)


def open_file(path):
    return xdg_open(path)


def open_url_external(url):
    brave = find_exec(BROWSER_CANDIDATES)
    if brave:
        subprocess.Popen([brave, url])
        return True
    return False


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


class FileTree(Gtk.ScrolledWindow):
    def __init__(self, root_path, on_open, on_open_terminal):
        super().__init__()
        self.root_path = os.path.abspath(root_path)
        self.on_open = on_open
        self.on_open_terminal = on_open_terminal

        self.store = Gtk.TreeStore(str, str, str, bool)
        self.view = Gtk.TreeView(model=self.store)
        self.view.set_headers_visible(False)
        icon_renderer = Gtk.CellRendererPixbuf()
        text_renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("Name")
        column.pack_start(icon_renderer, False)
        column.pack_start(text_renderer, True)
        column.add_attribute(icon_renderer, "icon-name", 0)
        column.add_attribute(text_renderer, "text", 1)
        self.view.append_column(column)

        self.add(self.view)

        root_iter = self.store.append(None, ["folder", self.root_path, self.root_path, True])
        self._add_placeholder(root_iter)
        self.view.expand_row(self.store.get_path(root_iter), False)

        self.view.connect("row-expanded", self.on_row_expanded)
        self.view.connect("row-activated", self.on_row_activated)
        self.view.connect("button-press-event", self.on_button_press)

    def _add_placeholder(self, parent_iter):
        self.store.append(parent_iter, ["", "", "", False])

    def _is_placeholder(self, tree_iter):
        return self.store[tree_iter][2] == ""

    def _populate(self, parent_iter):
        path = self.store[parent_iter][2]
        if not os.path.isdir(path):
            return

        child = self.store.iter_children(parent_iter)
        if child and self._is_placeholder(child):
            self.store.remove(child)

        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        for entry in entries:
            is_dir = entry.is_dir(follow_symlinks=False)
            icon_name = "folder" if is_dir else "text-x-generic"
            child_iter = self.store.append(parent_iter, [icon_name, entry.name, entry.path, is_dir])
            if is_dir:
                self._add_placeholder(child_iter)

    def on_row_expanded(self, view, tree_iter, tree_path):
        self._populate(tree_iter)

    def on_row_activated(self, view, tree_path, column):
        tree_iter = self.store.get_iter(tree_path)
        path = self.store[tree_iter][2]
        is_dir = self.store[tree_iter][3]
        if is_dir:
            if view.row_expanded(tree_path):
                view.collapse_row(tree_path)
            else:
                view.expand_row(tree_path, False)
                self._populate(tree_iter)
        else:
            self.on_open(path)

    def get_selected_path(self):
        selection = self.view.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter:
            return model[tree_iter][2]
        return None

    def on_button_press(self, widget, event):
        if event.button != 3:
            return False

        path_info = self.view.get_path_at_pos(int(event.x), int(event.y))
        if path_info:
            path, column, _, _ = path_info
            self.view.set_cursor(path, column, False)

        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Open terminal here")
        item.connect("activate", self._open_terminal_here)
        menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _open_terminal_here(self, _):
        selected = self.get_selected_path()
        if not selected:
            return
        if os.path.isdir(selected):
            self.on_open_terminal(selected)
        else:
            self.on_open_terminal(os.path.dirname(selected))


class App(Gtk.Window):
    def __init__(self, root_path):
        super().__init__(title=APP_NAME)
        self.root_path = os.path.abspath(root_path)
        self.current_dir = self.root_path
        self.file_manager = find_exec(FILE_MANAGER_CANDIDATES)
        self.has_brave = find_exec(BROWSER_CANDIDATES) is not None

        self.set_default_size(1200, 760)

        cfg = load_config()
        if "window" in cfg:
            w, h = cfg.get("window", [1200, 760])
            self.set_default_size(int(w), int(h))
        self.lock_password = cfg.get("lock_password", "")

        self.connect("delete-event", self.on_close)

        self.apply_css()

        overlay = Gtk.Overlay()
        self.add(overlay)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        overlay.add(root)

        # Left navigation bar
        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        nav.set_size_request(70, -1)
        nav.set_border_width(10)
        nav.get_style_context().add_class("nav")
        root.pack_start(nav, False, False, 0)

        self.nav_dots = {}

        self.btn_terminal, self.dot_terminal = self.make_nav_item(
            nav, "utilities-terminal", "Terminal", self.show_terminal
        )
        self.btn_brave, self.dot_brave = self.make_nav_item(
            nav, "brave-browser", "Browser", self.show_browser
        )
        self.btn_files, self.dot_files = self.make_nav_item(
            nav, "system-file-manager", "Files", self.on_files_clicked
        )
        self.btn_settings, self.dot_settings = self.make_nav_item(
            nav, "preferences-system", "Settings", self.open_settings
        )

        nav.pack_end(Gtk.Label(label=""), True, True, 0)

        # Main area
        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        root.pack_start(main, True, True, 0)

        file_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        file_panel.get_style_context().add_class("file-panel")
        file_panel.set_size_request(300, -1)
        main.pack_start(file_panel, False, False, 0)

        self.path_label = Gtk.Label(label=self.current_dir)
        self.path_label.set_xalign(0)
        self.path_label.set_margin_start(10)
        self.path_label.set_margin_top(8)
        self.path_label.set_margin_bottom(8)
        file_panel.pack_start(self.path_label, False, False, 0)

        self.type_label = Gtk.Label(label="Type: Folder")
        self.type_label.set_xalign(0)
        self.type_label.set_margin_start(10)
        self.type_label.set_margin_bottom(2)
        self.type_label.get_style_context().add_class("meta")
        file_panel.pack_start(self.type_label, False, False, 0)

        self.parent_label = Gtk.Label(label=f"In: {self.root_path}")
        self.parent_label.set_xalign(0)
        self.parent_label.set_margin_start(10)
        self.parent_label.set_margin_bottom(8)
        self.parent_label.get_style_context().add_class("meta")
        file_panel.pack_start(self.parent_label, False, False, 0)

        self.file_tree = FileTree(self.root_path, on_open=self.open_file_with_feedback, on_open_terminal=self.open_terminal)
        file_panel.pack_start(self.file_tree, True, True, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.pack_start(content, True, True, 0)

        topbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        topbar.set_border_width(8)
        topbar.get_style_context().add_class("topbar")
        content.pack_start(topbar, False, False, 0)

        self.multitask_toggle = Gtk.ToggleButton(label="Multitask")
        self.multitask_toggle.connect("toggled", self.on_multitask_toggle)
        topbar.pack_end(self.multitask_toggle, False, False, 0)

        self.btn_lock = Gtk.Button(label="Lock")
        self.btn_lock.connect("clicked", lambda *_: self.show_lock())
        topbar.pack_end(self.btn_lock, False, False, 0)

        self.stack = Gtk.Stack()
        content.pack_start(self.stack, True, True, 0)

        # Terminal view
        self.terminal_main = Vte.Terminal()
        self.terminal_main.set_hexpand(True)
        self.terminal_main.set_vexpand(True)
        self.stack.add_named(self.terminal_main, "terminal")

        # Browser view (WebKit)
        self.browser_container, self.browser_view = self.build_browser_widget()
        self.stack.add_named(self.browser_container, "browser")

        # Split view
        self.split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.split.set_position(520)
        self.terminal_split = Vte.Terminal()
        self.terminal_split.set_hexpand(True)
        self.terminal_split.set_vexpand(True)
        self.split.pack1(self.terminal_split, resize=True, shrink=False)
        self.browser_split_container, self.browser_split_view = self.build_browser_widget()
        self.split.pack2(self.browser_split_container, resize=True, shrink=False)
        self.stack.add_named(self.split, "split")

        selection = self.file_tree.view.get_selection()
        selection.connect("changed", self.on_selection_changed)

        self.open_terminal(self.root_path)
        self.show_terminal()
        self.refresh_status()

        self.build_lock_overlay(overlay)

    def icon_button(self, icon_name, tooltip, handler):
        button = Gtk.Button()
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
        button.set_image(image)
        button.set_always_show_image(True)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text(tooltip)
        button.connect("clicked", handler)
        button.set_size_request(48, 48)
        return button

    def make_nav_item(self, nav, icon_name, tooltip, handler):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.get_style_context().add_class("nav-item")
        button = self.icon_button(icon_name, tooltip, handler)
        dot = Gtk.Label(label="•")
        dot.get_style_context().add_class("nav-dot")
        dot.set_visible(False)
        box.pack_start(button, False, False, 0)
        box.pack_start(dot, False, False, 0)
        nav.pack_start(box, False, False, 0)
        self.nav_dots[button] = dot
        return button, dot

    def show_terminal(self, *_):
        self.stack.set_visible_child_name("terminal")
        self.set_active(self.btn_terminal)
        if self.multitask_toggle.get_active():
            self.multitask_toggle.set_active(False)

    def show_browser(self, *_):
        self.stack.set_visible_child_name("browser")
        if not HAS_WEBKIT2:
            launch_brave()
        self.set_active(self.btn_brave)
        if self.multitask_toggle.get_active():
            self.multitask_toggle.set_active(False)

    def show_split(self, *_):
        self.stack.set_visible_child_name("split")
        if not HAS_WEBKIT2:
            launch_brave()
        self.set_active(self.btn_brave)
        if not self.multitask_toggle.get_active():
            self.multitask_toggle.set_active(True)

    def on_multitask_toggle(self, button):
        if button.get_active():
            self.show_split()
        else:
            self.show_terminal()

    def build_browser_widget(self):
        if HAS_WEBKIT2:
            view = WebKit2.WebView()
            toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            toolbar.set_border_width(6)

            btn_back = Gtk.Button()
            btn_back.set_image(Gtk.Image.new_from_icon_name("go-previous", Gtk.IconSize.MENU))
            btn_back.set_always_show_image(True)
            btn_back.connect("clicked", lambda *_: view.go_back())

            btn_forward = Gtk.Button()
            btn_forward.set_image(Gtk.Image.new_from_icon_name("go-next", Gtk.IconSize.MENU))
            btn_forward.set_always_show_image(True)
            btn_forward.connect("clicked", lambda *_: view.go_forward())

            btn_reload = Gtk.Button()
            btn_reload.set_image(Gtk.Image.new_from_icon_name("view-refresh", Gtk.IconSize.MENU))
            btn_reload.set_always_show_image(True)
            btn_reload.connect("clicked", lambda *_: view.reload())

            url_entry = Gtk.Entry()
            url_entry.set_placeholder_text("Enter URL or search")
            url_entry.connect("activate", self.on_url_activate, view)

            toolbar.pack_start(btn_back, False, False, 0)
            toolbar.pack_start(btn_forward, False, False, 0)
            toolbar.pack_start(btn_reload, False, False, 0)
            toolbar.pack_start(url_entry, True, True, 0)

            container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            container.pack_start(toolbar, False, False, 0)
            container.pack_start(view, True, True, 0)

            view.load_uri("https://search.brave.com/")
            return container, view

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_border_width(16)
        title = Gtk.Label(label="Embedded browser unavailable")
        title.get_style_context().add_class("dim-label")
        subtitle = Gtk.Label(
            label="Install WebKit2 (gir1.2-webkit2-4.1) to enable in-app browsing."
        )
        subtitle.set_line_wrap(True)
        btn = Gtk.Button(label="Open Brave")
        btn.connect("clicked", lambda *_: launch_brave())
        box.pack_start(title, False, False, 0)
        box.pack_start(subtitle, False, False, 0)
        box.pack_start(btn, False, False, 0)
        return box, None

    def on_url_activate(self, entry, view):
        text = entry.get_text().strip()
        if not text:
            return
        if "://" not in text:
            text = f"https://search.brave.com/search?q={text.replace(' ', '+')}"
        view.load_uri(text)

    def open_settings(self, *_):
        dialog = Gtk.Dialog(title="Settings", transient_for=self, flags=0)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)

        box = dialog.get_content_area()
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        row.set_border_width(12)

        label = Gtk.Label(label="Lock screen password")
        label.set_xalign(0)
        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_placeholder_text("Set a password (leave blank to disable)")
        entry.set_text(self.lock_password)

        note = Gtk.Label(label=f"Config: {CONFIG_PATH}")
        note.set_xalign(0)
        note.get_style_context().add_class("meta")

        row.pack_start(label, False, False, 0)
        row.pack_start(entry, False, False, 0)
        row.pack_start(note, False, False, 0)
        box.add(row)

        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.lock_password = entry.get_text()
            cfg = load_config()
            cfg["lock_password"] = self.lock_password
            save_config(cfg)
        dialog.destroy()

    def set_active(self, button):
        for btn, dot in self.nav_dots.items():
            dot.set_visible(btn is button)

    def build_lock_overlay(self, overlay):
        self.lock_overlay = Gtk.EventBox()
        self.lock_overlay.get_style_context().add_class("lock-overlay")
        self.lock_overlay.set_visible(False)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        center.set_halign(Gtk.Align.CENTER)
        center.set_valign(Gtk.Align.CENTER)
        center.get_style_context().add_class("lock-card")

        if LOGO_PATH.exists():
            logo = Gtk.Image.new_from_file(str(LOGO_PATH))
        else:
            logo = Gtk.Image.new_from_icon_name("system-lock-screen", Gtk.IconSize.DIALOG)
        center.pack_start(logo, False, False, 0)

        title = Gtk.Label(label="Locked")
        title.get_style_context().add_class("lock-title")
        center.pack_start(title, False, False, 0)

        self.lock_entry = Gtk.Entry()
        self.lock_entry.set_visibility(False)
        self.lock_entry.set_placeholder_text("Enter password")
        self.lock_entry.connect("activate", lambda *_: self.try_unlock())
        center.pack_start(self.lock_entry, False, False, 0)

        unlock_btn = Gtk.Button(label="Unlock")
        unlock_btn.connect("clicked", lambda *_: self.try_unlock())
        center.pack_start(unlock_btn, False, False, 0)

        self.lock_message = Gtk.Label(label="")
        self.lock_message.get_style_context().add_class("meta")
        center.pack_start(self.lock_message, False, False, 0)

        self.lock_overlay.add(center)
        overlay.add_overlay(self.lock_overlay)

    def show_lock(self):
        self.lock_overlay.set_visible(True)
        self.lock_entry.set_text("")
        self.lock_message.set_text("")
        self.lock_entry.grab_focus()

    def hide_lock(self):
        self.lock_overlay.set_visible(False)

    def try_unlock(self):
        if not self.lock_password:
            self.hide_lock()
            return
        if self.lock_entry.get_text() == self.lock_password:
            self.hide_lock()
        else:
            self.lock_message.set_text("Wrong password")

    def open_terminal(self, cwd):
        cwd = os.path.abspath(cwd)
        self.current_dir = cwd
        self.path_label.set_text(self.current_dir)
        self.spawn_terminal(self.terminal_main, cwd)
        self.spawn_terminal(self.terminal_split, cwd)

    def spawn_terminal(self, terminal, cwd):
        argv = [TERMINAL_SHELL]
        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            cwd,
            argv,
            [],
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            None,
        )

    def open_file_with_feedback(self, path):
        if open_file(path):
            return True
        self.show_error(
            "Cannot open file",
            "No application found to open files. Install a file manager or set defaults.",
        )
        return False

    def on_files_clicked(self, *_):
        ok = open_in_files(self.current_dir)
        if not ok:
            self.show_error(
                "Cannot open folder",
                "No file manager found. Install one like nautilus or thunar.",
            )

    def on_selection_changed(self, selection):
        model, tree_iter = selection.get_selected()
        if not tree_iter:
            return
        path = model[tree_iter][2]
        if not path:
            return
        if os.path.isdir(path):
            self.current_dir = path
            self.type_label.set_text("Type: Folder")
        else:
            self.current_dir = os.path.dirname(path)
            self.type_label.set_text(f"Type: {self.describe_file(path)}")
        self.path_label.set_text(self.current_dir)
        self.parent_label.set_text(f"In: {self.current_dir}")

    def show_error(self, title, message):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def describe_file(self, path):
        mime, _ = mimetypes.guess_type(path)
        ext = Path(path).suffix.lower() or "unknown"
        if mime:
            return f"{ext} • {mime}"
        return ext

    def refresh_status(self):
        if not self.has_brave:
            self.btn_brave.set_sensitive(False)
        if not self.file_manager and not which("xdg-open"):
            self.btn_files.set_sensitive(False)

    def apply_css(self):
        css = b"""
        window { background: #f3f1ee; }
        .nav { background: #111111; }
        .nav button { background: transparent; }
        .nav image { color: #f4f4f4; }
        .nav-item { padding: 4px 0; }
        .nav-dot { color: #f1c40f; font-size: 18px; }
        .file-panel { background: #fbfaf8; border-right: 1px solid #e6e1da; }
        .topbar { background: #f7f4f0; border-bottom: 1px solid #e6e1da; }
        label { color: #2a2a2a; }
        .meta { color: #6b645c; font-size: 11px; }
        .lock-overlay { background: rgba(12, 12, 12, 0.82); }
        .lock-card { background: #1c1c1c; padding: 24px; border-radius: 12px; }
        .lock-title { color: #f4f4f4; font-size: 20px; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def on_close(self, *_):
        cfg = load_config()
        alloc = self.get_allocation()
        cfg["window"] = [alloc.width, alloc.height]
        cfg["last_root"] = self.root_path
        save_config(cfg)
        Gtk.main_quit()


def ensure_display():
    if not os.environ.get("DISPLAY"):
        print("No DISPLAY found. Try running with X11 forwarding or xvfb-run.")
        return False
    return True


def main():
    if not ensure_display():
        return 1
    root_path = os.getcwd()
    cfg = load_config()
    if len(os.sys.argv) > 1:
        root_path = os.sys.argv[1]
    elif cfg.get("last_root"):
        root_path = cfg["last_root"]

    app = App(root_path)
    app.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
