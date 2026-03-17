# AutoSilicon Paper Outline

*Detailed outline for a paper on LLM-in-the-loop hardware design space exploration.*
*Target venues: WOSET (4+1 pages, no template mandated, open-source required, single-blind, ~September deadline) or full conference (e.g., DAC, ICCAD, MLCAD).*

---

## Title Options

1. **AutoSilicon: LLM-in-the-Loop Design Space Exploration for Open-Source ASICs**
2. **Closing the Loop: Autonomous LLM-Driven RTL Optimization and Physical Design on Sky130**
3. **From Autoresearch to AutoSilicon: Applying Karpathy's Optimization Loop to ASIC Design**

Recommendation: Option 1 — clear, descriptive, citable. Option 3 is attention-grabbing but risks dating poorly.

---

## Abstract (~150 words, draft)

We present AutoSilicon, an autonomous design space exploration system that applies the autoresearch pattern — an LLM proposing modifications, a deterministic pipeline evaluating them, and a Pareto-based keep/discard policy — to open-source ASIC design. A Python harness invokes Claude Code CLI in a loop across two optimization phases. In the frontend phase, the LLM modifies parameterized RTL; Yosys synthesis evaluates gate count and estimated Fmax under a hard correctness constraint (all cocotb tests must pass). In the backend phase, the LLM modifies OpenLane configuration; full place-and-route evaluates die area, timing slack, DRC violations, and power. Pareto-improving changes are committed; regressions are reverted. We demonstrate AutoSilicon on the first open-source FOC (Field-Oriented Control) motor coprocessor targeting Sky130, showing [X]% area reduction and [Y] MHz Fmax improvement over the initial human design across [N] autonomous iterations. All code, logs, and the FOC design are open-source.

**Evidence needed before writing:** Final iteration counts, area/Fmax deltas, comparison baselines.

---

## Section 1: Introduction (~1 page full / ~0.5 page WOSET)

### Key claims
1. Hardware design is expensive: RTL → synthesis → PnR iteration cycles take hours/days; engineers manually interpret reports and adjust.
2. LLMs can read synthesis reports, understand timing paths, and propose targeted RTL or config modifications — but existing work uses them for one-shot generation, not iterative optimization.
3. Karpathy's autoresearch pattern (propose → evaluate → keep/discard) has proven effective for ML hyperparameter optimization but has never been applied to hardware.
4. AutoSilicon closes this gap: a fully autonomous loop across both frontend (synthesis) and backend (PnR) optimization, with cross-level feedback.

### Structure
- **Para 1:** The cost of hardware iteration. Manual read-report-modify cycles. Cite typical ASIC development timelines.
- **Para 2:** LLMs for hardware — recent explosion (6 papers in 2023 → 66+ in 2025). But the paradigm is *generation*, not *optimization*. Chip-Chat is conversational (human-in-the-loop per turn). MAGE achieves 95.7% VerilogEval but stops at RTL — no synthesis, no PnR. VeriGen, RTLCoder, RTLLM are benchmarks for one-shot correctness.
- **Para 3:** The autoresearch insight. Karpathy (2025) showed that an LLM can autonomously optimize ML training scripts by proposing changes, evaluating metrics, and keeping improvements. Hardware has an analogous structure: propose RTL change → synthesize → measure → keep or revert.
- **Para 4:** Our contributions (bulleted):
  - AutoSilicon: first application of the autoresearch loop to ASIC design
  - Two-phase optimization (frontend RTL + backend PnR) with cross-level feedback
  - Demonstrated on an FOC motor coprocessor — the first open-source FOC ASIC on Sky130
  - Fully open-source: harness, RTL, logs, evaluation pipeline

### Evidence needed
- Citation for typical ASIC development timelines (can cite Hennessy & Patterson or industry reports)
- Karpathy autoresearch blog post citation (2025)
- Precise paper counts for LLM+hardware growth (from prior_art.md: confirmed 6→66+)

---

## Section 2: Background (~0.75 page)

### 2.1 FOC Motor Control
- Clarke transform (3-phase → αβ) → Park transform (αβ → dq, requires sin/cos) → PI controllers (Id, Iq loops) → inverse Park (dq → αβ) → SVPWM (αβ → 3-phase duty cycles)
- Computational core: fixed-point CORDIC for sin/cos, multiply-accumulate for transforms, division for SVPWM sector calculation
- Real-time constraint: must complete within one PWM period (typically 50μs at 20 kHz switching)
- Why an ASIC: current solutions are either MCU firmware (flexible but slow/power-hungry) or FPGA (fast but expensive per-unit). A dedicated coprocessor enables low-power high-frequency FOC in cost-sensitive motor drives.

