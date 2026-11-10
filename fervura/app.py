"""Fervura: tray app + window that sends the CPU/GPU temperature to the display."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import math
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from . import __version__, datadir, settings, system, updates
from .protocol import Readings, build_frame
from .sensors import SensorHub, Snapshot

log = logging.getLogger(__name__)

DISPLAY_MAX = 99  # the device has two digits
MIN_INTERVAL = 0.1  # seconds; below this the send loop would just spin
SHUTDOWN_TIMEOUT = 3.0  # seconds to wait for the Sender to close the port and sensors
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3
READ_ERROR_STATUS = "erro ao ler sensores; veja o log.txt"
SAVE_ERROR_STATUS = "configuração não salva"
CPU_WARNING = "Temperatura da CPU indisponível. Veja o log.txt em %ProgramData%\\Fervura."
STORAGE_WARNING = ("A pasta %ProgramData%\\Fervura não pôde ser protegida: "
                   "a configuração não é salva e não há log.txt.")
OPEN_PAGE_ERROR = "não foi possível abrir o navegador; use Copiar link"


@dataclass
class Update:
    snapshot: Snapshot
    shown: int | None   # value sent to the display, None if nothing to send
    note: str           # why the shown value differs from the chosen source, if it does
    status: str
    connected: bool


def display_value(s: Snapshot, source: str) -> tuple[float | None, str]:
    cpu, gpu = s.cpu_temp, s.gpu_temp
    if source == "max":
        available = [t for t in (cpu, gpu) if t is not None]
        return (max(available) if available else None), ""
    wanted, other, other_name = (cpu, gpu, "GPU") if source == "cpu" else (gpu, cpu, "CPU")
    if wanted is not None:
        return wanted, ""
    if other is not None:
        return other, f"indisponível; mostrando a temperatura da {other_name}"
    return None, ""


class Sender(threading.Thread):
    """Reads sensors and pushes a frame to the display every `interval` seconds."""

    def __init__(self, hub: SensorHub, port: str | None, dry_run: bool, interval: float,
                 source: str, updates: queue.Queue):
        super().__init__(daemon=True)
        self.hub = hub
        self.interval = interval
        self.source = source
        self.updates = updates
        self._stop_event = threading.Event()
        self._last_error: tuple[type, str] | None = None  # full traceback only once per error
        if dry_run:
            self.display = None
        else:
            from .device import Display
            self.display = Display(port)

    def run(self) -> None:
        while not self._stop_event.is_set():
            self.step()
            self._stop_event.wait(self.interval)
        try:
            if self.display is not None:
                self.display.close()
        finally:
            if hasattr(self.hub, "close"):
                self.hub.close()

    def step(self) -> None:
        """One cycle; errors are logged (traceback once per repeated error) and never raised."""
        try:
            self.tick()
        except Exception as e:
            error = (type(e), str(e))
            if error != self._last_error:
                log.exception("send loop error")
            else:
                log.debug("send loop error repeated: %r", e)
            self._last_error = error
            return
        if self._last_error is not None:
            log.info("send loop recovered")
            self._last_error = None

    def tick(self) -> None:
        try:
            snap = self.hub.read()
        except Exception:
            # don't leave the window and tray showing the last value as connected
            self.updates.put(Update(Snapshot(), None, "", READ_ERROR_STATUS, False))
            raise
        value, note = display_value(snap, self.source)
        shown = None if value is None else max(0, min(round(value), DISPLAY_MAX))
        connected = False
        if shown is None:
            status = "sem temperatura para enviar"
        elif self.display is None:
            status = "simulação (nada é enviado)"
            log.info("frame %s", build_frame(Readings(cpu_temp=shown)).hex(" "))
        elif self.display.send(build_frame(Readings(cpu_temp=shown))):
            status, connected = f"conectado em {self.display.port}", True
        else:
            status = "display não encontrado, tentando de novo…"
        self.updates.put(Update(snap, shown, note, status, connected))

    def stop(self) -> None:
        self._stop_event.set()

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT) -> bool:
        """Stops and waits for the cleanup in run(). Gives up after `timeout` so a stuck
        thread (daemon) never blocks the exit. Returns True if the thread finished."""
        self.stop()
        if self.is_alive():
            self.join(timeout)
        if self.is_alive():
            log.warning("sender did not stop within %.0f s; exiting anyway", timeout)
            return False
        return True


def _fmt(value: float | None, unit: str) -> str:
    return "—" if value is None else f"{value:.0f} {unit}"


def warnings_text(s: Snapshot, storage_ready: bool) -> str:
    """Orange text under the system info: missing CPU temperature, unusable data folder."""
    lines = []
    if s.cpu_temp is None:
        lines.append(CPU_WARNING)
    if not storage_ready:
        lines.append(STORAGE_WARNING)
    return "\n".join(lines)


def update_text(release: updates.Release | None) -> str | None:
    """Line shown in the window and in the tray when a newer version exists."""
    return None if release is None else f"Nova versão {release.text} disponível"


def tray_tip(shown: int | None, status: str, release: updates.Release | None) -> str:
    """Tray tooltip: value, the new version notice (if any) and the status; 127 chars max."""
    lines = [f"Fervura: {'--' if shown is None else shown}", update_text(release), status]
    return "\n".join(line for line in lines if line)[:127]


class App:
    def __init__(self, root, sender: Sender, conf: dict,
                 checker: updates.UpdateChecker | None = None):
        import tkinter as tk
        from tkinter import ttk

        from .tray import Tray

        self.sender, self.conf, self.checker = sender, conf, checker
        self.save_problem = ""  # last error saving config.json, shown in the status line
        self.release: updates.Release | None = None  # newer version found, if any
        self.events: queue.Queue = queue.Queue()  # callables from the tray thread

        self.root = root
        root.title("Fervura")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        frm = ttk.Frame(root, padding=14)
        frm.grid(sticky="nsew")

        # --- what goes to the display
        top = ttk.LabelFrame(frm, text="Display", padding=10)
        top.grid(row=0, column=0, sticky="ew")
        self.shown_var = tk.StringVar(value="--")
        ttk.Label(top, textvariable=self.shown_var, font=("Segoe UI", 32, "bold"),
                  width=3, anchor="center").grid(row=0, column=0, rowspan=4, padx=(0, 14))
        self.source_var = tk.StringVar(value=conf["source"])
        for i, (key, label) in enumerate(settings.SOURCES.items()):
            ttk.Radiobutton(top, text=label, value=key, variable=self.source_var,
                            command=lambda: self.set_source(self.source_var.get())).grid(
                row=i, column=1, sticky="w")
        self.status_var = tk.StringVar(value="iniciando…")
        ttk.Label(top, textvariable=self.status_var, foreground="gray").grid(
            row=3, column=1, sticky="w", pady=(4, 0))

        # --- read-only system info
        info = ttk.LabelFrame(frm, text="Sistema", padding=10)
        info.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.info_vars: dict[str, tk.StringVar] = {}
        rows = [
            ("cpu_name", "CPU"), ("cpu_temp", "Temperatura"), ("cpu_usage", "Uso"),
            ("cpu_clock", "Clock"), (None, None),
            ("gpu_name", "GPU"), ("gpu_temp", "Temperatura"), ("gpu_usage", "Uso"),
            ("gpu_clock", "Clock"), ("gpu_mem", "Memória"), (None, None),
            ("ram_usage", "Uso de RAM"),
        ]
        for i, (key, label) in enumerate(rows):
            if key is None:
                ttk.Separator(info).grid(row=i, column=0, columnspan=2, sticky="ew", pady=4)
                continue
            bold = key.endswith("_name")
            ttk.Label(info, text=label, font=("Segoe UI", 9, "bold" if bold else "normal")).grid(
                row=i, column=0, sticky="w", padx=(0, 16))
            self.info_vars[key] = tk.StringVar(value="—")
            ttk.Label(info, textvariable=self.info_vars[key]).grid(row=i, column=1, sticky="w")
        self.warn_var = tk.StringVar(value="")
        ttk.Label(info, textvariable=self.warn_var, foreground="#b06000", wraplength=320).grid(
            row=len(rows), column=0, columnspan=2, sticky="w", pady=(6, 0))

        # --- options
        opts = ttk.Frame(frm)
        opts.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.autostart_var = tk.BooleanVar(value=settings.autostart_enabled())
        ttk.Checkbutton(opts, text="Iniciar com o Windows", variable=self.autostart_var,
                        command=self.toggle_autostart).grid(row=0, column=0, sticky="w")
        ttk.Button(opts, text="Sair", command=self.quit).grid(row=0, column=2)
        ttk.Button(opts, text="Ocultar", command=self.hide).grid(row=0, column=1, padx=6)
        self.check_updates_var = tk.BooleanVar(value=conf["check_updates"])
        ttk.Checkbutton(opts, text="Verificar atualizações", variable=self.check_updates_var,
                        command=lambda: self.set_check_updates(self.check_updates_var.get())).grid(
            row=1, column=0, sticky="w")
        # new version notice; hidden until the update check finds one
        self.update_frame = ttk.Frame(opts)
        self.update_frame.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.update_var = tk.StringVar(value="")
        ttk.Label(self.update_frame, textvariable=self.update_var, foreground="#1a5fb4",
                  wraplength=320).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Button(self.update_frame, text="Abrir página de download",
                   command=self.open_release_page).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(self.update_frame, text="Copiar link", command=self.copy_release_link).grid(
            row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        self.update_frame.grid_remove()
        opts.columnconfigure(0, weight=1)

        self.tray = Tray(
            on_open=lambda: self.events.put(self.show),
            on_quit=lambda: self.events.put(self.quit),
            get_source=lambda: self.conf["source"],
            set_source=lambda key: self.events.put(lambda: self.set_source(key)),
            get_update=lambda: update_text(self.release),
            on_update=lambda: self.events.put(self.open_release_page),
        )

    # -- actions (always on the Tk thread)
    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self) -> None:
        self.root.withdraw()

    def quit(self) -> None:
        self.sender.stop()
        if self.checker is not None:
            self.checker.stop()
        self.tray.stop()
        self.root.destroy()

    def save_conf(self) -> None:
        """Saves config.json; an error is logged and kept in the status line until a save works."""
        try:
            settings.save(self.conf)
        except OSError as e:
            log.warning("settings not saved: %s", e)
            self.save_problem = f"{SAVE_ERROR_STATUS}: {e}"
            self.status_var.set(self.save_problem)  # kept by render() until a save works
        else:
            self.save_problem = ""

    def set_source(self, key: str) -> None:
        """The new source applies to this session even if it cannot be saved."""
        self.conf["source"] = key
        self.source_var.set(key)
        self.sender.source = key
        self.save_conf()

    def set_check_updates(self, enabled: bool) -> None:
        """Turning it on checks right away; off, no request is made and the notice goes away.
        Applies to this session even if it cannot be saved."""
        self.conf["check_updates"] = enabled
        self.check_updates_var.set(enabled)
        if self.checker is not None:
            self.checker.set_enabled(enabled)
        if not enabled:
            self.show_release(None)
        self.save_conf()

    def show_release(self, release: updates.Release | None, note: str = "") -> None:
        """Shows (or hides, with None) the new version line under the options."""
        self.release = release
        if release is None:
            self.update_frame.grid_remove()
            return
        self.update_var.set(" — ".join(p for p in (update_text(release), note) if p))
        self.update_frame.grid()

    def open_release_page(self) -> None:
        """Opens the release page in the user's normal browser, never an elevated one
        (shellexec). If that fails, the window shows the line with "Copiar link"."""
        release = self.release
        if release is None:
            return
        from . import shellexec

        def done(error: str | None) -> None:  # runs on the opener thread
            if error is not None:
                self.events.put(lambda: self.release_page_failed(release, error))

        shellexec.open_url(release.url, done)

    def release_page_failed(self, release: updates.Release, error: str) -> None:
        log.warning("could not open %s: %s", release.url, error)
        if self.release == release:
            self.show_release(release, OPEN_PAGE_ERROR)
            self.show()

    def copy_release_link(self) -> None:
        if self.release is not None:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.release.url)

    def status_text(self, status: str, note: str) -> str:
        """Status line: send status, source note and, while it lasts, the save error."""
        return " — ".join(part for part in (status, note, self.save_problem) if part)

    def toggle_autostart(self) -> None:
        try:
            settings.set_autostart(self.autostart_var.get())
        except Exception as e:
            log.exception("autostart")
            self.status_var.set(f"não foi possível alterar a inicialização: {e}")
        self.autostart_var.set(settings.autostart_enabled())

    # -- periodic UI refresh
    def poll(self) -> None:
        while True:
            try:
                self.events.get_nowait()()
            except queue.Empty:
                break
        latest = None
        while True:
            try:
                latest = self.sender.updates.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self.render(latest)
        self.poll_updates()
        self.root.after(200, self.poll)

    def poll_updates(self) -> None:
        """Applies the newest result of the update check (ignored with the option off)."""
        found, release = False, None
        while self.checker is not None:
            try:
                release, found = self.checker.results.get_nowait(), True
            except queue.Empty:
                break
        if found and self.conf.get("check_updates") is True and release != self.release:
            self.show_release(release)

    def render(self, u: Update) -> None:
        s = u.snapshot
        self.shown_var.set("--" if u.shown is None else str(u.shown))
        self.status_var.set(self.status_text(u.status, u.note))
        v = self.info_vars
        v["cpu_name"].set(s.cpu_name or "—")
        v["cpu_temp"].set(_fmt(s.cpu_temp, "°C"))
        v["cpu_usage"].set(_fmt(s.cpu_usage, "%"))
        v["cpu_clock"].set(_fmt(s.cpu_clock, "MHz"))
        v["gpu_name"].set(s.gpu_name or "—")
        v["gpu_temp"].set(_fmt(s.gpu_temp, "°C"))
        v["gpu_usage"].set(_fmt(s.gpu_usage, "%"))
        v["gpu_clock"].set(_fmt(s.gpu_clock, "MHz"))
        v["gpu_mem"].set("—" if s.gpu_mem_used is None or s.gpu_mem_total is None
                         else f"{s.gpu_mem_used:.0f} / {s.gpu_mem_total:.0f} MB")
        v["ram_usage"].set(_fmt(s.ram_usage, "%"))
        self.warn_var.set(warnings_text(s, settings.storage_ready))
        self.tray.update(u.shown, u.connected, tray_tip(u.shown, u.status, self.release))

    def run(self) -> None:
        self.tray.start()
        self.sender.start()
        if self.checker is not None:
            self.checker.start()
        self.root.after(200, self.poll)
        self.root.mainloop()
        # the Sender is a daemon: wait briefly so it closes the serial port and the sensors
        self.sender.shutdown(SHUTDOWN_TIMEOUT)


def _setup_logging(verbose: bool, data_dir: Path | None) -> str | None:
    """log.txt (rotated) in the protected data folder, plus the console when there is one.
    Without the folder, or if log.txt or a rotated copy is a link or hard link, there is no
    file log (rotation renames and deletes those files). Returns why, in that case."""
    handlers: list[logging.Handler] = []
    problem = None
    if data_dir is not None:
        log_file = data_dir / "log.txt"
        files = [log_file] + [data_dir / f"log.txt.{i}" for i in range(1, LOG_BACKUPS + 1)]
        unsafe = [f for f in files if not datadir.is_safe_file(f)]
        if unsafe:
            problem = f"{unsafe[0]} is a link or hard link; no log file"
        else:
            try:
                handlers.append(logging.handlers.RotatingFileHandler(
                    log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"))
            except OSError as e:
                problem = f"could not open {log_file}: {e}"
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    if not handlers:
        handlers.append(logging.NullHandler())
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return problem


def _fatal(root, message: str) -> None:
    from tkinter import messagebox
    log.error(message)
    messagebox.showerror("Fervura", message, parent=root)
    root.destroy()
    sys.exit(1)


def _ensure_pawnio(root) -> None:
    """Same rule as the installer: no PawnIO, no program."""
    from tkinter import messagebox
    if system.pawnio_ok():
        log.info("PawnIO %s instalado", ".".join(map(str, system.pawnio_version())))
        return
    log.warning("PawnIO ausente ou desatualizado: %s", system.pawnio_version())
    if not messagebox.askyesno(
            "Fervura",
            "O driver PawnIO (software de terceiros, namazso - pawnio.eu) não está instalado "
            "ou está desatualizado.\n\nEle é necessário para ler a temperatura da CPU. "
            "Deseja instalar agora? O Windows pedirá permissão de administrador.",
            parent=root):
        _fatal(root, "Não é possível continuar sem o driver PawnIO.")
    try:
        ok = system.install_pawnio()
    except FileNotFoundError as e:
        _fatal(root, f"Instalador do PawnIO não encontrado: {e}\nReinstale o Fervura.")
    if not ok:
        _fatal(root, "O driver PawnIO não foi instalado. Não é possível continuar.")
    log.info("PawnIO instalado")


def _interval(text: str) -> float:
    """argparse type for --interval: a finite number of seconds >= MIN_INTERVAL."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"número inválido: {text!r}") from None
    if not math.isfinite(value) or value < MIN_INTERVAL:
        raise argparse.ArgumentTypeError(f"use um número de segundos maior ou igual a {MIN_INTERVAL}")
    return value


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fervura: envia a temperatura para o display serial de dois dígitos.")
    p.add_argument("--port", help="porta COM fixa (padrão: detectar pelo VID/PID)")
    p.add_argument("--interval", type=_interval, default=1.0,
                   help=f"segundos entre envios (padrão 1, mínimo {MIN_INTERVAL})")
    p.add_argument("--dry-run", action="store_true", help="não abre a serial; só registra os pacotes")
    p.add_argument("--minimized", action="store_true", help="inicia só com o ícone na bandeja")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    # Elevated program: its files go to a folder that normal processes cannot change.
    # Without it (e.g. not elevated yet, or a folder planted by someone else) nothing is
    # written to disk: log only on the console and default settings, not saved.
    storage_problem = datadir.prepare()
    settings.storage_ready = storage_problem is None
    log_problem = _setup_logging(args.verbose, None if storage_problem else datadir.DATA_DIR)
    log.info("Fervura %s iniciando; administrador=%s", __version__, system.is_admin())
    if storage_problem:
        log.warning("data folder not used (%s): no log file, settings not saved",
                    storage_problem)
    elif log_problem:
        log.warning("%s", log_problem)

    import tkinter as tk
    root = tk.Tk()
    root.withdraw()  # dialogs first; the window is built afterwards

    # CPU temperature needs administrator rights. The packaged .exe asks for them through
    # its manifest; running from source, ask now and hand over to the elevated copy.
    if not system.is_admin():
        if system.restart_as_admin(sys.argv[1:] if argv is None else argv):
            root.destroy()
            return
        _fatal(root, "O Fervura precisa de permissão de administrador para ler a "
                     "temperatura da CPU.")

    instance = system.SingleInstance()
    if instance.already_running:
        if not args.minimized:
            from tkinter import messagebox
            messagebox.showinfo("Fervura", "O Fervura já está em execução. "
                                "Use o ícone na bandeja do sistema.", parent=root)
        root.destroy()
        return

    _ensure_pawnio(root)
    try:
        hub = SensorHub(system.lhm_dir())
    except Exception as e:
        log.exception("sensores")
        _fatal(root, f"Não foi possível carregar a leitura de sensores:\n{e}")

    settings.migrate_legacy()
    conf = settings.load()
    sender = Sender(hub, args.port, args.dry_run, args.interval, conf["source"], queue.Queue())
    checker = updates.UpdateChecker(conf["check_updates"])  # no request while it is off
    app = App(root, sender, conf, checker)
    if not args.minimized:
        root.deiconify()
    app.run()
