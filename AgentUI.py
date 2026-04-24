"""
AgentUI.py — Windows GUI wrapper for Agent.py

A modern dark-mode desktop console that drives the agentic loop in Agent.py.

Features:
  - Multi-line prompt input (Ctrl+Enter to submit)
  - max_iterations slider (1–10)
  - Real-time, colour-coded log streaming (stdout captured via QueueWriter)
  - Save log to .txt via native Windows dialog
  - Background threading — UI never freezes during agent execution

Usage:
  python AgentUI.py
"""

import sys
import queue
import threading
import datetime
import importlib.util
import os

import customtkinter as ctk
from tkinter import filedialog, messagebox

# ── Load Agent.py as a module (does NOT run its __main__ block) ────────────────
_agent_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Agent.py")
_spec = importlib.util.spec_from_file_location("Agent", _agent_path)
_agent_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_agent_module)

run_agent = _agent_module.run_agent   # the function we'll call

# ── Inter-thread communication queues ─────────────────────────────────────────
log_queue    = queue.Queue()   # (text: str) chunks forwarded from stdout
result_queue = queue.Queue()   # sentinel: agent finished


# ── Stdout redirector ─────────────────────────────────────────────────────────
class QueueWriter:
    """
    Drop-in replacement for sys.stdout.
    Forwards every write() call into log_queue so the UI can display it live.
    """
    def __init__(self, q: queue.Queue):
        self._q = q

    def write(self, text: str):
        if text:
            self._q.put(text)

    def flush(self):
        pass   # required by the file-object protocol


# ── Log-line colour classifier ────────────────────────────────────────────────
def classify_tag(line: str) -> str:
    """Map a log line to a colour tag based on its content."""
    l = line.strip()
    if "Agent Answer:" in l:
        return "answer"
    if l.startswith("→ Calling tool") or l.startswith("→ Result") or l.startswith("→ Error"):
        return "tool"
    if l.startswith("LLM:"):
        return "llm"
    if l.startswith("---") or l.startswith("===") or l.startswith(">>>"):
        return "heading"
    if "error" in l.lower() or "failed" in l.lower() or "parse error" in l.lower():
        return "error"
    return "normal"


