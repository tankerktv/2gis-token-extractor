"""Окно — для тех, кому терминал барьер.

Сборку без Python берут не авторы скриптов, а люди, которым нужно один раз
скопировать токен и вставить его куда-то — например, в настройку интеграции
Home Assistant. Консоль таким людям отказывает вежливо, но всё-таки отказывает.

Окно — такая же тонкая обёртка, как ``cli.py``: всё, что оно умеет, делают уже
проверенные модули. Здесь только кнопки, надписи и то, без чего окну нельзя.

**Работа с браузером идёт в отдельном потоке.** Захват длится до минуты, вход —
пока человек вводит код из SMS. В потоке окна это значило бы замершее окно и
пометку Windows «не отвечает». Результат возвращается в поток окна через
очередь: tkinter не терпит, когда его трогают из чужих потоков.

**Потоки не фоновые (не daemon), и это нарочно.** Если закрыть окно посреди
захода, процесс не умрёт сразу, а тихо доработает: браузер закроется, замок
на сессию снимется. Фоновый поток умер бы вместе с окном и оставил бы замок —
и следующий запуск четверть часа отвечал бы, что сессия занята.

**Токен живёт только в памяти окна.** На экране он скрыт, пока не попросят
показать; в буфер обмена попадает только по кнопке — это и есть явная просьба.

tkinter из стандартной библиотеки: ноль новых зависимостей, работает в сборке
PyInstaller на всех трёх системах. Импортируется лениво, внутри функций, —
чтобы модуль грузился и там, где tkinter нет, и консоль за него не платила.
"""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from . import auth_api, browser
from .errors import BrowserMissing, WindowUnavailable
from .i18n import TEXTS, describe_error, detect_language
from .state import resolve_state_path

#: Чем закрывать токен на экране. Длина сохраняется — видно, что токен есть.
#: Узкая точка, а не круг ●: сорок кругов на экране со 125 % упирались в край
#: поля.
MASK = "•"

#: Пикселей на дюйм при масштабе 100 % — от этого числа считается увеличение.
BASE_DPI = 96

#: Как часто окно заглядывает в очередь готовых результатов.
POLL_MS = 100

Outcome = tuple[str, object]
Launch = Callable[[Callable[[], object], Callable[[str, object], None]], None]


def _call(task: Callable[[], object]) -> Outcome:
    """Выполняет работу и упаковывает итог — удачный или нет — в пару."""
    try:
        return "ok", task()
    except Exception as error:  # окно должно показать любую беду, а не молча умереть
        return "error", error


class ThreadLauncher:
    """Выполняет работу в отдельном потоке, а итог отдаёт в поток окна."""

    def __init__(self, root) -> None:
        self._root = root
        self._outcomes: queue.Queue = queue.Queue()
        self._root.after(POLL_MS, self._poll)

    def __call__(self, task, on_done) -> None:
        def work() -> None:
            self._outcomes.put((on_done, _call(task)))

        threading.Thread(target=work, daemon=False).start()

    def _poll(self) -> None:
        try:
            while True:
                on_done, (kind, payload) = self._outcomes.get_nowait()
                on_done(kind, payload)
        except queue.Empty:
            pass
        self._root.after(POLL_MS, self._poll)


def run_now(task, on_done) -> None:
    """Выполняет работу сразу, в том же потоке. Для тестов."""
    on_done(*_call(task))


