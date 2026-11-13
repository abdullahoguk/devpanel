#!/usr/bin/env python3
"""DevPanel — dev projelerini ve tunnel'ları başlat/durdur/izle (systemd --user).

  devpanel.py                 Ana pencere (GTK4 + libadwaita)
  devpanel.py tray            Panel simgesi (AppIndicator)
  devpanel.py install         Menü kısayolu + oturum açılışında tray
  devpanel.py status | start | stop | restart | logs <proje> [satır]
"""
import json, os, re, shlex, shutil, signal, subprocess, sys, threading, time
import gi

APP_ID = "com.kod8.DevPanel"
SELF = os.path.abspath(__file__)
CFG = os.path.expanduser("~/.config/devpanel/projects.json")
PREFIX = "devpanel-"
RUNNING = {"active", "activating", "external"}
STATE = {  # durum: (metin, css sınıfı, tray emoji)
    "active": ("Çalışıyor", "success", "🟢"),
    "activating": ("Başlıyor", "warning", "🟡"),
    "deactivating": ("Duruyor", "warning", "🟡"),
    "failed": ("Hata verdi", "error", "🔴"),
    "external": ("Dışarıda çalışıyor", "accent", "🔵"),
    "busy": ("Port başka bir süreçte", "warning", "🟠"),
}


def bg(fn, done=None):
    """Uzun süren start/stop'u arayüzü kilitlemeden arka planda çalıştır."""
    def run():
        r = fn()
        if done:
            from gi.repository import GLib
            GLib.idle_add(lambda: (done(r), False)[1])
    threading.Thread(target=run, daemon=True).start()
OFF = ("Kapalı", "dim-label", "⚪")
EXAMPLE = [{"name": "Cloudflare Tunnel", "dir": "~", "cmd": "cloudflared tunnel run TUNEL_ADI"}]


# ───────────────────────── çekirdek ─────────────────────────
def sh(*a):
    return subprocess.run(a, capture_output=True, text=True)


def load():
    if not os.path.exists(CFG):
        save(EXAMPLE)
    with open(CFG) as f:
        return json.load(f).get("projects", [])


def save(projects):
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    with open(CFG, "w") as f:
        json.dump({"projects": projects}, f, ensure_ascii=False, indent=2)


def cfg_mtime():
    try:
        return os.path.getmtime(CFG)
    except OSError:
        return 0


def slug(p):
    return p.get("id") or re.sub(r"[^a-z0-9_-]+", "-", p["name"].lower()).strip("-")


def unit(p):
    return f"{PREFIX}{slug(p)}.service"


def port(p):
    if str(p.get("port", "")).isdigit():
        return int(p["port"])
    m = re.search(r":(\d+)", p.get("url", ""))
    return int(m.group(1)) if m else None


def listeners():
    """port -> {pid}  (pid'ler sadece kendi kullanıcımızın süreçleri için görünür)"""
    res = {}
    for line in sh("ss", "-ltnpH").stdout.splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        try:
            pt = int(f[3].rsplit(":", 1)[1])
        except ValueError:
            continue
        res.setdefault(pt, set()).update(int(x) for x in re.findall(r"pid=(\d+)", line))
    return res


