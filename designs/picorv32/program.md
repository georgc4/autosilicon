You are optimizing a PicoRV32 RISC-V CPU (Verilog, Sky130).
The harness evaluates your changes — do NOT run make, lint, test, or synth yourself.

Make ONE focused RTL change to reduce area (µm²) and/or improve fmax (MHz) while keeping tests passing.
Your change is kept if it's Pareto-improving on area (minimize) × fmax (maximize).
Only edit .v files in rtl/. Do NOT touch tb/, synth/, or Makefile.

After your edit, write a one-line summary of what you changed and why to the file rtl/.change_summary (overwrite it each time).

Focus on the main `picorv32` core and `picorv32_pcpi_*` coprocessors.
The `picorv32_axi`, `picorv32_axi_adapter`, and `picorv32_wb` wrappers are secondary.
Do NOT change the top-level module port interface.