**Key claim:** FOC is a good vehicle design because it has clear parameterization axes (word width, CORDIC iterations, pipeline depth, multiplier sharing, PWM resolution) and a well-defined correctness criterion (motor model simulation match).

**Evidence needed:** FOC algorithm description (textbook, cite Bose or Krause), timing budget calculation for target PWM frequency.

### 2.2 Open-Source ASIC Flow
- Yosys (synthesis) → OpenLane 2 (floorplan, placement, CTS, routing, signoff) → Sky130 PDK
- Key metrics at each stage: gate count and estimated Fmax (synthesis), die area, WNS, TNS, DRC count, power (PnR)
- The flow is deterministic: same inputs → same outputs. This is critical for the keep/discard policy.

**Evidence needed:** OpenLane 2 version and Sky130 PDK version used. Confirm determinism (same config → same results).

### 2.3 The Autoresearch Pattern
- Karpathy (2025): LLM proposes a code modification to an ML training script. Script runs. Metrics are compared to the current best. If improved, the change is kept (git commit); otherwise, reverted (git checkout). Loop repeats.
- Key properties: (1) the LLM sees full history of prior attempts and their results, (2) the evaluation is deterministic and automated, (3) no human in the loop during optimization, (4) the search space is unbounded (the LLM can propose any valid code change).
- Our adaptation: replace "training script" with "RTL + PnR config", replace "training metrics" with "synthesis/PnR metrics", replace "validation loss" with "test pass + Pareto dominance".

**Evidence needed:** Karpathy blog post citation. Confirm the pattern description matches his implementation.

---

## Section 3: AutoSilicon Methodology (~1.5 pages) — MAIN CONTRIBUTION

### 3.1 System Architecture
- **Harness:** Python script that orchestrates the loop. Manages git state, constructs prompts, invokes Claude Code CLI (`claude -p <prompt> --allowedTools ...`), parses output, runs evaluation pipeline, records results.
- **Prompt construction:** Each iteration, the prompt includes: (a) the current design state (key RTL files or config), (b) the full optimization log (TSV: iteration, change description, metrics, kept/discarded), (c) the current Pareto frontier, (d) the evaluation criteria, (e) instructions to propose exactly one modification.
- **Evaluation pipeline:** A shell script or Makefile target that runs lint → cocotb tests → Yosys synthesis (frontend) or full OpenLane PnR (backend). Returns structured metrics (JSON or TSV).
- **Result recording:** TSV log with columns: iteration, timestamp, change_description, gate_count, fmax_est, tests_passed, area_um2, wns_ns, drc_count, power_mw, kept (bool), git_hash.

**Figure 1: System architecture diagram** — harness ↔ Claude Code CLI ↔ git repo ↔ evaluation pipeline ↔ metrics log ↔ prompt construction (cycle).

**Key claim:** The architecture is tool-agnostic: any LLM with a CLI interface can replace Claude Code; any synthesis/PnR flow can replace Yosys/OpenLane.

**Evidence needed:** Working harness code. Measured loop iteration time (wall-clock per frontend iteration, per backend iteration).

### 3.2 Frontend Optimization Loop
- **Scope:** LLM modifies parameterized RTL files (SystemVerilog). Changes can be: parameter value adjustments, microarchitectural restructuring (pipeline depth, resource sharing), algorithmic changes (e.g., CORDIC iterations vs. lookup table size).
- **Evaluation:** `make lint && make test && make synth-one` — Verilator lint, cocotb testbench suite, Yosys generic synthesis.
- **Metrics:** gate count (from Yosys `stat`), estimated Fmax (from Yosys `sta` or derived from critical path delay).
- **Hard constraint:** All cocotb tests must pass. Any test failure → automatic revert, no exceptions.
- **Soft optimization:** Minimize gate count, maximize Fmax. Pareto dominance determines keep/discard.

**Figure 2: Frontend optimization loop flowchart** — LLM proposes RTL change → lint → test → synthesize → extract metrics → Pareto check → commit or revert → update log → next iteration.