def procs():
    """Kullanıcının süreçleri: pid -> (cmdline, cwd)"""
    uid, me, out = os.getuid(), os.getpid(), {}
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == me:
            continue
        try:
            if os.stat(f"/proc/{d}").st_uid != uid:
                continue
            with open(f"/proc/{d}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="ignore").strip()
            cwd = os.readlink(f"/proc/{d}/cwd")
        except OSError:
            continue
        if cmd:
            out[int(d)] = (cmd, cwd)
    return out


def in_dir(cwd, d):
    d = os.path.realpath(os.path.expanduser(d or "~"))
    if d == os.path.realpath(os.path.expanduser("~")):
        return True  # klasör ~ ise klasör kısıtı yok
    return cwd == d or cwd.startswith(d + "/")


def external_pids(p, ps=None, lst=None):
    """DevPanel dışında (terminal, Claude Code…) çalışan, BU projeye ait süreçler."""
    ps = procs() if ps is None else ps
    lst = listeners() if lst is None else lst
    pt = port(p)
    if pt and not p.get("match"):  # port + klasör eşleşmesi
        return {x for x in lst.get(pt, ()) if x in ps and in_dir(ps[x][1], p.get("dir"))}
    needle = p.get("match") or p["cmd"]  # komut satırı + klasör eşleşmesi
    return {pid for pid, (cmd, cwd) in ps.items()
            if needle in cmd and "devpanel" not in cmd and not cmd.startswith("bash -lc")
            and in_dir(cwd, p.get("dir"))}


def states(projects):
    if not projects:
        return []
    st = sh("systemctl", "--user", "is-active", *[unit(p) for p in projects]).stdout.split()
    st += ["inactive"] * (len(projects) - len(st))
    ps = lst = None
    res = []
    for p, s in zip(projects, st):
        if s in ("active", "activating", "deactivating"):
            res.append(s)
            continue
        if ps is None:
            ps, lst = procs(), listeners()
        if external_pids(p, ps, lst):
            res.append("external")
        elif port(p) in lst:
            res.append("busy")  # port dolu ama başka klasördeki bir süreçte
        else:
            res.append(s)
    return res


def start(p):
    u, d = unit(p), os.path.expanduser(p.get("dir") or "~")
    script = f'[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh"; cd {shlex.quote(d)} && {p["cmd"]}'
    args = ["systemd-run", "--user", "--unit", u, "--description", p["name"], "--working-directory", d,
            "-p", f"TimeoutStopSec={p.get('stop_timeout', 5)}"]  # cloudflared gibi yavaş kapananları zorla
    for k, v in p.get("env", {}).items():
        args += ["--setenv", f"{k}={v}"]
    sh("systemctl", "--user", "reset-failed", u)
    r = sh(*args, "bash", "-lc", script)
    return r.returncode == 0, r.stderr.strip()


def stop(p):
    sh("systemctl", "--user", "stop", unit(p))
    sh("systemctl", "--user", "reset-failed", unit(p))
    pids = external_pids(p)  # sadece bu projeye ait dış süreçler; aynı portlu diğer projelere dokunma
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        for _ in range(30):
            pids = {x for x in pids if os.path.exists(f"/proc/{x}")}
            if not pids:
                return
            time.sleep(0.1)


def restart(p):
    stop(p)
    return start(p)


def log_text(p, n=300):
    out = sh("journalctl", "--user", "-u", unit(p), "-n", str(n), "--no-pager", "-o", "short").stdout
    return out.strip() or "Henüz log yok."


def notify_err(title, msg):
    subprocess.Popen(["notify-send", "-i", "dialog-error", title, msg])


def xdg_open(target):
    subprocess.Popen(["xdg-open", os.path.expanduser(target)])


def open_window():
    subprocess.Popen([sys.executable, SELF], start_new_session=True)


def ensure_tray():
    if sh("pgrep", "-f", f"{SELF} tray").returncode != 0:
        subprocess.Popen([sys.executable, SELF, "tray"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ───────────────────────── ana pencere (GTK4 / libadwaita) ─────────────────────────
def window_main():
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    GLib.set_prgname(APP_ID)

    def btn(icon, tip, cb, css=("flat",)):
        b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER, css_classes=list(css))
        b.connect("clicked", lambda *_: cb())
        return b

    class LogDialog(Adw.Dialog):
        def __init__(self, p):
            super().__init__(title=p["name"], content_width=780, content_height=540)
            self.p, self.last = p, None
            tv = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                              top_margin=10, bottom_margin=10, left_margin=14, right_margin=14)
            self.buf = tv.get_buffer()
            self.sw = Gtk.ScrolledWindow(child=tv, vexpand=True)
            hb = Adw.HeaderBar()
            hb.pack_start(btn("edit-copy-symbolic", "Kopyala", self.copy, ()))
            tb = Adw.ToolbarView(content=self.sw)
            tb.add_top_bar(hb)
            self.set_child(tb)
            self.load()
            self.timer = GLib.timeout_add_seconds(2, self.load)
            self.connect("closed", lambda *_: GLib.source_remove(self.timer))

        def load(self):
            txt = log_text(self.p)
            if txt != self.last:
                self.last = txt
                self.buf.set_text(txt)
                GLib.idle_add(self.to_end)
            return True

        def to_end(self):
            adj = self.sw.get_vadjustment()
            adj.set_value(adj.get_upper())
            return False

        def copy(self):
            self.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(self.last or ""))

    class EditDialog(Adw.Dialog):
        FIELDS = [("name", "Ad"), ("dir", "Klasör"), ("cmd", "Komut"),
                  ("url", "URL (opsiyonel)"), ("port", "Port (opsiyonel)")]

        def __init__(self, win, p=None):
            super().__init__(title="Projeyi düzenle" if p else "Yeni proje", content_width=480)
            self.win, self.orig = win, p
            p = p or {"dir": "~", "cmd": "npm run dev"}
            self.save_btn = Gtk.Button(label="Kaydet", css_classes=["suggested-action"])
            self.save_btn.connect("clicked", self.on_save)
            cancel = Gtk.Button(label="İptal")
            cancel.connect("clicked", lambda *_: self.close())
            hb = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
            hb.pack_start(cancel)
            hb.pack_end(self.save_btn)

            grp = Adw.PreferencesGroup()
            self.f = {}
            for key, title in self.FIELDS:
                r = Adw.EntryRow(title=title, text=str(p.get(key, "")))
                r.connect("changed", lambda *_: self.validate())
                grp.add(r)
                self.f[key] = r
            self.f["dir"].add_suffix(btn("folder-open-symbolic", "Klasör seç", self.pick_dir))
            page = Adw.PreferencesPage()
            page.add(grp)
            tb = Adw.ToolbarView(content=page)
            tb.add_top_bar(hb)
            self.set_child(tb)
            self.validate()

        def validate(self):
            self.save_btn.set_sensitive(bool(self.f["name"].get_text().strip() and self.f["cmd"].get_text().strip()))

        def pick_dir(self):
            Gtk.FileDialog(title="Proje klasörü").select_folder(self.win, None, self.picked)

        def picked(self, dlg, res):
            try:
                path = dlg.select_folder_finish(res).get_path()
            except GLib.Error:
                return
            home = os.path.expanduser("~")
            self.f["dir"].set_text("~" + path[len(home):] if path.startswith(home) else path)
            if not self.f["name"].get_text().strip():
                self.f["name"].set_text(os.path.basename(path))

        def on_save(self, *_):
            p = dict(self.orig or {})
            for key, _t in self.FIELDS:
                v = self.f[key].get_text().strip()
                if v:
                    p[key] = int(v) if key == "port" and v.isdigit() else v
                else:
                    p.pop(key, None)
            p.setdefault("dir", "~")
            ps = load()
            if self.orig and unit(self.orig) != unit(p):
                stop(self.orig)  # ad değiştiyse eski servis sahipsiz kalmasın
            if self.orig in ps:
                ps[ps.index(self.orig)] = p
            else:
                ps.append(p)
            save(ps)
            self.win.reload()
            self.close()

    class ProjectRow(Adw.ActionRow):
        def __init__(self, win, p):
            super().__init__(title=p["name"], use_markup=False)
            self.win, self.p, self.prev, self.sync, self.pending = win, p, None, False, False
            self.dot = Gtk.Label(label="●", css_classes=["dim-label"])
            self.add_prefix(self.dot)

            if p.get("url"):
                self.add_suffix(btn("web-browser-symbolic", "Tarayıcıda aç", lambda: xdg_open(p["url"])))
            self.add_suffix(btn("utilities-terminal-symbolic", "Loglar", lambda: LogDialog(p).present(win)))

            acts = Gio.SimpleActionGroup()
            def act(name, cb):
                a = Gio.SimpleAction.new(name, None)
                a.connect("activate", lambda *_: (cb(), GLib.timeout_add(400, win.refresh_once)))
                acts.add_action(a)
                return a
            self.a_restart = act("restart", lambda: self.run(lambda: restart(p)))
            act("folder", lambda: xdg_open(p.get("dir") or "~"))
            act("edit", lambda: EditDialog(win, p).present(win))
            act("delete", self.ask_delete)
            self.insert_action_group("row", acts)

            menu = Gio.Menu()
            menu.append("Yeniden başlat", "row.restart")
            menu.append("Klasörü aç", "row.folder")
            sec = Gio.Menu()
            sec.append("Düzenle", "row.edit")
            sec.append("Sil", "row.delete")
            menu.append_section(None, sec)
            self.add_suffix(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                                           valign=Gtk.Align.CENTER, css_classes=["flat"]))

            self.sw = Gtk.Switch(valign=Gtk.Align.CENTER)
            self.sw.connect("state-set", self.on_switch)
            self.add_suffix(self.sw)
            self.set_activatable_widget(self.sw)

        def on_switch(self, _sw, on):
            if not self.sync:
                self.run(lambda: start(self.p) if on else stop(self.p))
            return False

        def run(self, fn):
            self.pending = True
            self.sw.set_sensitive(False)
            self.set_subtitle("İşleniyor…")
            bg(fn, self.done)

        def done(self, r):
            self.pending = False
            self.sw.set_sensitive(True)
            if isinstance(r, tuple) and not r[0]:
                self.win.toast(f"{self.p['name']} başlatılamadı: {r[1]}")
            GLib.timeout_add(600, self.win.refresh_once)

        def update(self, s):
            if self.pending:
                return
            text, css, _ = STATE.get(s, OFF)
            if s == "busy":
                text = f"Port {port(self.p)} başka bir süreçte"
            self.dot.set_css_classes([css])
            self.set_subtitle(f"{text}  ·  {self.p.get('dir', '~')}  ·  {self.p['cmd']}")
            on = s in RUNNING
            if self.sw.get_active() != on:
                self.sync = True
                self.sw.set_active(on)
                self.sync = False
            self.a_restart.set_enabled(on)

        def ask_delete(self):
            d = Adw.AlertDialog(heading=f"“{self.p['name']}” silinsin mi?",
                                body="Çalışıyorsa önce durdurulur.")
            d.add_response("cancel", "İptal")
            d.add_response("delete", "Sil")
            d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            d.connect("response", self.on_delete)
            d.present(self.win)

        def on_delete(self, _d, resp):
            if resp != "delete":
                return
            stop(self.p)
            ps = load()
            if self.p in ps:
                ps.remove(self.p)
            save(ps)
            self.win.reload()

    class Win(Adw.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="DevPanel", default_width=680, default_height=560)
            self.title = Adw.WindowTitle(title="DevPanel")
            hb = Adw.HeaderBar(title_widget=self.title)
            hb.pack_start(btn("list-add-symbolic", "Proje ekle", self.add_project, ()))
            menu = Gio.Menu()
            menu.append("Tümünü durdur", "app.stop-all")
            menu.append("Ayar dosyasını aç", "app.open-config")
            menu.append("Hakkında", "app.about")
            hb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       primary=True, tooltip_text="Menü"))
            self.toasts = Adw.ToastOverlay()
            tb = Adw.ToolbarView(content=self.toasts)
            tb.add_top_bar(hb)
            self.set_content(tb)
            self.rows, self.mtime = [], 0
            self.reload()
            GLib.timeout_add_seconds(2, self.tick)

        def add_project(self):
            EditDialog(self).present(self)

        def reload(self):
            self.mtime = cfg_mtime()
            projects = load()
            self.rows = []
            if not projects:
                sp = Adw.StatusPage(icon_name="applications-development-symbolic", title="Henüz proje yok",
                                    description="npm run dev, cloudflared vb. komutlarını buraya ekle")
                b = Gtk.Button(label="Proje ekle", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
                b.connect("clicked", lambda *_: self.add_project())
                sp.set_child(b)
                self.toasts.set_child(sp)
                self.title.set_subtitle("")
                return
            grp = Adw.PreferencesGroup()
            for p in projects:
                r = ProjectRow(self, p)
                grp.add(r)
                self.rows.append(r)
            page = Adw.PreferencesPage()
            page.add(grp)
            self.toasts.set_child(page)
            self.refresh_once()

        def tick(self):
            if cfg_mtime() != self.mtime:  # dosya dışarıdan değiştiyse
                self.reload()
            else:
                self.refresh_once()
            return True

        def refresh_once(self):
            n = 0
            for r, s in zip(self.rows, states([r.p for r in self.rows])):
                if s == "failed" and r.prev not in (None, "failed"):
                    t = Adw.Toast(title=f"{r.p['name']} hata verdi", button_label="Loglar", timeout=8)
                    t.connect("button-clicked", lambda *_, p=r.p: LogDialog(p).present(self))
                    self.toasts.add_toast(t)
                r.prev = s
                r.update(s)
                n += s in RUNNING
            if self.rows:
                self.title.set_subtitle(f"{n} çalışıyor" if n else "Hepsi kapalı")
            return False

        def toast(self, msg):
            self.toasts.add_toast(Adw.Toast(title=msg, timeout=6))

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID)

        def do_startup(self):
            Adw.Application.do_startup(self)
            for name, cb in [("stop-all", self.stop_all), ("open-config", lambda: xdg_open(CFG)),
                             ("about", self.about), ("quit", self.quit),
                             ("close", lambda: self.props.active_window and self.props.active_window.close())]:
                a = Gio.SimpleAction.new(name, None)
                a.connect("activate", lambda *_, cb=cb: cb())
                self.add_action(a)
            self.set_accels_for_action("app.quit", ["<Ctrl>q"])
            self.set_accels_for_action("app.close", ["<Ctrl>w"])
            ensure_tray()

        def do_activate(self):
            (self.props.active_window or Win(self)).present()

        def stop_all(self):
            w = self.props.active_window
            bg(lambda: [stop(p) for p in load()], lambda _r: w and w.refresh_once())

        def about(self):
            Adw.AboutDialog(application_name="DevPanel", application_icon="applications-development",
                            version="1.0", developer_name="Abdullah · Serbay Interactive",
                            comments="Dev sunucularını ve tunnel'ları tek yerden yönet"
                            ).present(self.props.active_window)

    sys.exit(App().run([sys.argv[0]]))


