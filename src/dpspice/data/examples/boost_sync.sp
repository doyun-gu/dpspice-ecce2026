* Synchronous boost converter: d = 0.6, Vin = 12 V.
* In CCM the k = 0 harmonic of V(out) is Vin / (1 - d) = 30 V, less conduction
* losses and an O(1/K) harmonic-truncation bias (the switch commutates a
* capacitor-clamped node); at the default K the DC error sits inside the
* reported ripple band.
Vin vin 0 12
L1 vin sw 100u
S1 sw 0 g 0 SWMOD
S2 sw out gb 0 SWMOD
C1 out 0 100u
Rl out 0 20
* 50 kHz gate pair: main switch ON for 60 percent of the period.
Vg g 0 PULSE(0 1 0 0 0 12u 20u)
Vgb gb 0 PULSE(1 0 0 0 0 12u 20u)
.model SWMOD SW(Ron=20m Roff=1Meg Vt=0.5)
.tran 0 5m
.end
