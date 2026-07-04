* Asynchronous boost converter: gated switch + real diode, hybrid NR-HB path.
* The switch block is constant, Newton iterates on the diode only. This is the
* bare converter: no snubber, the switch node commutates hard. Plain Newton is
* K-fragile on this node (it may stall at one harmonic count and converge at a
* neighbouring one); when it stalls, the solver engages source/Gmin
* continuation automatically and reports the rungs it took in the run record.
* The DC output converges first-order in K from above toward the switched-DAE
* transient reference 29.315 V (validation/td_boost_async.py); at the shipped
* K = 20 it reads about 32.4 V, so validate any operating point against a
* transient reference. A snubbered variant with a shifted operating point is
* boost_async_snubber.sp.
Vin vin 0 12
L1 vin sw 100u
S1 sw 0 g 0 SWMOD
D1 sw out DMOD
C1 out 0 100u
Rl out 0 20
Vg g 0 PULSE(0 1 0 0 0 12u 20u)
.model SWMOD SW(Ron=20m Roff=1Meg Vt=0.5)
.model DMOD D(Is=1e-9 N=1)
.tran 0 5m
.end
