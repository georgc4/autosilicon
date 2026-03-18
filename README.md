# AutoSilicon

Autonomous LLM-in-the-loop RTL optimization. An agent iteratively modifies SystemVerilog, evaluates via synthesis (Yosys/Sky130), and keeps only Pareto-improving changes. Regressions are automatically reverted.

## How It Works

```
                    ┌─────────────┐
                    │  Specs +    │
                    │  RTL Source  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
              ┌────►│  LLM Agent  │ Claude Code / Codex
              │     │  (edit RTL) │
              │     └──────┬──────┘
              │            │ git commit
              │     ┌──────▼──────┐
              │     │    Lint     │ Verilator
              │     │    Test     │ cocotb
              │     │  Synthesize │ Yosys + Sky130
              │     └──────┬──────┘
              │            │
              │     ┌──────▼──────┐
              │     │ Hard Gates  │ Tests pass? ULP ok?
              │     │ Pareto Test │ Area + Fmax improved?
              │     └──────┬──────┘
              │            │
              │      ┌─────┴─────┐
              │      │           │
              │   keep        revert
              │      │           │
              └──────┘           │
                    ▲            │
                    └────────────┘
```

The loop runs autonomously until interrupted or the experiment budget is exhausted.

## Key Features

- **Iterative closed-loop optimization** with real EDA synthesis feedback
- **Multi-objective Pareto tracking** (area and Fmax, extensible to power)
- **Safety-gated autonomy**: scope enforcement, test hard gates, automatic revert on regression
- **Multi-agent support**: Claude Code CLI and Codex CLI pluggable
- **Reproducible**: every experiment committed to git with full metric logging

## Results: FOC Motor Controller

The included FOC (Field-Oriented Control) coprocessor is a pure-digital motor control accelerator targeting Sky130. Starting from a baseline of 215K um2 / 171 MHz, 19 autonomous experiments produced:

| Metric | Baseline | Best | Change |
|--------|----------|------|--------|
| Area | 215,164 um2 | 191,487 um2 | -11.0% |
| Fmax | 171.2 MHz | 187.3 MHz | +9.4% |
| Experiments | - | 19 | 68% kept |

Notable optimizations the LLM found autonomously:
- Replaced constant multipliers with CSD shift-add chains (saves hardware multipliers)
- Eliminated redundant pipeline registers (FSM serialization guarantees stability)
- Shared comparators across SVPWM min/max computation
- Narrowed bit-widths where full precision was unnecessary

**Provenance note**: The FOC design was created with substantial LLM assistance (Claude researched the math, designed the microarchitecture, and wrote the RTL). A human provided direction, specification format, and verification strategy. The optimization results are relative to this LLM-designed baseline.

## Quick Start

### Prerequisites

- Python 3.10+
- [Yosys](https://github.com/YosysHQ/yosys) (synthesis)
- [Verilator](https://verilator.org) (linting)
- [cocotb](https://cocotb.readthedocs.io) (testbenches)
- Sky130 PDK (via [volare](https://github.com/efabless/volare))
- [Claude Code](https://claude.ai/code) or [Codex](https://openai.com/codex) CLI

```bash
# Install Python dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install cocotb cocotb-test numpy pyyaml

# Verify tools
make toolcheck

# Dry run (validates wiring without invoking LLM)
python3 harness/autosilicon.py --design-dir designs/foc --mode fe --dry-run

# Run optimization (uses Claude by default)
python3 harness/autosilicon.py --design-dir designs/foc --mode fe --max-experiments 10

# Or with Codex
python3 harness/autosilicon.py --design-dir designs/foc --mode fe \
  --agent-cli codex --agent-model gpt-5.4 --max-experiments 10
```

### Isolated runs (for reproducible experiments)

```bash
python3 scripts/launch_agent_run.py \
  --design-dir designs/foc \
  --agent-cli claude --agent-model claude-opus-4-6 \
  --run-name opus-run-1 \
  --max-experiments 25
```

This creates an isolated git worktree so experiments don't interfere with each other.

## Bring Your Own Design

To optimize your own design:

1. Create `designs/your_design/` with:
   - `rtl/` directory with SystemVerilog source files
   - `Makefile` with targets: `lint`, `test`, `synth-one`
   - `program.md` (optimization directive for the LLM, or use the template)

2. Run:
   ```bash
   python3 harness/autosilicon.py --design-dir designs/your_design --mode fe
   ```

See `harness/program_template_fe.md` for the LLM directive template.

## Project Structure

```
harness/
  autosilicon.py        # Main experiment loop
  prompt_builder.py     # Assembles LLM prompts with history + RTL
  metric_extractor.py   # Parses Yosys/OpenLane outputs
  pareto.py             # Multi-objective Pareto frontier logic
  results.py            # Experiment log (results.tsv) management

designs/foc/
  rtl/                  # FOC coprocessor SystemVerilog (10 modules)
  tb/                   # cocotb testbenches (6 test suites)
  docs/                 # Math spec + microarchitecture spec
  dashboard.html        # Interactive Pareto frontier visualization

scripts/
  launch_agent_run.py   # Worktree-isolated experiment launcher
```

## How It Compares

| System | Approach | Feedback | Multi-objective |
|--------|----------|----------|-----------------|
| ChipChat | Conversational generation | Human | No |
| AutoChip | Automated generation | Test results | No |
| RTLCoder | Fine-tuned generation | None | No |
| **AutoSilicon** | **Iterative optimization** | **Synthesis PPA** | **Yes (Pareto)** |

## Status

- Frontend optimization (RTL + Yosys): working
- Backend optimization (OpenLane PnR): infrastructure ready, not yet evaluated
- External benchmark evaluation: in progress

## License

Apache 2.0. See [LICENSE](LICENSE).
