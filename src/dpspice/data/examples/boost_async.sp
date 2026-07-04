* Asynchronous boost converter: gated switch + real diode, hybrid NR-HB path.
* The switch block is constant, Newton iterates on the diode only. The 150 nF
* snubber slows the switch-node transition to a harmonic-resolvable ramp; it is
* a deliberate circuit modification, not just a solver aid. It injects charge at
* the switch node and raises V(out) above the bare Vin / (1 - d), so the DC
* here differs from the un-snubbered converter. Convergence of the hybrid path
* on this hard-switched diode node is not monotone in K (the truncated switch
* node chatters across the junction threshold): this example is validated at
* K = 20, and raising the harmonic count may stall the Newton iteration, which
* is reported as a loud error rather than a silent wrong answer.
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