**Key claims:**
- The hard test constraint prevents the LLM from "gaming" metrics by breaking functionality.
- The Pareto policy avoids premature convergence to a single objective.

**Evidence needed:** Number of frontend iterations run. Examples of changes proposed (parameter tweaks vs. structural). Pass/fail/revert statistics.

### 3.3 Backend Optimization Loop
- **Scope:** LLM modifies OpenLane configuration (JSON/Tcl). The netlist is frozen from the frontend-optimized design. Changes include: clock period target, placement density, CTS parameters, routing layer usage, macro placement hints, power grid configuration.
- **Evaluation:** Full OpenLane PnR run (floorplan → placement → CTS → routing → signoff).
- **Metrics:** die area (μm²), worst negative slack (WNS in ns), total negative slack (TNS), DRC violation count, estimated power (mW).
- **Hard constraint:** DRC violations must be zero for a kept result (or monotonically decreasing toward zero during early iterations).
- **Soft optimization:** Minimize area, maximize timing margin (WNS → 0 or positive), minimize power.

**Key claim:** Backend optimization is slower per iteration (minutes to hours for PnR vs. seconds for synthesis) but the search space is more constrained (config parameters, not arbitrary code), so fewer iterations are needed.

**Evidence needed:** Backend iteration wall-clock time. Number of iterations to timing closure. OpenLane config parameters the LLM actually modified.

### 3.4 Pareto Frontier Tracking and Keep/Discard Policy
- **Multi-objective:** Frontend: gate_count × Fmax. Backend: area × WNS × power.
- **Pareto dominance:** A new design point is kept if it dominates (improves at least one metric without worsening any) or is non-dominated by any existing frontier point.
- **Tie-breaking:** If the new point is Pareto-equivalent (same frontier), keep it only if it introduces a structural change (not just noise). Hash the metrics to detect duplicates.
- **Frontier persistence:** The Pareto frontier is stored in the TSV log and reconstructed each iteration. The LLM sees the full frontier in its prompt, enabling it to target unexplored regions.
- **Git integration:** Each kept design is a git commit (with the change description in the commit message). The working tree always reflects the most recent kept design. Reverts use `git checkout -- .` to restore the previous state.

**Key claim:** Pareto tracking prevents the optimizer from collapsing to a single objective and provides interpretable progress visualization.

**Evidence needed:** Pareto frontier plots at intervals (iteration 10, 50, 100). Number of frontier points over time.

### 3.5 Cross-Level Feedback (PnR → RTL)
- **Trigger:** When backend optimization stalls (e.g., WNS remains negative after N iterations), extract the critical path from OpenLane timing reports.
- **Feedback mechanism:** The critical path (module hierarchy, cell types, arrival/required times) is formatted into a prompt for a new frontend iteration. The LLM is asked to restructure the RTL to break the critical path (e.g., add a pipeline register, retime logic, reduce fan-out).
- **Iteration:** After the RTL change, the full pipeline re-runs: lint → test → synthesis → PnR. If the timing improves, the change is kept across both levels.

**Key claim:** Cross-level feedback is what distinguishes AutoSilicon from running two independent optimization loops. It enables the system to escape local optima that are unreachable within a single abstraction level.

**Evidence needed:** At least one concrete example of cross-level feedback improving timing. Before/after WNS and the specific RTL change made.

### 3.6 Scope Containment and Correctness Guarantees
- **File allowlist:** The LLM is restricted to modifying specific files (RTL sources for frontend, OpenLane config for backend). It cannot modify testbenches, the harness, or the evaluation pipeline.
- **Tool allowlist:** Claude Code's `--allowedTools` flag restricts the LLM to file read/write and specific shell commands. No network access, no arbitrary code execution.
- **Deterministic evaluation:** The evaluation pipeline is a fixed script. The LLM cannot influence the evaluation criteria.
- **Test-gated commits:** No change is committed without passing the full test suite. The tests themselves are outside the LLM's modification scope.
- **Human checkpoints:** The harness supports a `--max-iterations` flag and a `--pause-on-structural` flag that pauses for human review when the LLM proposes a change classified as structural (vs. parametric).

**Key claim:** The correctness guarantee comes from the separation of concerns: the LLM proposes, the deterministic pipeline evaluates, and the test suite gates. The LLM cannot circumvent this.

