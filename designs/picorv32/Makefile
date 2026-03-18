# AutoSilicon — PicoRV32 RISC-V CPU Benchmark Makefile
#
# Targets:
#   make lint         — Verilator lint
#   make test         — Run testbench_ez via Icarus Verilog
#   make synth-one    — Single-config Yosys synthesis (Sky130)
#   make clean        — Clean build artifacts

.PHONY: all lint test synth-one clean

# ── Paths ─────────────────────────────────────────────────────
DESIGN_DIR := $(shell pwd)
PROJ_ROOT  := $(DESIGN_DIR)/../..
INFRA_DIR  := $(PROJ_ROOT)/infra

RTL_DIR    := $(DESIGN_DIR)/rtl
TB_DIR     := $(DESIGN_DIR)/tb

RTL_FILES  := $(RTL_DIR)/picorv32.v

# Python (prefer venv)
VENV_PYTHON := $(shell test -x $(PROJ_ROOT)/.venv/bin/python3 && echo $(PROJ_ROOT)/.venv/bin/python3)
PYTHON := $(or $(PYTHON),$(if $(VENV_PYTHON),$(VENV_PYTHON),python3))

# Icarus Verilog
IVERILOG := iverilog
VVP := vvp

# Design config
LINT_TOP := picorv32

all: lint

# ── RTL Linting ────────────────────────────────────────────────
lint:
	@echo "Running Verilator lint on PicoRV32..."
	verilator --lint-only -Wall \
		-Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND \
		-Wno-CASEINCOMPLETE -Wno-UNOPTFLAT -Wno-BLKANDNBLK \
		-Wno-BLKSEQ -Wno-DECLFILENAME -Wno-GENUNNAMED \
		-Wno-INITIALDLY -Wno-WIDTHCONCAT \
		--top-module $(LINT_TOP) \
		$(RTL_FILES)
	@echo "PicoRV32 lint passed."

# ── Test (Icarus Verilog + testbench_ez) ──────────────────────
# testbench_ez.v is self-contained: runs a simple load/store/add loop.
# The test wrapper checks that the trap signal doesn't fire unexpectedly
# and that the program counter advances.
$(TB_DIR)/testbench_ez.vvp: $(TB_DIR)/testbench_ez.v $(RTL_FILES)
	$(IVERILOG) -o $@ $^

test: $(TB_DIR)/testbench_ez.vvp
	@echo "Running PicoRV32 testbench_ez..."
	cd $(TB_DIR) && $(VVP) -N testbench_ez.vvp > test_output.log 2>&1
	@# Check simulation completed (look for normal $finish)
	@if grep -q "write " $(TB_DIR)/test_output.log; then \
		echo "PicoRV32 testbench_ez: PASS (program executed store instructions)"; \
	else \
		echo "PicoRV32 testbench_ez: FAIL (no memory writes observed)"; \
		exit 1; \
	fi
	@# Generate minimal results.xml for harness compatibility
	@echo '<?xml version="1.0" encoding="utf-8"?>' > $(TB_DIR)/results.xml
	@echo '<testsuites><testsuite name="picorv32_ez" tests="1">' >> $(TB_DIR)/results.xml
	@echo '  <testcase name="test_basic_execution" classname="picorv32"/>' >> $(TB_DIR)/results.xml
	@echo '</testsuite></testsuites>' >> $(TB_DIR)/results.xml

# ── Synthesis ─────────────────────────────────────────────────
synth-one:
	@echo "Running single-config Yosys synthesis (PicoRV32)..."
	$(PYTHON) $(INFRA_DIR)/synth_sweep.py --design-dir $(DESIGN_DIR) --single

# ── Cleanup ────────────────────────────────────────────────────
clean:
	rm -rf obj_dir/ sim_build/ __pycache__/
	rm -rf synth/results/*.ys synth/results/*.json synth/results/*.v synth/results/*.log synth/results/*.csv
	rm -f $(TB_DIR)/testbench_ez.vvp $(TB_DIR)/test_output.log $(TB_DIR)/results.xml
	find . -name "*.vcd" -delete 2>/dev/null || true
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