class TokenWindow:
    """Четыре кнопки и строка состояния."""

    def __init__(
        self,
        root,
        *,
        language: str,
        state_path: Path,
        launch: Launch | None = None,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.language = language
        self.texts = TEXTS[language]
        self.state_path = state_path
        self.launch = launch or ThreadLauncher(root)
        self.token: str | None = None
        self.revealed = False
        self.busy = False

        texts = self.texts
        root.title(texts["title"])
        root.resizable(False, False)

        # Размеры ниже заданы для экрана со 100 %. Шрифты tkinter масштабирует
        # сам, а пиксели — нет: без поправки на 125 % текст рос бы, а отступы и
        # ширина переноса оставались прежними, и окно выглядело бы тесным.
        scale = max(1.0, root.winfo_fpixels("1i") / BASE_DPI)

        def px(value: float) -> int:
            return round(value * scale)

        frame = ttk.Frame(root, padding=px(16))
        frame.grid()
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text=texts["heading"], font=("", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )

        self.status = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, wraplength=px(380), justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(px(8), px(12))
        )

        self.sign_in_button = ttk.Button(frame, text=texts["button_sign_in"], command=self.sign_in)
        self.sign_in_button.grid(row=2, column=0, sticky="we")
        self.get_button = ttk.Button(frame, text=texts["button_get"], command=self.get_token)
        self.get_button.grid(row=2, column=1, sticky="we", padx=(px(8), 0))

        self.token_text = tk.StringVar(value=texts["token_empty"])
        self.token_entry = ttk.Entry(
            frame, textvariable=self.token_text, width=46, state="readonly"
        )
        self.token_entry.grid(row=3, column=0, columnspan=2, sticky="we", pady=(px(12), 0))

        self.show_button = ttk.Button(
            frame, text=texts["button_show"], command=self.toggle_reveal, state="disabled"
        )
        self.show_button.grid(row=4, column=0, sticky="w", pady=(px(6), 0))
        self.copy_button = ttk.Button(
            frame, text=texts["button_copy"], command=self.copy_token, state="disabled"
        )
        self.copy_button.grid(row=4, column=1, sticky="e", pady=(px(6), 0))

        # Появляется, только когда браузера нет: большинству людей эта кнопка
        # не нужна никогда, и маячить ей незачем.
        self.install_button = ttk.Button(
            frame, text=texts["button_install"], command=self.install_browser
        )
        self.install_button.grid(row=5, column=0, columnspan=2, sticky="we", pady=(px(12), 0))
        self.install_button.grid_remove()

        ttk.Label(frame, text=texts["note"], foreground="gray", wraplength=px(380)).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(px(14), 0)
        )

        self.refresh_status()

    # --- состояние -----------------------------------------------------------

    def refresh_status(self) -> None:
        key = "status_session" if self.state_path.exists() else "status_no_session"
        self.status.set(self.texts[key])

    def set_busy(self, busy: bool) -> None:
        """Пока идёт работа с браузером, кнопки не нажимаются.

        Второй заход поверх первого упёрся бы в замок на сессию, и человек
        получил бы непонятный отказ за то, что нажал кнопку дважды.
        """
        self.busy = busy
        work_state = "disabled" if busy else "normal"
        for button in (self.sign_in_button, self.get_button, self.install_button):
            button.configure(state=work_state)
        token_state = "normal" if self.token and not busy else "disabled"
        for button in (self.show_button, self.copy_button):
            button.configure(state=token_state)

    def install_button_visible(self) -> bool:
        return bool(self.install_button.grid_info())

    def _conceal(self) -> None:
        """Прячет показанный токен перед новой попыткой.

        Прежний токен при этом не выбрасывается: старые токены не гаснут, и
        его по-прежнему можно скопировать. Но держать его раскрытым всю минуту
        захода, да ещё рядом с сообщением об ошибке, незачем.
        """
        if self.revealed:
            self.revealed = False
            self._render_token()

    # --- действия ------------------------------------------------------------

    def sign_in(self) -> None:
        self._conceal()
        self.status.set(self.texts["status_signing_in"])
        self.set_busy(True)
        state_path = self.state_path

        def task():
            capture = asyncio.run(browser.interactive_login(state_path))
            return capture, auth_api.check_token(capture.token)

        self.launch(task, self._on_token)

    def get_token(self) -> None:
        self._conceal()
        self.status.set(self.texts["status_getting"])
        self.set_busy(True)
        state_path = self.state_path

        def task():
            capture = asyncio.run(browser.capture_token(state_path))
            return capture, auth_api.check_token(capture.token)

        self.launch(task, self._on_token)

    def install_browser(self) -> None:
        self.status.set(self.texts["status_installing"])
        self.set_busy(True)
        self.launch(browser.install_browser, self._on_installed)

    def toggle_reveal(self) -> None:
        self.revealed = not self.revealed
        self._render_token()

    def copy_token(self) -> None:
        if not self.token:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.token)
        # Без update() на части систем буфер пустеет, как только окно закрыто:
        # tkinter отдаёт содержимое лениво, по запросу, и после выхода отдавать
        # становится некому.
        self.root.update()
        self.status.set(self.texts["status_copied"])

    # --- итоги ---------------------------------------------------------------

    def _on_token(self, kind: str, payload: object) -> None:
        if kind == "error":
            self.show_error(payload)
            return

        capture, result = payload
        if result.expired:
            # Токен только что выдан и тут же отвергнут — такое бывает, только
            # если 2ГИС успел погасить сессию прямо во время захода.
            self.set_busy(False)
            self.status.set(self.texts["error_session_expired"])
            return

        self.token = capture.token
        self.revealed = False
        self._render_token()
        if result.alive and result.account:
            self.status.set(self.texts["status_got"].format(account=result.account))
        else:
            # Сеть до api.auth.2gis.com могла не ответить — токен от этого не хуже.
            self.status.set(self.texts["status_got_plain"])
        self.set_busy(False)

    def _on_installed(self, kind: str, payload: object) -> None:
        self.set_busy(False)
        if kind == "error":
            self.show_error(payload)
            return
        if payload == 0:
            self.install_button.grid_remove()
            self.status.set(self.texts["status_installed"])
        else:
            self.status.set(self.texts["error_install_failed"].format(code=payload))

    def show_error(self, error: object) -> None:
        self.set_busy(False)
        self.status.set(describe_error(error, self.language))
        if isinstance(error, BrowserMissing):
            self.install_button.grid()

    def _render_token(self) -> None:
        if not self.token:
            self.token_text.set(self.texts["token_empty"])
        elif self.revealed:
            self.token_text.set(self.token)
        else:
            self.token_text.set(MASK * len(self.token))
        self.show_button.configure(
            text=self.texts["button_hide" if self.revealed else "button_show"]
        )