**Evidence needed:** Description of the file/tool allowlists. Any instances where the LLM attempted to modify files outside its scope (and was blocked).

---

## Section 4: FOC Coprocessor Design (~1 page)

### 4.1 Architecture Overview
- Pipeline: ADC interface → Clarke transform → Park transform (with CORDIC sin/cos) → PI controllers (Iq, Id) → inverse Park → SVPWM generator → PWM output
- Bus interface: Wishbone B4, memory-mapped registers for configuration and status
- Fixed-point arithmetic throughout, parameterized word width
- Single-cycle or multi-cycle variants depending on pipeline depth parameter

**Figure 3: FOC coprocessor block diagram** — showing the transform pipeline, CORDIC block, PI controllers, and bus interface.

**Evidence needed:** Working RTL. Block diagram. Register map.

### 4.2 Parameterization Axes
- **Word width:** 16, 24, 32-bit fixed-point (Q8.8, Q12.12, Q16.16). Affects precision and area.
- **CORDIC iterations:** 8, 12, 16. More iterations = better sin/cos accuracy, more area and latency.
- **Pipeline depth:** 1 (combinational), 2, 4 stages. Deeper = higher Fmax, more registers.
- **Multiplier sharing:** Dedicated (one multiplier per transform stage) vs. shared (time-multiplexed). Area/throughput tradeoff.
- **PWM resolution:** 8, 10, 12-bit. Affects SVPWM output quality and timer width.

**Table 1: FOC parameter space** — all axes, their values, total configuration count.

**Key claim:** This parameter space is large enough that exhaustive sweep is impractical at the PnR level (each PnR run takes minutes to hours), motivating the LLM-guided search.

**Evidence needed:** Parameter space size calculation. Estimated wall-clock time for exhaustive PnR sweep vs. LLM-guided.

### 4.3 Verification Strategy
- **Golden model:** Python (NumPy) FOC pipeline with configurable parameters. Generates input/output vectors.
- **cocotb testbenches:** Per-block tests (Clarke, Park, CORDIC, PI, SVPWM) and top-level integration tests.
- **Motor model simulation:** Simplified PMSM model driven by the coprocessor's PWM output, checking torque/speed convergence.
- **Test count and coverage:** [N] tests, covering normal operation, edge cases (zero speed, max torque, field weakening region), and parameter corner cases.

**Evidence needed:** Working cocotb testbench suite. Test count and descriptions. Pass rate across parameter configurations.

---

## Section 5: Experimental Results (~1.5 pages)

### 5.1 Frontend Loop: Pareto Frontier Evolution
- Plot the Pareto frontier (gate count vs. estimated Fmax) at iterations 1, 10, 25, 50, 100 (or whatever the actual iteration counts are).
- Show the frontier expanding and tightening over iterations.
- Highlight the initial human design point and the final best points.

**Figure 4: Pareto frontier evolution (frontend)** — gate count (x) vs. Fmax (y), colored by iteration number, with Pareto frontier lines at key intervals.

**Key claim:** The LLM-driven search finds design points that dominate the initial human design within [N] iterations.

**Evidence needed:** Actual synthesis results from the optimization loop. Minimum [20] iterations for a meaningful plot; ideally [50+].

### 5.2 Taxonomy of LLM-Proposed Changes
- Categorize every proposed change: (a) parameter adjustment (e.g., CORDIC iterations 16→12), (b) microarchitectural (e.g., add pipeline register, share multiplier), (c) algorithmic (e.g., replace CORDIC with LUT for small angles), (d) cosmetic/no-op (e.g., reformatting, comments).
- Report acceptance rate by category.
- Analyze: did the LLM converge to parameter tweaks after exhausting structural changes, or vice versa?

**Figure 6: Taxonomy of LLM-proposed changes** — stacked bar chart or pie chart showing change categories, split by accepted/rejected.

**Key claim:** The LLM proposes a mix of parametric and structural changes, with structural changes contributing disproportionately to Pareto improvements.

**Evidence needed:** Manual or automated classification of all proposed changes from the optimization log.

### 5.3 Backend Loop: Timing Closure and Area Optimization
- Plot die area vs. WNS over backend iterations.
- Show the trajectory from initial (possibly timing-violated) design to timing-closed, area-optimized result.
- Report DRC violation count over iterations (should reach zero).

**Figure 5: Pareto frontier evolution (backend)** — die area (x) vs. WNS (y), with the feasibility boundary (WNS >= 0) highlighted.

