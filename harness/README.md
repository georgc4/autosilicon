# AutoSilicon Harness

LLM-in-the-loop hardware design optimization, inspired by
[Karpathy's autoresearch](https://github.com/karpathy/autoresearch).

An autonomous agent iteratively modifies RTL or PnR configuration,
evaluates via synthesis/PnR, and keeps improvements while discarding
regressions. The agent never stops — it loops until interrupted or
a per-launch experiment budget is reached.

## Quick start

```bash
# Frontend optimization (RTL → synthesis, default config only)
python harness/autosilicon.py \
    --mode fe \
    --design-dir ~/laplace-accelerator

# Frontend optimization with Codex CLI + GPT-5.4
python harness/autosilicon.py \
    --mode fe \
    --design-dir ~/laplace-accelerator \
    --agent-cli codex \
    --agent-model gpt-5.4

# Backend optimization (PnR configuration)
python harness/autosilicon.py \
    --mode be \
    --design-dir ~/laplace-accelerator

# With limits
python harness/autosilicon.py \
    --mode fe \
    --design-dir ~/laplace-accelerator \
    --max-experiments 1 \
    --agent-model claude-opus-4-6

# Zero-cost wiring check
python harness/autosilicon.py \
    --mode fe \
    --design-dir ~/laplace-accelerator \
    --agent-cli codex \
    --agent-model gpt-5.4 \
    --max-experiments 1 \
    --dry-run

# Keep a run's artifacts in a namespaced subdirectory
python harness/autosilicon.py \
    --mode fe \
    --design-dir ~/autosilicon/designs/foc \
    --agent-cli codex \
    --agent-model gpt-5.4 \
    --artifacts-dir runs/gpt54-seed-r1 \
    --max-experiments 1

# One-command seeded Codex launcher for FOC
python scripts/launch_agent_run.py \
    --design-dir designs/foc \
    --agent-cli codex \
    --agent-model gpt-5.4 \
    --run-name gpt54-seed-r1 \
    --max-experiments 1

# With longer budgets
python harness/autosilicon.py \
    --mode fe \
    --design-dir ~/laplace-accelerator \
    --max-experiments 50 \
    --agent-timeout 1200 \
    --timeout-per-run 3600
```

## Prerequisites

- **Python 3.11+**
- **One supported agent CLI** on PATH:
  - **Claude Code CLI** (`claude`)
  - **Codex CLI** (`codex`)
  - On macOS, the harness will also auto-detect the bundled Codex binary at `/Applications/Codex.app/Contents/Resources/codex`
- **Git** — the design directory must be a git repository
- **Frontend mode:** `yosys`, `verilator`, `cocotb` (for `make lint`, `make test`, `make synth-one`)
- **Backend mode:** OpenLane2 / Nix (for `make pnr`)

## How it works

```
┌─────────────────────────────────────────────────────────┐
│                    EXPERIMENT LOOP                       │
│                                                         │
│  1. Build prompt (program.md + history + frontier)       │
│  2. Invoke agent CLI → agent edits design files          │
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
- Optimizes: area (min) × estimated_fmax (max)
- Hard gate: tests must pass
- Scope: one canonical default synthesis configuration per experiment

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
| `--max-experiments` | 0 (infinite) | Run at most N new experiments in this invocation |
| `--agent-timeout` | 900s | Timeout per agent invocation |
| `--timeout-per-run` | 1800s | Timeout per evaluation run |
| `--program-md` | `<design-dir>/program.md` | Path to program.md |
| `--artifacts-dir` | `<design-dir>` | Directory where run artifacts are written |
| `--log-file` | `<design-dir>/autosilicon.log` | Run log path |
| `--status-file` | `<design-dir>/autosilicon_status.json` | Live status JSON path |
| `--agent-cli` | `claude` | Agent runner: `claude` or `codex` |
| `--agent-model` | (default) | Model override for the selected agent CLI |
| `--claude-model` | (default) | Deprecated alias for `--agent-model` when using Claude |
| `--dry-run` | off | Validate prompt + run wiring without invoking the agent |

## Output files

| File | Description |
|------|-------------|
| `results.tsv` | Append-only experiment log (NOT committed to git) |
| `pareto_frontier.json` | Current Pareto frontier over verified default-config runs |
| `autosilicon.log` | Full run log with timestamps |
| `autosilicon_status.json` | Live run metadata for dashboards and monitoring |
| `.autosilicon_prompt.txt` | The exact prompt for the current/last experiment |

If `--artifacts-dir` is set, all of the files above are written under that directory instead of the design root. This is the cleanest way to keep multiple lineages in one repo without mixing their evidence.

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
