# Closed-form O(1/K) DC truncation bias of the LTP solve

Derivation and validation of the analytic bias correction shipped as
`solve_ltp(..., bias_correction=True)` (CLI `--bias-correction`). The
correction is parameter-free: every quantity in it comes from the netlist,
the gate spectrum, or the truncated solution itself. Validation reuses the
sweep numbers already recorded in `VALIDATION_REPORT.md` §1/§7 — no new
reference solver runs were needed beyond Richardson limits of the LTP solve
itself.

## 1. Where the truncation error lives

The LTP operator assembles the switch conductance as a Toeplitz block
`Toeplitz(G_k) ⊗ P` with the gate coefficients kept out to `Kc = 2K`
(`SwitchedHBNet.switch_block`). Inside the retained window |k| ≤ K the block
is therefore **exact**: no coefficient of g(t) is missing. The only
truncation error is the discarded coupling to response harmonics beyond the
window. The exact branch current of switch s at harmonic k is

    I_k = Σ_m  G_{k−m} V_m        (over all m)

and the truncated solve keeps only |m| ≤ K. Both g(t) and the switched
branch voltage have O(1/|m|) spectra (each is discontinuous at the gate
edges), so the dropped products decay like 1/m² — summable, with an O(1/K)
total. That is the observed first-order DC convergence (§7, Finding F2).

## 2. The dropped k = 0 branch current in closed form

Write the unit gate spectrum (gate.py, exact):

    S_0 = d,   S_k = e^{−jπkd} sin(πkd)/(πk) · e^{−2πjk·phase}   (k ≠ 0).

Behind a gated switch the branch voltage is, to leading order, **chopped
between two quasi-constant levels**: v_on while the switch conducts and
v_off while it blocks (the fast structure of the waveform is the gate
edges; the intra-interval ripple is smooth and its spectrum decays a full
order faster). So for |m| > K,

    V_m ≈ (v_on − v_off) · S_m.

The conductance tail is G_{−m} = Δg · S_{−m} with Δg = g_on − g_off, and the
dropped DC branch current is

    D0 = Σ_{|m|>K} G_{−m} V_m
       = Δg (v_on − v_off) Σ_{|m|>K} S_{−m} S_m
       = Δg (v_on − v_off) · 2 Σ_{m>K} |S_m|²          (S_{−m} = conj S_m)
       = Δg (v_on − v_off) · tail(K, d),

    tail(K, d) = 2 Σ_{m>K} sin²(πmd) / (πm)².

The phase factors cancel identically (|S_m| is phase-independent) and D0 is
real — the dropped tail is a pure DC defect per switch, of one sign.

**The tail is a finite closed form.** By Parseval the unit gate has power d
and DC power d², so its total two-sided AC power is d(1−d):

    tail(K, d) = d(1−d) − 2 Σ_{m=1}^{K} sin²(πmd) / (πm)².

This is exact (checked against a 2·10⁶-term partial sum to 4·10⁻¹² relative
over K ∈ {5..400}, d ∈ {0.2, 0.5, 0.8}) and is what `gate_tail(K, duty)`
ships. Since sin² averages ½, tail(K, d) → 1/(π²K): the O(1/K) law, with
the duty dependence carried by the partial sum.

## 3. Embedding and the corrected solve

D0 is a branch current the truncated KCL rows never see. Re-inserting it at
k = 0 through the switch incidence (−D0 into the n_pos row, +D0 into the
n_neg row, vector δ) and pushing it through the **same truncated operator**
gives the first-order correction

    X_corr = X_trunc − Y_K⁻¹ δ̃,     δ̃ = [0, …, δ at the k = 0 block, …, 0].

Because δ̃ passes through the full Y_K, the correction lands on every
retained harmonic, not just DC — consistent with the observation (report
§2, boost_sync) that the finite-K error contaminates the whole spectrum
with the same O(1/K) order.

**Self-consistency.** v_on and v_off are the mean branch voltages over the
ON/OFF intervals of the solution — but the truncated solution's means are
themselves biased by the very defect being corrected. The shipped
implementation therefore iterates the correction to a fixed point,
re-measuring (v_on, v_off) from the corrected reconstruction each pass.
The map is linearly contractive (contraction ratio ≈ tail·Δg·R_thevenin,
well below 1 for any converter this path solves); 5–13 iterations reach
1e-10 relative in the sweeps below. No step introduces a fitted constant.