**Key claim:** The LLM achieves timing closure and reduces area relative to default OpenLane configuration within [N] backend iterations.

**Evidence needed:** Actual PnR results. At least [10] backend iterations.

### 5.4 Cross-Level Feedback Examples
- Present 1-2 concrete examples where backend timing failure triggered an RTL restructuring.
- Show: (a) the failing critical path from OpenLane, (b) the LLM's proposed RTL change, (c) the resulting timing improvement.
- Quantify: WNS before and after, area impact.

**Evidence needed:** At least one actual cross-level feedback instance with before/after metrics.

### 5.5 Comparison: LLM-Optimized vs. Initial Human Design vs. Sweep-Only
- **Baseline 1:** Initial human-written FOC design (before any LLM optimization).
- **Baseline 2:** Best result from a parameter-sweep-only approach (exhaustive or random sampling of the parameter space, no LLM, no structural changes).
- **AutoSilicon:** Best result from the LLM-driven optimization loop.
- Compare on: gate count, Fmax, die area, WNS, power.

**Table 2: Final design metrics comparison** — rows: initial human, sweep-only best, AutoSilicon best. Columns: gate count, Fmax (MHz), die area (um2), WNS (ns), power (mW).

**Key claim:** AutoSilicon outperforms parameter sweep because it can propose structural changes that sweep cannot explore. It outperforms the initial human design because it explores more iterations than a human would attempt.

**Evidence needed:** All three baselines with complete metrics. The parameter sweep must be a fair comparison (same total compute budget or same number of evaluations).

### 5.6 Cost Analysis
- **Tokens:** Total input/output tokens consumed across all iterations. Cost at API pricing.
- **Wall-clock time:** Total runtime, broken down by LLM inference time vs. evaluation pipeline time.
- **Human intervention:** Number and nature of human interventions (if any) during the autonomous loop.
- **Comparison:** Estimated human-engineer time to achieve the same result (rough order-of-magnitude).

**Table 3: Cost breakdown** — tokens (input/output), API cost ($), wall-clock time (hours), evaluation time (hours), human interventions (count), estimated human-equivalent time.

**Key claim:** The total cost (API + compute) is significantly lower than human-engineer time for equivalent optimization, though the quality ceiling may be lower.

**Evidence needed:** Token counts from Claude Code logs. Wall-clock measurements. Honest estimate of human-equivalent effort.

---

## Section 6: Discussion (~0.5 page)

### What Worked
- The LLM effectively reads synthesis reports and proposes targeted changes.
- The Pareto-based keep/discard policy provides stable convergence.
- The test-gated commit policy prevented any functionality regressions.
- Cross-level feedback enabled escaping local optima at a single abstraction level.

### What Didn't Work / Failure Modes
- **Goodhart's law in hardware:** Did the LLM learn to game metrics? E.g., reducing gate count by removing functionality that isn't covered by tests. Discuss how the test suite's completeness bounds this risk.
- **Structural change plateau:** After some iterations, does the LLM exhaust meaningful structural changes and degenerate to parameter tweaking? Report if/when this happened.
- **Prompt length growth:** The optimization log grows with each iteration. At what point does the log exceed the context window? How was this managed (summarization, truncation, sliding window)?
- **PnR noise:** Small OpenLane config changes can produce non-monotonic metric changes due to placement randomness. How does the Pareto policy handle this?

### Limitations and Threats to Validity
- Single vehicle design (FOC). Transferability to other designs is demonstrated only anecdotally (NLT accelerator from prior work used same infrastructure).
- Single LLM (Claude). Different LLMs may produce different optimization trajectories.
- Sky130 only. Results may not transfer to advanced nodes.
- The "initial human design" baseline is set by the authors, not an independent expert.
- cocotb test suite completeness is a human judgment call; undertesting could mask LLM-introduced bugs.

---

## Section 7: Related Work (~0.5 page)

