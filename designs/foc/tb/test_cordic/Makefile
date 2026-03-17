PROJ_ROOT := $(shell cd ../../../.. && pwd)
DESIGN_DIR := $(shell cd ../.. && pwd)
VENV := $(PROJ_ROOT)/.venv
SHELL := /bin/bash
export PATH := $(VENV)/bin:$(PATH)


SIM ?= icarus
TOPLEVEL_LANG ?= verilog

VERILOG_SOURCES = $(DESIGN_DIR)/rtl/foc_pkg.sv $(DESIGN_DIR)/rtl/foc_cordic.sv
TOPLEVEL = foc_cordic
MODULE = test_cordic

COMPILE_ARGS += -g2012
COMPILE_ARGS += -Pfoc_cordic.DATA_W=16 -Pfoc_cordic.FRAC_W=15
COMPILE_ARGS += -Pfoc_cordic.ANGLE_W=16 -Pfoc_cordic.CORDIC_ITERS=16

export PYTHONPATH := $(DESIGN_DIR)/tb/common:$(DESIGN_DIR)/model:$(PROJ_ROOT)/infra

COCOTB_MAKEFILES := $(shell PATH=$(VENV)/bin:$$PATH cocotb-config --makefiles)
include $(COCOTB_MAKEFILES)/Makefile.sim
