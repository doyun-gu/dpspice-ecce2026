* Full-bridge series resonant converter at the ECCE Table I tank values.
* Diagonal switch pairs share a gate; the tank floats between legs a and b.
Vdc vdc 0 100
S1 vdc a g 0 SWMOD
S2 a 0 gb 0 SWMOD
S3 vdc b gb 0 SWMOD
S4 b 0 g 0 SWMOD
Rs a n1 3
Ls n1 n2 100.04u
Cs n2 b 30.07n
Rload n2 b 2k
* 92.3 kHz square-wave excitation, 50 percent duty.
Vg g 0 PULSE(0 1 0 0 0 5.417u 10.834u)
Vgb gb 0 PULSE(1 0 0 0 0 5.417u 10.834u)
.model SWMOD SW(Ron=1m Roff=1Meg Vt=0.5)
.tran 0 1m
.end
