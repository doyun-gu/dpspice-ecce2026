* Synchronous buck converter: two complementary gated switches, LTP path.
* d = 0.4, Vin = 12 V -> ideal V(out) DC component = 4.8 V (minus Ron losses).
Vin vin 0 12
S1 vin sw g 0 SWMOD
S2 sw 0 gb 0 SWMOD
L1 sw out 100u
C1 out 0 47u
Rl out 0 5
* 50 kHz gate pair: Vgb is the complement of Vg (inverted PULSE levels).
Vg g 0 PULSE(0 1 0 0 0 8u 20u)
Vgb gb 0 PULSE(1 0 0 0 0 8u 20u)
.model SWMOD SW(Ron=1m Roff=1Meg Vt=0.5)
.tran 0 2m
.end
