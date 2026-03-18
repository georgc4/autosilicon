# Shared FOC parameter defaults — override via make args:
#   make test DATA_W=12 FRAC_W=11 CORDIC_ITERS=8

DATA_W ?= 16
FRAC_W ?= 15
PWM_BITS ?= 10
ANGLE_W ?= 16
PI_ACC_W ?= $(shell echo $$(($(DATA_W) * 2)))
CORDIC_ITERS ?= 16
PIPE_DEPTH ?= 1
SHARED_MUL ?= 0

# Export for Python (foc_tb_utils.py reads these)
export DATA_W
export FRAC_W
export PWM_BITS
export ANGLE_W
export PI_ACC_W
export CORDIC_ITERS
export PIPE_DEPTH
export SHARED_MUL
