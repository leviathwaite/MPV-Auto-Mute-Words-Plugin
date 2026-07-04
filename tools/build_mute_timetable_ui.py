#!/usr/bin/env python3
"""
Tkinter front end for build_mute_timetable.py.

Lets users pick a media file, optionally swap the bad-words list, and run the
existing WhisperX timetable builder without using the command line directly.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError as exc:  # pragma: no cover - depends on system package
    tk = None
    filedialog = messagebox = ttk = None
    TK_IMPORT_ERROR = exc
else:
    TK_IMPORT_ERROR = None

from build_mute_timetable import DEFAULT_BADWORDS, VALID_MODELS

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_SCRIPT = Path(__file__).resolve().with_name("build_mute_timetable.py")
COMPUTE_TYPES = ("int8", "float16", "float32")
DEVICE_OPTIONS = ("auto", "cpu", "cuda")


class TimetableUi:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MPV Auto-Mute Timetable Builder")
        self.root.minsize(760, 420)

        self.media_var = tk.StringVar()
        self.badwords_var = tk.StringVar(value=str(DEFAULT_BADWORDS.resolve()))
        self.output_var = tk.StringVar()
        self.model_var = tk.StringVar(value="base")
        self.device_var = tk.StringVar(value="auto")
        self.compute_var = tk.StringVar(value="int8")
        self.language_var = tk.StringVar()
        self.batch_size_var = tk.StringVar(value="16")
        self.merge_gap_var = tk.StringVar(value="0.1")
        self.status_var = tk.StringVar(value="Select a media file to begin.")

        self.process: subprocess.Popen[str] | None = None

        self.media_var.trace_add("write", self._sync_default_output)
        self._build_layout()

    def _build_layout(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(8, weight=1)

        self._add_path_row(main, 0, "Media file", self.media_var, self.pick_media_file)
        self._add_path_row(main, 1, "Bad-words list", self.badwords_var, self.pick_badwords_file)
        self._add_path_row(main, 2, "Output JSON", self.output_var, self.pick_output_file)

        ttk.Label(main, text="Whisper model").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            main,
            textvariable=self.model_var,
            values=VALID_MODELS,
            state="readonly",
        ).grid(row=3, column=1, sticky="ew", pady=(8, 0))

        options = ttk.Frame(main)
        options.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        for idx in range(4):
            options.columnconfigure(idx, weight=1)

        self._add_option_field(options, 0, "Device", self.device_var, DEVICE_OPTIONS)
        self._add_option_field(options, 1, "Compute type", self.compute_var, COMPUTE_TYPES)
        self._add_entry_field(options, 2, "Language", self.language_var)
        self._add_entry_field(options, 3, "Batch size", self.batch_size_var)

        advanced = ttk.Frame(main)
        advanced.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        advanced.columnconfigure(1, weight=1)
        ttk.Label(advanced, text="Merge gap (sec)").grid(row=0, column=0, sticky="w")
        ttk.Entry(advanced, textvariable=self.merge_gap_var).grid(row=0, column=1, sticky="ew")

        button_bar = ttk.Frame(main)
        button_bar.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.run_button = ttk.Button(button_bar, text="Generate timetable", command=self.run_builder)
        self.run_button.pack(side="left")
        ttk.Button(button_bar, text="Reset default bad-words list", command=self.use_default_badwords).pack(side="left", padx=(8, 0))

        ttk.Label(main, textvariable=self.status_var).grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 4))

        self.log = tk.Text(main, height=12, wrap="word", state="disabled")
        self.log.grid(row=8, column=0, columnspan=3, sticky="nsew")

    def _add_path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: callable,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=(0, 8))
        ttk.Button(parent, text="Browse…", command=command).grid(row=row, column=2, padx=(8, 0), pady=(0, 8))

    def _add_option_field(
        self,
        parent: ttk.Frame,
        column: int,
        label: str,
        variable: tk.StringVar,
        values: tuple[str, ...] | list[str],
    ) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=column, sticky="ew", padx=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=variable, values=values, state="readonly").grid(row=1, column=0, sticky="ew")

    def _add_entry_field(
        self,
        parent: ttk.Frame,
        column: int,
        label: str,
        variable: tk.StringVar,
    ) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=column, sticky="ew", padx=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=variable).grid(row=1, column=0, sticky="ew")

    def _sync_default_output(self, *_args: object) -> None:
        media = Path(self.media_var.get()).expanduser()
        if not self.media_var.get().strip() or not media.suffix:
            return
        self.output_var.set(str(media.with_suffix(".mute.json")))

    def pick_media_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose media file",
            initialdir=str(Path.home()),
        )
        if path:
            self.media_var.set(path)

    def pick_badwords_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose bad-words list",
            initialdir=str(DEFAULT_BADWORDS.resolve().parent),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.badwords_var.set(path)

    def pick_output_file(self) -> None:
        default_name = Path(self.output_var.get()).name if self.output_var.get() else "output.mute.json"
        path = filedialog.asksaveasfilename(
            title="Choose output JSON",
            initialdir=str(Path(self.media_var.get()).expanduser().parent if self.media_var.get() else Path.home()),
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("Mute timetable JSON", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def use_default_badwords(self) -> None:
        self.badwords_var.set(str(DEFAULT_BADWORDS.resolve()))

    def append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def validate(self) -> bool:
        if self.process is not None:
            messagebox.showinfo("Already running", "A timetable build is already in progress.")
            return False

        media = Path(self.media_var.get()).expanduser()
        if not self.media_var.get().strip():
            messagebox.showerror("Missing media file", "Choose a media file first.")
            return False
        if not media.exists():
            messagebox.showerror("Media not found", f"Media file not found:\n{media}")
            return False

        badwords = Path(self.badwords_var.get()).expanduser()
        if not self.badwords_var.get().strip():
            self.use_default_badwords()
            badwords = Path(self.badwords_var.get())
        if not badwords.exists():
            messagebox.showerror("Bad-words file not found", f"Bad-words file not found:\n{badwords}")
            return False

        if not self.output_var.get().strip():
            self.output_var.set(str(media.with_suffix(".mute.json")))

        try:
            batch_size = int(self.batch_size_var.get())
            if batch_size <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid batch size", "Batch size must be a positive integer.")
            return False

        try:
            merge_gap = float(self.merge_gap_var.get())
            if merge_gap < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid merge gap", "Merge gap must be zero or greater.")
            return False

        return True

    def build_command(self) -> list[str]:
        command = [
            sys.executable,
            str(CLI_SCRIPT),
            str(Path(self.media_var.get()).expanduser()),
            "--badwords",
            str(Path(self.badwords_var.get()).expanduser()),
            "--output",
            str(Path(self.output_var.get()).expanduser()),
            "--model",
            self.model_var.get(),
            "--compute-type",
            self.compute_var.get(),
            "--batch-size",
            self.batch_size_var.get(),
            "--merge-gap",
            self.merge_gap_var.get(),
        ]

        if self.device_var.get() != "auto":
            command.extend(["--device", self.device_var.get()])
        if self.language_var.get().strip():
            command.extend(["--language", self.language_var.get().strip()])
        return command

    def run_builder(self) -> None:
        if not self.validate():
            return

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        command = self.build_command()
        self.status_var.set("Generating mute timetable…")
        self.run_button.configure(state="disabled")
        self.append_log(f"$ {' '.join(command)}\n\n")

        thread = threading.Thread(target=self._run_process, args=(command,), daemon=True)
        thread.start()

    def _run_process(self, command: list[str]) -> None:
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.root.after(0, self.append_log, line)

            returncode = self.process.wait()
        except Exception as exc:  # pragma: no cover - UI error path
            self.root.after(0, self._finish_run, False, f"Failed to launch builder:\n{exc}")
            return
        finally:
            self.process = None

        if returncode == 0:
            self.root.after(0, self._finish_run, True, f"Finished.\nOutput: {self.output_var.get()}")
        else:
            self.root.after(0, self._finish_run, False, "Builder failed. See log output above.")

    def _finish_run(self, success: bool, message: str) -> None:
        self.run_button.configure(state="normal")
        self.status_var.set(message)
        if success:
            messagebox.showinfo("Mute timetable created", message)
        else:
            messagebox.showerror("Mute timetable failed", message)


def main() -> None:
    if TK_IMPORT_ERROR is not None:
        sys.exit(
            "Tkinter is not available in this Python environment.\n"
            "Install the platform's Tk package, then re-run this tool."
        )
    root = tk.Tk()
    ttk.Style(root)
    TimetableUi(root)
    root.mainloop()


if __name__ == "__main__":
    main()
