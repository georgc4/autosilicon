You are optimizing an FOC motor control coprocessor (SystemVerilog, Sky130).
The harness evaluates your changes — do NOT run make, lint, test, or synth yourself.

Make ONE focused RTL change to reduce gate count while keeping tests passing.
Only edit .sv files in rtl/. Do NOT touch tb/, synth/, openlane/, or Makefile.

After your edit, write a one-line summary of what you changed and why.

DO NOT remove or merge FSM states (like S_DONE) — the pipeline timing and
handshake signals depend on exact cycle counts. Tests verify latency.
