* DPSpice cross-check: half-wave rectifier, RC-SMOOTHED, MILD (wider conduction)
* R=1k, C=10uF -> RC = 10 ms = 0.5 period. Wider conduction angle (~102 deg), more ripple.
* Shows the conduction angle MOVING with parameters (proof it is solved, not fixed).
* Diode model MUST match device.ShockleyDiode(Is=1e-9, n=1.0): Is=1e-9 N=1 Rs=0 Cjo=0
V1 in 0 SINE(0 5 50)
D1 in out Dmod
R1 out 0 1k
C1 out 0 10u
.model Dmod D(Is=1e-9 N=1 Rs=0 Cjo=0)
* run 0.6 s (30 cycles settling), save the LAST 40 ms (2 cycles), 1 us max step.
* uic NOT used: start from rest and let it settle to steady state.
.tran 0 0.6 0.56 1u
.options plotwinsize=0 numdgt=7
.backanno
.end