# ── Main Application ──────────────────────────────────────────────────────────
class AgentApp(ctk.CTk):

    # ── Init ──────────────────────────────────────────────────────────────────
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("AI Agent Console")
        self.geometry("960x720")
        self.minsize(720, 520)
        self.configure(fg_color="#0d1117")

        self._agent_running = False
        self._full_log: list[str] = []   # plain-text store for file export

        self._build_ui()
        self._configure_tags()
        self._poll_queue()   # start the 100 ms polling loop

    # ── UI Construction ───────────────────────────────────────────────────────
    def _build_ui(self):

        # ── Header ────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="#161b22", corner_radius=0, height=58)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="🤖  AI Agent Console",
            font=ctk.CTkFont(family="Segoe UI", size=19, weight="bold"),
            text_color="#e6edf3",
        ).pack(side="left", padx=22, pady=14)

        ctk.CTkLabel(
            header,
            text="Powered by Gemini",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#484f58",
        ).pack(side="right", padx=22)

        # ── Body (two-zone: input + log) ──────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="#0d1117")
        body.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        # ┌─ Input card ───────────────────────────────────────────────────┐
        input_card = ctk.CTkFrame(body, fg_color="#161b22", corner_radius=12)
        input_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        input_card.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            input_card,
            text="Prompt",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#7d8590",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 4))

        self.prompt_box = ctk.CTkTextbox(
            input_card,
            height=95,
            font=ctk.CTkFont(family="Consolas", size=13),
            fg_color="#0d1117",
            border_color="#30363d",
            border_width=1,
            text_color="#e6edf3",
            corner_radius=8,
        )
        self.prompt_box.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 10))
        self.prompt_box.insert(
            "0.0",
            "what is the stock price of GOOGLE, still in the yesterdays range? if yes, tell me a joke!",
        )
        # Ctrl+Enter submits
        self.prompt_box.bind("<Control-Return>", lambda e: self._run_agent())

        # Controls row (slider left, buttons right)
        ctrl = ctk.CTkFrame(input_card, fg_color="transparent")
        ctrl.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 14))

        # ── Slider group
        slider_grp = ctk.CTkFrame(ctrl, fg_color="transparent")
        slider_grp.pack(side="left")

        ctk.CTkLabel(
            slider_grp,
            text="Max Iterations:",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#7d8590",
        ).pack(side="left", padx=(0, 6))

        self.iter_val_label = ctk.CTkLabel(
            slider_grp,
            text="5",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#58a6ff",
            width=22,
        )
        self.iter_val_label.pack(side="left")

        self.iter_var = ctk.IntVar(value=5)
        ctk.CTkSlider(
            slider_grp,
            from_=1,
            to=10,
            number_of_steps=9,
            variable=self.iter_var,
            width=170,
            progress_color="#1f6feb",
            button_color="#58a6ff",
            button_hover_color="#79c0ff",
            command=lambda v: self.iter_val_label.configure(text=str(int(v))),
        ).pack(side="left", padx=(8, 0))

        # ── Buttons
        self.clear_btn = ctk.CTkButton(
            ctrl,
            text="✕  Clear",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color="#161b22",
            border_color="#30363d",
            border_width=1,
            hover_color="#21262d",
            text_color="#7d8590",
            corner_radius=8,
            width=90,
            command=self._clear_log,
        )
        self.clear_btn.pack(side="right", padx=(8, 0))

        self.run_btn = ctk.CTkButton(
            ctrl,
            text="▶  Run Agent",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color="#1f6feb",
            hover_color="#388bfd",
            corner_radius=8,
            width=140,
            command=self._run_agent,
        )
        self.run_btn.pack(side="right")

        # ┌─ Log card ─────────────────────────────────────────────────────┐
        log_card = ctk.CTkFrame(body, fg_color="#161b22", corner_radius=12)
        log_card.grid(row=1, column=0, sticky="nsew")
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(1, weight=1)

        log_hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        log_hdr.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 6))

        ctk.CTkLabel(
            log_hdr,
            text="Agent Log",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#7d8590",
        ).pack(side="left")

        # Legend chips — bg colors are dark tinted versions of each accent
        legend = ctk.CTkFrame(log_hdr, fg_color="transparent")
        legend.pack(side="left", padx=20)
        for label, fg_color, bg_color in [
            ("LLM",    "#58a6ff", "#0d2040"),
            ("Tool",   "#d29922", "#2b1f00"),
            ("Answer", "#3fb950", "#0b2a12"),
            ("Error",  "#f85149", "#2d0b09"),
        ]:
            chip = ctk.CTkFrame(legend, fg_color=bg_color, corner_radius=4)
            chip.pack(side="left", padx=3)
            ctk.CTkLabel(
                chip,
                text=label,
                font=ctk.CTkFont(family="Segoe UI", size=10),
                text_color=fg_color,
            ).pack(padx=6, pady=2)

        self.save_btn = ctk.CTkButton(
            log_hdr,
            text="💾  Save Log",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color="#161b22",
            border_color="#30363d",
            border_width=1,
            hover_color="#21262d",
            text_color="#7d8590",
            corner_radius=8,
            width=110,
            command=self._save_log,
        )
        self.save_btn.pack(side="right")

        self.log_box = ctk.CTkTextbox(
            log_card,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color="#0d1117",
            border_color="#30363d",
            border_width=1,
            text_color="#c9d1d9",
            corner_radius=8,
            wrap="word",
            state="disabled",
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))

        # ── Status bar ────────────────────────────────────────────────────
        status_bar = ctk.CTkFrame(self, fg_color="#161b22", corner_radius=0, height=32)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)

        self.status_dot = ctk.CTkLabel(
            status_bar,
            text="●",
            font=ctk.CTkFont(size=12),
            text_color="#3fb950",
        )
        self.status_dot.pack(side="left", padx=(18, 4), pady=8)

        self.status_label = ctk.CTkLabel(
            status_bar,
            text="Ready",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#484f58",
        )
        self.status_label.pack(side="left")

        ctk.CTkLabel(
            status_bar,
            text="Ctrl+Enter to run",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#30363d",
        ).pack(side="right", padx=18)

    # ── Colour tags on the underlying tk.Text widget ──────────────────────────
    def _configure_tags(self):
        tb = self.log_box._textbox
        tb.tag_config("answer",  foreground="#3fb950", font=("Consolas", 12, "bold"))
        tb.tag_config("tool",    foreground="#d29922")
        tb.tag_config("llm",     foreground="#58a6ff")
        tb.tag_config("heading", foreground="#bc8cff", font=("Consolas", 12, "bold"))
        tb.tag_config("error",   foreground="#f85149")
        tb.tag_config("normal",  foreground="#c9d1d9")

    # ── Agent runner ──────────────────────────────────────────────────────────
    def _run_agent(self):
        if self._agent_running:
            return

        query = self.prompt_box.get("0.0", "end").strip()
        if not query:
            messagebox.showwarning("Empty Prompt", "Please enter a prompt before running.")
            return

        self._agent_running = True
        self._set_status("running")
        self.run_btn.configure(state="disabled", text="⏳  Running…")

        max_iter = int(self.iter_var.get())

        def _worker():
            orig_stdout = sys.stdout
            sys.stdout = QueueWriter(log_queue)
            try:
                answer = run_agent(query, max_iterations=max_iter)
                result_queue.put(answer)
            except Exception as exc:
                log_queue.put(f"\n[ERROR] {exc}\n")
                result_queue.put(None)
            finally:
                sys.stdout = orig_stdout

        threading.Thread(target=_worker, daemon=True).start()

    # ── 100 ms queue polling loop ─────────────────────────────────────────────
    def _poll_queue(self):
        while not log_queue.empty():
            text = log_queue.get_nowait()
            self._append_log(text)

        if not result_queue.empty():
            result_queue.get_nowait()   # consume sentinel
            self._agent_running = False
            self._set_status("done")
            self.run_btn.configure(state="normal", text="▶  Run Agent")

        self.after(100, self._poll_queue)

    # ── Log helpers ───────────────────────────────────────────────────────────
    def _append_log(self, text: str):
        """Insert text into the log box with per-line colour tagging."""
        self._full_log.append(text)
        lines = text.split("\n")
        self.log_box.configure(state="normal")
        tb = self.log_box._textbox
        for i, line in enumerate(lines):
            if i > 0:
                tb.insert("end", "\n", "normal")
            if line:
                tb.insert("end", line, classify_tag(line))
        self.log_box.configure(state="disabled")
        self.log_box._textbox.see("end")   # auto-scroll

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("0.0", "end")
        self.log_box.configure(state="disabled")
        self._full_log.clear()
        self._set_status("ready")

    def _save_log(self):
        if not self._full_log:
            messagebox.showinfo("Nothing to Save", "The log is empty — run the agent first.")
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=f"agent_log_{ts}.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Save Agent Log",
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("".join(self._full_log))
            messagebox.showinfo("Saved", f"Log saved to:\n{path}")

    # ── Status bar helpers ────────────────────────────────────────────────────
    def _set_status(self, state: str):
        if state == "running":
            self.status_dot.configure(text_color="#d29922")
            self.status_label.configure(text="Agent is thinking…")
        elif state == "done":
            self.status_dot.configure(text_color="#3fb950")
            self.status_label.configure(text="Done  ✓")
        else:   # ready
            self.status_dot.configure(text_color="#3fb950")
            self.status_label.configure(text="Ready")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = AgentApp()
    app.mainloop()
