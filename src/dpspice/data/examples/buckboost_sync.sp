* Synchronous inverting buck-boost: d = 0.5, Vin = 12 V.
* The averaged model gives V(out) k = 0 = -d * Vin / (1 - d) = -12 V; the
* sync switch S2 commutates the output capacitor node, so the DC component
* carries the same O(1/K) harmonic-truncation bias as the boost, kept inside
* the reported ripple band by the switch on-resistance.
Vin vin 0 12
S1 vin sw g 0 SWMOD
S2 sw out gb 0 SWMOD
L1 sw 0 100u
C1 out 0 100u
Rl out 0 20
* 50 kHz complementary gate pair, main switch ON 50 percent of the period.
Vg g 0 PULSE(0 1 0 0 0 10u 20u)
Vgb gb 0 PULSE(1 0 0 0 0 10u 20u)
.model SWMOD SW(Ron=20m Roff=1Meg Vt=0.5)
.tran 0 5m
.end
