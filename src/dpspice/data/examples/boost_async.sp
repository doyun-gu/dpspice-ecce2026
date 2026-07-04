* Asynchronous boost converter: gated switch + real diode, hybrid NR-HB path.
* The switch block is constant; Newton iterates on the diode only. The 150 nF
* snubber slows the switch-node transition to a harmonic-resolvable ramp so
* the diode commutates smoothly (quasi-square-wave operation); without it the
* Gibbs ringing of the truncated switch node chatters across the junction
* threshold and Newton stalls. The ramp raises V(out) above Vin / (1 - d).
Vin vin 0 12
L1 vin sw 100u
S1 sw 0 g 0 SWMOD
D1 sw out DMOD
Csn sw 0 150n
C1 out 0 100u
Rl out 0 20
Vg g 0 PULSE(0 1 0 0 0 12u 20u)
.model SWMOD SW(Ron=20m Roff=1Meg Vt=0.5)
.model DMOD D(Is=1e-9 N=1)
.tran 0 5m
.end