# ───────────────────────── panel simgesi (GTK3 / AppIndicator) ─────────────────────────
def tray_main():
    gi.require_version("Gtk", "3.0")
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AI, GLib, Gtk

    class Tray:
        def __init__(self):
            self.ind = AI.Indicator.new("devpanel", "applications-development",
                                        AI.IndicatorCategory.APPLICATION_STATUS)
            self.ind.set_status(AI.IndicatorStatus.ACTIVE)
            self.ind.set_title("DevPanel")
            self.build()
            GLib.timeout_add_seconds(3, self.tick)

        @staticmethod
        def item(menu, label, cb):
            i = Gtk.MenuItem(label=label)
            i.connect("activate", lambda *_: cb())
            menu.append(i)
            return i

        def build(self):
            self.mtime = cfg_mtime()
            self.rows = []
            m = Gtk.Menu()
            self.item(m, "DevPanel'i aç", open_window)
            m.append(Gtk.SeparatorMenuItem())
            for p in load():
                it = Gtk.MenuItem(label=p["name"])
                sub = Gtk.Menu()
                b_on = self.item(sub, "Başlat", lambda p=p: bg(lambda: start(p), self.done))
                b_off = self.item(sub, "Durdur", lambda p=p: bg(lambda: stop(p), self.done))
                self.item(sub, "Yeniden başlat", lambda p=p: bg(lambda: restart(p), self.done))
                if p.get("url"):
                    self.item(sub, "Tarayıcıda aç", lambda p=p: xdg_open(p["url"]))
                it.set_submenu(sub)
                m.append(it)
                self.rows.append((p, it, b_on, b_off))
            m.append(Gtk.SeparatorMenuItem())
            self.item(m, "Tümünü durdur", lambda: bg(lambda: [stop(p) for p, *_ in self.rows], self.done))
            self.item(m, "Simgeyi kapat", Gtk.main_quit)
            m.show_all()
            self.ind.set_menu(m)
            self.refresh()

        def tick(self):
            self.build() if cfg_mtime() != self.mtime else self.refresh()
            return True

        def done(self, r):
            if isinstance(r, tuple) and not r[0]:
                notify_err("DevPanel", r[1])
            self.refresh()

        def refresh(self):
            n = 0
            for (p, it, b_on, b_off), s in zip(self.rows, states([r[0] for r in self.rows])):
                it.set_label(f"{STATE.get(s, OFF)[2]}  {p['name']}")
                on = s in RUNNING
                n += on
                b_on.set_sensitive(not on)
                b_off.set_sensitive(on)
            self.ind.set_icon_full("media-playback-start" if n else "applications-development", "DevPanel")
            self.ind.set_label(str(n) if n else "", "")

    Tray()
    Gtk.main()


