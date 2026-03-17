# FOC Motor Coprocessor — Timing Constraints for Sky130 @ 50 MHz
# ================================================================
# 50 MHz target is conservative for Sky130 hd cells.
# FOC pipeline is less multiplier-heavy than NLT, so timing should
# be achievable with moderate pipelining (PIPE_DEPTH=2).

# Clock definition: 50 MHz = 20 ns period
create_clock [get_ports clk] -name core_clk -period 20.0

# Input delay: assume signals arrive 5ns after clock edge
# (from SPI/encoder interface or upstream bus)
set_input_delay 5.0 -clock core_clk [all_inputs]
set_input_delay 0.0 -clock core_clk [get_ports clk]

# Output delay: assume downstream (PWM driver) needs signals 5ns before next edge
set_output_delay 5.0 -clock core_clk [all_outputs]

# Clock uncertainty (jitter + skew budget)
set_clock_uncertainty 0.5 [get_clocks core_clk]

# Don't optimize clock net
set_dont_touch_network [get_ports clk]

# False paths: reset is async, treat as false path for setup
set_false_path -from [get_ports rst_n]