def enable_crisp_text() -> None:
    """Объявляет Windows, что окно само справится с масштабом экрана.

    Без этого на экране со 125 % или 150 % Windows рисует окно в 100 % и
    растягивает картинку — текст мылится. Вызывать нужно до создания окна:
    объявление действует на весь процесс и задним числом не применяется.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # по монитору системы
        except (AttributeError, OSError):  # Windows старше 8.1
            ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):  # pragma: no cover — зависит от системы
        pass


def hide_own_console() -> None:
    """Прячет окно консоли, открытое ради этой программы.

    Вызывать можно **только при запуске кликом**: тогда консоль своя. При
    запуске из терминала это спрятало бы терминал самого человека.

    На Windows 11 с «Терминалом» по умолчанию окно консоли может и не
    спрятаться — оно принадлежит Терминалу, а не нам. Тогда оно просто
    останется за окном программы; мешать это не мешает.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            ctypes.windll.user32.ShowWindow(console, 0)  # SW_HIDE
    except (AttributeError, OSError):  # pragma: no cover — зависит от системы
        pass


def run(
    language: str | None = None,
    state_path: Path | None = None,
    *,
    hide_console: bool = False,
) -> int:
    """Открывает окно и ждёт, пока его закроют."""
    try:
        import tkinter as tk
    except ImportError as error:  # pragma: no cover — зависит от сборки
        raise WindowUnavailable(
            "this build has no window support (tkinter). Use the commands instead."
        ) from error

    enable_crisp_text()
    try:
        root = tk.Tk()
    except tk.TclError as error:  # нет экрана: SSH, контейнер, сервер
        raise WindowUnavailable(
            f"cannot open a window here ({error}). Use the commands instead."
        ) from error

    TokenWindow(
        root,
        language=detect_language(language),
        state_path=state_path or resolve_state_path(),
    )
    # Консоль прячется только после того, как окно действительно создано:
    # иначе при сбое человек остался бы и без окна, и без сообщения об ошибке.
    if hide_console:
        hide_own_console()
    root.mainloop()
    return 0