### LLM for Hardware Design
- **One-shot generation:** VeriGen [Thakur 2024], RTLCoder [2024], RTLLM [Lu 2024], VerilogEval [Liu 2023]. Benchmarks for LLM RTL generation quality. No iteration, no synthesis feedback.
- **Conversational design:** Chip-Chat [Blocklove 2023]. Human-in-the-loop per turn. 8-bit processor, Sky130 tapeout. Our work: fully autonomous loop, more complex design.
- **Multi-agent RTL:** MAGE [DAC 2025]. 95.7% VerilogEval correctness with Claude 3.5 Sonnet. RTL generation only, no synthesis/PnR evaluation. Our work: closes the loop through physical design.
- **Agentic full-flow:** AiEDA [2024] (concept-to-GDSII, limited results), Architect in the Loop [2025] (RISC-V, identified "semantic cohesion gap"). Our work: quantitative results across full flow.
- **Industry:** Cadence ChipStack [2026] (10x productivity claim, commercial, closed-source).

### AutoML and Autoresearch
- **Karpathy autoresearch [2025]:** Direct inspiration. LLM optimizes ML training scripts in a loop. We adapt the pattern to hardware.
- **AutoML (NAS, hyperparameter optimization):** Neural architecture search uses RL/evolutionary methods to optimize model architecture. AutoSilicon uses an LLM instead of RL, operating on RTL instead of model graphs. The LLM can propose structural changes that fixed search spaces cannot express.

### Design Space Exploration in EDA
- **Traditional DSE:** Parameter sweeps, genetic algorithms, Bayesian optimization over fixed parameter spaces.
- **Our distinction:** The LLM can modify the design *structurally* (adding pipeline stages, changing resource sharing), not just tune parameters. This is DSE with an unbounded, code-level search space.

---

## Section 8: Conclusion (~0.25 page)

- Restate the core contribution: AutoSilicon applies the autoresearch pattern to ASIC design, closing the loop across RTL optimization and physical design.
- Headline results: [X]% area improvement, [Y] MHz Fmax, [Z] autonomous iterations, [$W] API cost.
- The FOC coprocessor is (to our knowledge) the first open-source FOC ASIC on Sky130.
- Open-source availability: all code, logs, and the FOC design are released under [license].
- Future work: (1) multi-design generalization, (2) LLM-driven testbench augmentation, (3) integration with formal verification, (4) application to advanced nodes via OpenROAD.

---

## Figure and Table Plan

| ID | Type | Description | When Available |
|----|------|-------------|----------------|
| Fig 1 | Diagram | AutoSilicon system architecture — harness, LLM, git, eval pipeline, log, prompt cycle | Can draw now (methodology is defined) |
| Fig 2 | Flowchart | Frontend optimization loop — propose → lint → test → synth → Pareto check → commit/revert | Can draw now |
| Fig 3 | Block diagram | FOC coprocessor — Clarke → Park → PI → inv Park → SVPWM, with CORDIC and bus interface | Need FOC RTL complete |
| Fig 4 | Scatter plot | Pareto frontier evolution (frontend) — gate count vs Fmax, colored by iteration | Need 50+ frontend iterations |
| Fig 5 | Scatter plot | Pareto frontier evolution (backend) — die area vs WNS, colored by iteration | Need 10+ backend iterations |
| Fig 6 | Bar/pie chart | Taxonomy of LLM-proposed changes — parametric vs structural vs algorithmic, accepted/rejected | Need change log classification |
| Fig 7 | Screenshot | Final GDS layout from OpenLane | Need completed PnR |
| Table 1 | Table | FOC parameter space — axes, values, total configurations | Need FOC parameterization finalized |
| Table 2 | Table | Metrics comparison — initial human vs sweep-only vs AutoSilicon | Need all three baselines |
| Table 3 | Table | Cost breakdown — tokens, API cost, wall-clock, human interventions | Need completed optimization runs |

---

## WOSET Submission Guidelines (Verified)

- **Venue:** Workshop on Open-Source EDA Technology, co-located with ICCAD (typically November)
- **Regular papers:** 4 pages + 1 page references, 15-minute video presentation
- **Work-in-progress:** 2-page abstract + 1 page references, 10-minute video
- **Format:** No specific template mandated (authors use varying formats; IEEE two-column is common)
- **Open-source requirement:** All source code must be available under an open-source license (BSD/GPL/Apache) — mandatory
- **Review:** Single-blind, no registration fee, virtual workshop
- **Proceedings:** Published on woset-workshop.github.io (not IEEE/ACM proceedings)
- **Submission platform:** OpenReview
- **Typical deadlines:** Submission late September, notification mid-October, workshop mid-November
- **Precedent:** WOSET 2024 accepted ORAssistant (AI+EDA chatbot) and OpenLane 2, confirming AI-assisted open-source EDA methodology is in scope
- **Next edition:** No 2025/2026 CFP announced yet; watch woset-workshop.github.io
- **Also relevant:** Open-Source EDA Birds-of-a-Feather at DAC 2025 (same community)

