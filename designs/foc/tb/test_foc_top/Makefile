PROJ_ROOT := $(shell cd ../../../.. && pwd)
DESIGN_DIR := $(shell cd ../.. && pwd)
VENV := $(PROJ_ROOT)/.venv
SHELL := /bin/bash
export PATH := $(VENV)/bin:$(PATH)

include $(DESIGN_DIR)/tb/common/params.mk

SIM ?= icarus
TOPLEVEL_LANG ?= verilog

VERILOG_SOURCES = \
    $(DESIGN_DIR)/rtl/foc_pkg.sv \
    $(DESIGN_DIR)/rtl/foc_cordic.sv \
    $(DESIGN_DIR)/rtl/foc_clarke.sv \
    $(DESIGN_DIR)/rtl/foc_park.sv \
    $(DESIGN_DIR)/rtl/foc_pi.sv \
    $(DESIGN_DIR)/rtl/foc_inv_park.sv \
    $(DESIGN_DIR)/rtl/foc_svpwm.sv \
    $(DESIGN_DIR)/rtl/foc_ctrl.sv \
    $(DESIGN_DIR)/rtl/foc_wb.sv \
    $(DESIGN_DIR)/rtl/foc_top.sv

TOPLEVEL = foc_top
MODULE = test_foc_top

COMPILE_ARGS += -g2012
COMPILE_ARGS += -Pfoc_top.DATA_W=$(DATA_W) -Pfoc_top.FRAC_W=$(FRAC_W)
COMPILE_ARGS += -Pfoc_top.CORDIC_ITERS=$(CORDIC_ITERS) -Pfoc_top.PIPE_DEPTH=$(PIPE_DEPTH)
COMPILE_ARGS += -Pfoc_top.PWM_BITS=$(PWM_BITS) -Pfoc_top.SHARED_MUL=$(SHARED_MUL)

export PYTHONPATH := $(DESIGN_DIR)/tb/common:$(DESIGN_DIR)/model:$(PROJ_ROOT)/infra

COCOTB_MAKEFILES := $(shell PATH=$(VENV)/bin:$$PATH cocotb-config --makefiles)
include $(COCOTB_MAKEFILES)/Makefile.sim