The corrected solution no longer satisfies the plain truncated system (by
construction — it satisfies the tail-augmented one), so `solve_ltp` reports
the residual against Y_K X + B + δ̃ and labels the route `ltp+bias`.

## 4. Structural buck immunity

A complementary pair sharing the switched node (buck: S1 vin–sw, S2 sw–0,
complementary gates) produces two tails that cancel there. The pair sees
the same branch-voltage swing with opposite sign conventions and
complementary gates, so the two D0 contributions at the shared node are
equal and opposite; the residual single-ended defects land on the source
node (held stiff by the voltage source) and on ground. The prediction is an
**exact structural zero**, not a small number — and that is what both the
prototype (|ΔV(out)| = 4·10⁻¹⁶) and the shipped test
(`test_bias_correction_buck_is_structural_zero`) measure, matching the §1
matrix where the buck shows conduction loss only at every duty. Boost and
buck-boost have S2 in series between the switched node and the output, so
S2's tail lands on `out` — the visible bias.

## 5. Validation against the recorded sweeps

All numbers below come through the shipped path
(`dpspice.solve_hb(..., bias_correction=True)`). "Predicted" is the
correction the closed form applies; "measured" is the recorded truncation
error from the report sweeps.

**K sweep** (§7 boost, d = 0.6, measured against the Richardson limit
29.807 V — acceptance band ±10%):

| K | measured bias (V) | predicted (V) | ratio |
|---|---|---|---|
| 5 | 3.34 | 3.042 | 0.911 |
| 10 | 1.78 | 1.701 | 0.956 |
| 20 | 0.91 | 0.895 | 0.984 |
| 40 | 0.46 | 0.458 | 0.996 |
| 80 | 0.23 | 0.232 | 1.008 |
| 160 | 0.12 | 0.117 | 0.971 |

**Duty sweep** (K = 15 boost). One caveat first: the §1 matrix quotes DC
error against the **lossless** ratio Vin/(1−d), which conflates the
truncation bias with the genuine Ron conduction drop the converter really
has. The correction targets (and should remove) only the former, so the
honest reference per duty is the Richardson limit 2·V(400) − V(200) of the
LTP solve itself:

| d | Richardson ref (V) | lossless (V) | plain K=15 (V) | true bias (V) | predicted (V) | ratio | residual after correction (V) |
|---|---|---|---|---|---|---|---|
| 0.2 | 14.975 | 15.000 | 14.819 | 0.156 | 0.148 | 0.948 | +0.008 |
| 0.3 | 17.105 | 17.143 | 16.884 | 0.221 | 0.212 | 0.960 | +0.009 |
| 0.5 | 23.899 | 24.000 | 23.305 | 0.594 | 0.578 | 0.972 | +0.016 |
| 0.7 | 39.554 | 40.000 | 36.969 | 2.585 | 2.516 | 0.973 | +0.069 |
| 0.8 | 58.527 | 60.000 | 50.268 | 8.260 | 7.878 | 0.954 | +0.381 |

(The ref-minus-lossless column gap is the conduction drop; the report's
duty-sweep "errors" 0.18/0.26/0.69/3.03/9.73 are the sum of that drop and
the true bias, which is why the correction must not be judged against
them.)

**Buck** (K = 15): predicted correction exactly zero (|ΔV(out)| ≤ 1e-12
relative, machine-level in the prototype), as §4 requires structurally.

Both sweeps sit inside the ~10% acceptance band; the worst point (K = 5,
91.1%) is where the "two-level branch voltage" model is weakest because the
retained window is only ±5 harmonics. The residual after correction is the
second-order remainder: at d = 0.8 it is 0.38 V where the uncorrected error
was 8.26 V, a 22× reduction, and it shrinks quadratically in the K sweep
(0.30 V at K = 5, 0.019 V at K = 20).

## 6. What the correction is not

- It is **not** exact: the two-level branch-voltage model drops the
  intra-interval ripple's contribution to the tail, which is the observed
  ≤5% shortfall and the O(1/K²) residual.
- It does **not** apply to the hybrid NR-HB path: a diode's conduction
  interval is solved for, not gated, so its truncation defect is not the
  closed-form gate tail. The dispatcher refuses the flag there rather than
  silently ignoring it.
- It is **off by default**: the plain truncated solve remains the shipped
  baseline, and corrected results are labelled `ltp+bias` end to end.
