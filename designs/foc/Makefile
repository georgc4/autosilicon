# AutoSilicon — FOC Motor Coprocessor Design Makefile
#
# Targets:
#   make lint         — Verilator lint on all RTL
#   make test         — Run cocotb testbenches
#   make golden       — Run golden model sanity check
#   make synth-one    — Single-config Yosys synthesis
#   make sweep-quick  — Quick synthesis sweep (~6 configs)
#   make sweep-medium — Medium synthesis sweep (~50 configs)
#   make pnr          — OpenLane2 PnR flow
#   make clean        — Clean build artifacts

.PHONY: all lint lint-strict test golden synth-one sweep-quick sweep-medium
.PHONY: pnr pnr-check prep-rtl clean
.PHONY: test-cordic test-clarke test-park test-pi test-svpwm test-foc-top

# ── Paths ─────────────────────────────────────────────────────
DESIGN_DIR := $(shell pwd)
PROJ_ROOT  := $(DESIGN_DIR)/../..
INFRA_DIR  := $(PROJ_ROOT)/infra
RTL_DIR    := $(DESIGN_DIR)/rtl
MODEL_DIR  := $(DESIGN_DIR)/model

# Package must be first for Verilator file-order dependency resolution
RTL_FILES := $(RTL_DIR)/foc_pkg.sv $(filter-out $(RTL_DIR)/foc_pkg.sv,$(wildcard $(RTL_DIR)/*.sv))

# Python (prefer venv)
VENV_PYTHON := $(shell test -x $(PROJ_ROOT)/.venv/bin/python3 && echo $(PROJ_ROOT)/.venv/bin/python3)
PYTHON := $(or $(PYTHON),$(if $(VENV_PYTHON),$(VENV_PYTHON),python3))

# Design configuration
LINT_TOP    := foc_top
LINT_PARAMS := -GDATA_W=24 -GFRAC_W=12 -GCORDIC_ITERS=12 -GPIPE_DEPTH=2 -GPWM_BITS=10

all: lint

# ── RTL Linting ────────────────────────────────────────────────
lint:
	@echo "Running Verilator lint on FOC RTL..."
	verilator --lint-only -Wall \
		-Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND \
		--top-module $(LINT_TOP) \
		$(LINT_PARAMS) \
		$(RTL_FILES)
	@echo "FOC lint passed."

lint-strict:
	verilator --lint-only -Wall \
		--top-module $(LINT_TOP) \
		$(LINT_PARAMS) \
		$(RTL_FILES)

# Individual module lint
lint-%:
	verilator --lint-only -Wall \
		--top-module $* \
		$(RTL_DIR)/$*.sv $(RTL_DIR)/foc_pkg.sv

# ── Golden Model ───────────────────────────────────────────────
golden:
	@echo "Running FOC golden model sanity check..."
	cd $(MODEL_DIR) && $(PYTHON) foc_golden.py
	@echo ""

golden-fixedpoint:
	@echo "Running FOC fixed-point model comparison..."
	cd $(MODEL_DIR) && $(PYTHON) foc_fixed_point.py

# ── Testbench (cocotb) ────────────────────────────────────────
TB_DIR := $(DESIGN_DIR)/tb
VENV_PATH := $(PROJ_ROOT)/.venv/bin

# Parameter overrides: make test DATA_W=12 FRAC_W=11 CORDIC_ITERS=8
PARAM_ARGS = DATA_W=$(DATA_W) FRAC_W=$(FRAC_W) CORDIC_ITERS=$(CORDIC_ITERS) \
             PIPE_DEPTH=$(PIPE_DEPTH) PWM_BITS=$(PWM_BITS) SHARED_MUL=$(SHARED_MUL) \
             ANGLE_W=$(ANGLE_W) PI_ACC_W=$(PI_ACC_W)

# Include param defaults
include $(TB_DIR)/common/params.mk

test: test-cordic test-clarke test-park test-pi test-svpwm test-foc-top
	@echo "All FOC cocotb tests passed."

test-cordic:
	@echo "Running CORDIC tests (DW=$(DATA_W) CI=$(CORDIC_ITERS))..."
	PATH=$(VENV_PATH):$$PATH $(MAKE) -C $(TB_DIR)/test_cordic $(PARAM_ARGS)

test-clarke:
	@echo "Running Clarke tests (DW=$(DATA_W))..."
	PATH=$(VENV_PATH):$$PATH $(MAKE) -C $(TB_DIR)/test_clarke $(PARAM_ARGS)

test-park:
	@echo "Running Park tests (DW=$(DATA_W))..."
	PATH=$(VENV_PATH):$$PATH $(MAKE) -C $(TB_DIR)/test_park $(PARAM_ARGS)

test-pi:
	@echo "Running PI controller tests (DW=$(DATA_W))..."
	PATH=$(VENV_PATH):$$PATH $(MAKE) -C $(TB_DIR)/test_pi $(PARAM_ARGS)

test-svpwm:
	@echo "Running SVPWM tests (DW=$(DATA_W))..."
	PATH=$(VENV_PATH):$$PATH $(MAKE) -C $(TB_DIR)/test_svpwm $(PARAM_ARGS)

test-foc-top:
	@echo "Running FOC top integration tests (DW=$(DATA_W))..."
	PATH=$(VENV_PATH):$$PATH $(MAKE) -C $(TB_DIR)/test_foc_top $(PARAM_ARGS)

# ── Synthesis ─────────────────────────────────────────────────
synth-one:
	@echo "Running single-config Yosys synthesis (FOC defaults)..."
	$(PYTHON) $(INFRA_DIR)/synth_sweep.py --design-dir $(DESIGN_DIR) --single

sweep-quick:
	@echo "Running quick synthesis sweep (FOC)..."
	$(PYTHON) $(INFRA_DIR)/synth_sweep.py --design-dir $(DESIGN_DIR) --preset quick

sweep-medium:
	@echo "Running medium synthesis sweep (FOC)..."
	$(PYTHON) $(INFRA_DIR)/synth_sweep.py --design-dir $(DESIGN_DIR) --preset medium

# ── Physical Design (OpenLane2) ────────────────────────────────
prep-rtl:
	@echo "Preprocessing RTL for Yosys/OpenLane2..."
	$(PYTHON) $(INFRA_DIR)/prep_rtl.py --design-dir $(DESIGN_DIR)

pnr-check:
	@echo "Checking OpenLane2 / Nix installation..."
	@which nix >/dev/null 2>&1 || (echo "ERROR: Nix not installed. Run scripts/install_openlane.sh" && exit 1)
	@echo "  nix: $$(nix --version 2>/dev/null)"

pnr: prep-rtl
	@echo "Running OpenLane2 flow for foc_top..."
	cd openlane/foc_top && openlane config.json

# ── Cleanup ────────────────────────────────────────────────────
clean:
	rm -rf obj_dir/ sim_build/ __pycache__/
	rm -rf synth/results/*.ys synth/results/*.json synth/results/*.v synth/results/*.log synth/results/*.csv
	rm -rf openlane/foc_top/src/*.sv
	for d in $(TB_DIR)/test_*/; do rm -rf $$d/sim_build $$d/results.xml $$d/__pycache__; done
	find . -name "*.vcd" -delete 2>/dev/null || true
	find . -name "*.fst" -delete 2>/dev/null || true
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
