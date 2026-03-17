# AutoSilicon Harness

LLM-in-the-loop hardware design optimization, inspired by
[Karpathy's autoresearch](https://github.com/karpathy/autoresearch).

An autonomous agent iteratively modifies RTL or PnR configuration,
evaluates via synthesis/PnR, and keeps improvements while discarding
regressions. The agent never stops — it loops until interrupted or
a maximum experiment count is reached.

## Quick start

```bash
# Frontend optimization (RTL → synthesis)
python -m autosilicon.harness.autosilicon \
    --mode fe \
    --design-dir ~/laplace-accelerator

# Backend optimization (PnR configuration)
python -m autosilicon.harness.autosilicon \
    --mode be \
    --design-dir ~/laplace-accelerator

# With limits
python -m autosilicon.harness.autosilicon \
    --mode fe \
    --design-dir ~/laplace-accelerator \
    --max-experiments 50 \
    --timeout-per-run 3600
```

## Prerequisites

- **Python 3.11+**
- **Claude Code CLI** (`claude`) on PATH
- **Git** — the design directory must be a git repository
- **Frontend mode:** `yosys`, `verilator`, `cocotb` (for `make lint`, `make test`, `make synth-one`)
- **Backend mode:** OpenLane2 / Nix (for `make pnr`)

## How it works

```
┌─────────────────────────────────────────────────────────┐
│                    EXPERIMENT LOOP                       │
│                                                         │
│  1. Build prompt (program.md + history + frontier)       │
│  2. Invoke Claude Code CLI → agent edits design files    │
│  3. git commit (before evaluation, so every attempt      │
│     is recorded)                                        │
│  4. Run evaluation pipeline (lint → test → synth, or pnr)│
│  5. Append to results.tsv                                │
│  6. Keep or discard:                                    │
│     - Hard gate failed? → revert                        │
│     - Not Pareto-improving? → revert                    │
│     - Otherwise → keep, update frontier                 │
│  7. Loop forever                                        │
└─────────────────────────────────────────────────────────┘
```

## Modes

### Frontend (`--mode fe`)

- Agent edits: `rtl/*.sv`, `model/*.py`
- Pipeline: `make lint` → `make test` → `make synth-one`
- Optimizes: gate_count (min) × estimated_fmax (max)
- Hard gate: tests must pass

### Backend (`--mode be`)

- Agent edits: `openlane/*/config.json`, `*.sdc`, `pin_order.cfg`
- Pipeline: `make pnr`
- Optimizes: die_area (min) × WNS (max) × power (min)
- Hard gate: DRC violations must not increase

## Configuration

### program.md

The main control file. Write it in English — it tells the agent what to
optimize, what constraints to respect, and how to think about the design
space. Templates are provided:

- `program_template_fe.md` — frontend optimization
- `program_template_be.md` — backend optimization

Copy a template to `<design-dir>/program.md` and customize for your design.

### CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--mode` | required | `fe` or `be` |
| `--design-dir` | required | Path to the design git repo |
| `--max-experiments` | 0 (infinite) | Stop after N experiments |
| `--timeout-per-run` | 1800s | Timeout per evaluation run |
| `--program-md` | `<design-dir>/program.md` | Path to program.md |
| `--log-file` | `<design-dir>/autosilicon.log` | Run log path |
| `--claude-model` | (default) | Claude model override |

## Output files

| File | Description |
|------|-------------|
| `results.tsv` | Append-only experiment log (NOT committed to git) |
| `pareto_frontier.json` | Current Pareto frontier points |
| `autosilicon.log` | Full run log with timestamps |

## Architecture

```
harness/
├── autosilicon.py          Main loop
├── prompt_builder.py       Prompt construction
├── metric_extractor.py     Parse synthesis & PnR reports
├── pareto.py               Pareto frontier tracking
├── results.py              results.tsv management
├── program_template_fe.md  Template for frontend optimization
├── program_template_be.md  Template for backend optimization
└── README.md               This file
```

## Scope containment

The agent can ONLY edit design files. It cannot modify:
- The Makefile or build system
- Testbench or verification infrastructure
- Synthesis or PnR scripts
- The harness itself

Unauthorized modifications are detected after commit and result in
automatic revert.