---

## WOSET Distillation Plan (4+1 pages)

### Page Budget

| Section | Full Paper | WOSET (4 pages) | Strategy |
|---------|-----------|-----------------|----------|
| Abstract | 0.25 | 0.2 | Tighten |
| 1. Introduction | 1.0 | 0.5 | Cut to 3 paragraphs: problem, gap, contribution |
| 2. Background | 0.75 | — | Merge into Sections 3 and 4 |
| 3. Methodology | 1.5 | 1.0 | Cut 3.5 (cross-level) to one paragraph; cut 3.6 (scope) entirely |
| 4. FOC Design | 1.0 | 0.5 | One paragraph + block diagram; fold into methodology as "vehicle design" |
| 5. Results | 1.5 | 1.0 | Keep 5.1, 5.2, 5.5; cut 5.3, 5.4, 5.6 |
| 6. Discussion | 0.5 | — | Fold 2-3 key points into conclusion |
| 7. Related Work | 0.5 | 0.5 | Keep but compress (one paragraph per category) |
| 8. Conclusion | 0.25 | 0.3 | Absorb discussion highlights |
| References | 1.0 | 1.0 | Separate page (allowed) |
| **Total** | **~7.25** | **4.0 + 1.0 refs** | |

### Figures for WOSET (max 4-5)
1. Fig 1: System architecture (essential — the main contribution)
2. Fig 3: FOC block diagram (small, inset)
3. Fig 4: Pareto frontier evolution — frontend (the money plot)
4. Table 2: Metrics comparison (the punchline)
5. Fig 6: Change taxonomy (if space permits; otherwise cut)

### What Gets Cut
- Background section (fold FOC description into one paragraph; assume reader knows Yosys/OpenLane)
- Cross-level feedback details (mention as future work)
- Scope containment details (one sentence: "tests gate all commits")
- Backend-only results (mention in passing; focus on frontend loop which has more iterations)
- Cost analysis table (mention API cost in one sentence)
- GDS screenshot (save for poster/talk)

---

## Prerequisites Checklist

Before the paper can be written, the following must exist:

### Must Have (blocking)
- [ ] FOC coprocessor RTL — parameterized, lint-clean
- [ ] FOC cocotb testbench suite — comprehensive, all passing
- [ ] AutoSilicon harness — Python script implementing the loop
- [ ] Frontend optimization run — minimum 50 iterations with TSV log
- [ ] Pareto frontier plots from actual data
- [ ] Change taxonomy classification of the optimization log
- [ ] Comparison baselines: initial human design metrics, parameter-sweep-only metrics

### Should Have (strengthens paper significantly)
- [ ] Backend optimization run — minimum 10 iterations with PnR metrics
- [ ] At least one cross-level feedback example with before/after metrics
- [ ] OpenLane PnR completed (GDS screenshot)
- [ ] Cost analysis (token counts, wall-clock times)

### Nice to Have (for full conference version)
- [ ] NLT accelerator as second vehicle design (transferability evidence)
- [ ] Multiple LLM comparison (Claude vs. GPT-4 vs. open-source)
- [ ] Formal verification integration
- [ ] Post-layout simulation results

---

## Differentiation Summary

| Dimension | Chip-Chat | MAGE | VeriGen/RTLCoder | AutoSilicon |
|-----------|-----------|------|------------------|-------------|
| LLM role | Conversational partner | Multi-agent RTL generator | One-shot code generator | Autonomous optimizer |
| Human in loop? | Every turn | Setup only | One-shot | Setup only |
| Iteration? | Manual | No | No | Autonomous loop |
| Synthesis feedback? | No | No | No | Yes (Yosys) |
| PnR feedback? | No | No | No | Yes (OpenLane) |
| Cross-level? | No | No | No | Yes (PnR→RTL) |
| Vehicle design | 8-bit processor | Benchmarks | Benchmarks | FOC coprocessor |
| Tapeout? | Yes (Sky130) | No | No | Planned (Sky130) |
| Open-source? | Yes | Partial | Varies | Yes (all artifacts) |
