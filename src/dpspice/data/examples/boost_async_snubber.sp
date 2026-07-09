* Asynchronous boost converter with a 150 nF switch-node snubber.
* Same converter as boost_async.sp; the snubber slows the switch-node
* transition to a harmonic-resolvable ramp. It is a deliberate circuit
* modification, not a solver aid: the extra capacitance injects charge at the
* switch node and SHIFTS THE OPERATING POINT, raising V(out) above the bare
* converter (switched-DAE transient references: 31.094 V snubbered vs
* 29.315 V bare, validation/td_boost_async.py). The hybrid DC converges
* first-order in K from above toward the snubbered reference; where plain
* Newton stalls the solver engages source/Gmin continuation automatically.
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
