* Mixed netlist: gated switch feeding a diode rectifier, hybrid NR-HB path.
* Rmid keeps the mid node in the linear MNA once switch and diode are lifted.
Vs in 0 SINE(0 10 50)
S1 in mid g 0 SWMOD
Rmid mid 0 1e9
D1 mid out DMOD
C1 out 0 100u
Rl out 0 1k
Vg g 0 PULSE(0 1 0 0 0 5m 20m)
.model SWMOD SW(Ron=1m Roff=1Meg Vt=0.5)
.model DMOD D(Is=1e-9 N=1)
.tran 0 0.2
.end