# ───────────────────────── kurulum & CLI ─────────────────────────
def install():
    entry = ("[Desktop Entry]\nType=Application\nName={name}\nComment=Dev sunucuları ve tunnel yöneticisi\n"
             "Exec={py} {self}{arg}\nIcon=applications-development\nTerminal=false\n"
             "Categories=Development;\nStartupWMClass={id}\n{extra}")
    files = {
        "~/.local/share/applications/devpanel.desktop": dict(name="DevPanel", arg="", extra=""),
        "~/.config/autostart/devpanel-tray.desktop": dict(name="DevPanel Tray", arg=" tray",
                                                          extra="X-GNOME-Autostart-enabled=true\nNoDisplay=true\n"),
    }
    for path, v in files.items():
        path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(entry.format(py=sys.executable, self=SELF, id=APP_ID, **v))
        print("✓", path)
    ensure_tray()
    print("✓ Tray başlatıldı. Menüde 'DevPanel' olarak bulabilirsin.")


def find(name):
    n = name.lower()
    for p in load():
        if n in (slug(p), p["name"].lower()):
            return p
    sys.exit(f"Proje bulunamadı: {name}  (devpanel.py status ile listele)")


def cli(a):
    cmd = a[0]
    if cmd == "status":
        ps = load()
        for p, s in zip(ps, states(ps)):
            print(f"{s:<12} {slug(p):<22} port={port(p) or '-':<6} {p['name']}")
        return
    if cmd not in ("start", "stop", "restart", "logs") or len(a) < 2:
        sys.exit(__doc__)
    p = find(a[1])
    if cmd == "logs":
        print(log_text(p, a[2] if len(a) > 2 else 100))
        return
    if cmd == "stop":
        stop(p)
    else:
        ok, err = (start if cmd == "start" else restart)(p)
        if not ok:
            sys.exit(err)
    print(f"{cmd}: {p['name']} → {states([p])[0]}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "tray":
        tray_main()
    elif arg == "install":
        install()
    elif arg:
        cli(sys.argv[1:])
    else:
        window_main()
