# AutoSilicon — Top-Level Makefile
#
# Delegates to design-specific Makefiles under designs/<name>/
# Currently supported designs: foc
#
# Usage:
#   make toolcheck        — Verify all required tools
#   make foc-lint         — Lint FOC RTL
#   make foc-test         — Run FOC cocotb tests
#   make foc-synth-one    — Single-config Yosys synthesis
#   make foc-sweep-quick  — Quick sweep (~6 configs)
#   make foc-sweep-medium — Medium sweep (~50 configs)
#   make foc-pnr          — OpenLane2 PnR flow
#   make clean            — Clean all build artifacts

.PHONY: all toolcheck clean
.PHONY: foc-lint foc-test foc-golden foc-synth-one foc-sweep-quick foc-sweep-medium foc-pnr foc-pnr-check foc-clean

# Use venv Python if available, else system Python
VENV_PYTHON := $(shell test -x .venv/bin/python3 && echo .venv/bin/python3)
PYTHON := $(if $(VENV_PYTHON),$(VENV_PYTHON),python3)
export PYTHON

all: foc-lint

# ── Tool verification ──────────────────────────────────────────
toolcheck:
	@echo "AutoSilicon — Toolchain Check"
	@echo "=============================="
	@printf "  %-20s " "yosys:"; \
		(yosys --version 2>/dev/null | head -1 && echo "") || echo "NOT FOUND"
	@printf "  %-20s " "verilator:"; \
		verilator --version 2>/dev/null | head -1 || echo "NOT FOUND"
	@printf "  %-20s " "iverilog:"; \
		iverilog -V 2>/dev/null | head -1 || echo "NOT FOUND"
	@printf "  %-20s " "surfer:"; \
		surfer --version 2>/dev/null || echo "NOT FOUND"
	@printf "  %-20s " "python3:"; \
		python3 --version 2>/dev/null || echo "NOT FOUND"
	@printf "  %-20s " "cocotb:"; \
		$(PYTHON) -c "import cocotb; print(cocotb.__version__)" 2>/dev/null || echo "NOT FOUND"
	@printf "  %-20s " "numpy:"; \
		$(PYTHON) -c "import numpy; print(numpy.__version__)" 2>/dev/null || echo "NOT FOUND"
	@printf "  %-20s " "pyyaml:"; \
		$(PYTHON) -c "import yaml; print(yaml.__version__)" 2>/dev/null || echo "NOT FOUND"
	@printf "  %-20s " "venv:"; \
		echo "$(PYTHON)"

# ── FOC Design Targets (delegate to designs/foc/Makefile) ─────
foc-lint:
	$(MAKE) -C designs/foc lint

foc-test:
	$(MAKE) -C designs/foc test

foc-golden:
	$(MAKE) -C designs/foc golden

foc-synth-one:
	$(MAKE) -C designs/foc synth-one

foc-sweep-quick:
	$(MAKE) -C designs/foc sweep-quick

foc-sweep-medium:
	$(MAKE) -C designs/foc sweep-medium

foc-pnr-check:
	$(MAKE) -C designs/foc pnr-check

foc-pnr:
	$(MAKE) -C designs/foc pnr

foc-clean:
	$(MAKE) -C designs/foc clean

# ── Global Cleanup ────────────────────────────────────────────
clean:
	$(MAKE) -C designs/foc clean
	rm -rf __pycache__/
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
