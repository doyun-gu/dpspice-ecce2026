* DPSpice cross-check: half-wave rectifier, RC-SMOOTHED, STRONG DCM (HEADLINE)
* R=1k, C=100uF -> RC = 100 ms = 5 periods. Narrow conduction angle (~48 deg), SOLVED.
* Diode model MUST match device.ShockleyDiode(Is=1e-9, n=1.0): Is=1e-9 N=1 Rs=0 Cjo=0
V1 in 0 SINE(0 5 50)
D1 in out Dmod
R1 out 0 1k
C1 out 0 100u
.model Dmod D(Is=1e-9 N=1 Rs=0 Cjo=0)
* run 1.0 s (50 cycles settling), save the LAST 40 ms (2 cycles), 1 us max step.
* uic NOT used: start from rest and let it settle to steady state.
.tran 0 1.0 0.96 1u
.options plotwinsize=0 numdgt=7
.backanno
.end
