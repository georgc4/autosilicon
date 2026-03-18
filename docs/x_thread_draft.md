# X Thread Draft — AutoSilicon

*Review and edit before posting. Adjust tone to your voice.*

---

**1/9 — Hook**

What happens when you let an LLM run 19 synthesis experiments on a chip design overnight?

It replaced hardware multipliers with shift-add chains, eliminated dead pipeline registers, and shared comparators across datapaths.

11% smaller. 10% faster. All functional tests passing.

---

**2/9 — The insight**

RTL optimization is different from RTL generation.

The LLM doesn't need to design a chip from scratch. It needs to find non-obvious improvements to working code — the same tedious work hardware engineers do after every synthesis report.

Constant multiplier decomposition. Resource sharing. Bit-width narrowing.

---

**3/9 — How it works**

AutoSilicon: an autonomous loop inspired by @karpathy's autoresearch.

1. LLM reads RTL + synthesis history
2. Proposes one micro-architectural change
3. Git commit → lint → test → Yosys synthesis (Sky130)
4. If tests pass AND Pareto-improving (area + Fmax): keep
5. Otherwise: auto-revert
6. Repeat

The LLM sees what worked and what didn't. Each step builds on the last.

---

**4/9 — Concrete example**

The LLM found that multiplying by PWM_MAX (= 2^10 - 1 = 1023) doesn't need a hardware multiplier.

Since x * 1023 = (x << 10) - x, it replaced three 16x11-bit multipliers with shift-subtract.

Result: 5.4% area reduction in one commit, zero precision loss.

[attach diff screenshot]

---

**5/9 — Safety matters**

68% of proposals were kept. 32% were caught and reverted:

- 3 weren't Pareto-improving (traded area for speed badly)
- 2 broke synthesis metrics
- 1 failed functional tests

The system never ships a regression. Scope enforcement prevents the LLM from touching testbenches or build scripts.

---

**6/9 — Results**

FOC motor controller on Sky130 (a real BLDC/PMSM control coprocessor):

Before: 215K um2, 171 MHz
After:  191K um2, 187 MHz

That's 24K um2 saved and 16 MHz gained, autonomously, overnight.

[attach Pareto frontier chart]

---

**7/9 — The honest question**

Is iterative optimization genuinely better than just asking the LLM 19 times independently and picking the best result?

I'm running that experiment now. Same compute budget, iterative vs. best-of-N.

If feedback doesn't help, it's just expensive brute force. If it does — compounding improvements are real.

---

**8/9 — Open source**

AutoSilicon is fully open-source. Bring your own design:

1. Drop your SystemVerilog + Makefile + testbench in a directory
2. Run the harness
3. Get a Pareto frontier of optimized designs

Works with Claude Code CLI or Codex. Apache 2.0.

github.com/[your-username]/autosilicon

---

**9/9 — What's next**

- Running against established RTL benchmarks for external validation
- Comparing Claude vs. GPT on the same optimization tasks
- Targeting ICCAD 2026 ("Agentic AI for synthesis" track)

If you work on chip design and want to try this on your own RTL, DM me.

---

## Notes for posting

- Attach: Pareto frontier screenshot from dashboard, diff screenshot of the shift-subtract optimization
- Consider a short screen recording of the dashboard updating
- Tag: @matthewvenn @karpathy @FOSSiFdn
- Hashtags: #OpenSourceSilicon #ChipDesign #AI #EDA
- Post morning US Pacific time for maximum chip-Twitter visibility
- Thread should be a proper thread (reply chain), not individual tweets
