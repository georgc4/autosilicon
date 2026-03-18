PROJ_ROOT := $(shell cd ../../../.. && pwd)
DESIGN_DIR := $(shell cd ../.. && pwd)
VENV := $(PROJ_ROOT)/.venv
SHELL := /bin/bash
export PATH := $(VENV)/bin:$(PATH)

include $(DESIGN_DIR)/tb/common/params.mk

SIM ?= icarus
TOPLEVEL_LANG ?= verilog

VERILOG_SOURCES = $(DESIGN_DIR)/rtl/foc_pkg.sv $(DESIGN_DIR)/rtl/foc_svpwm.sv
TOPLEVEL = foc_svpwm
MODULE = test_svpwm

COMPILE_ARGS += -g2012
COMPILE_ARGS += -Pfoc_svpwm.DATA_W=$(DATA_W) -Pfoc_svpwm.FRAC_W=$(FRAC_W) -Pfoc_svpwm.PWM_BITS=$(PWM_BITS)

export PYTHONPATH := $(DESIGN_DIR)/tb/common:$(DESIGN_DIR)/model:$(PROJ_ROOT)/infra

COCOTB_MAKEFILES := $(shell PATH=$(VENV)/bin:$$PATH cocotb-config --makefiles)
include $(COCOTB_MAKEFILES)/Makefile.sim
