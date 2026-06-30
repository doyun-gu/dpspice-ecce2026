* Half-wave diode rectifier (resistive load)
* Nonlinear diode -> harmonic-balance is auto-selected.
V1 in 0 SINE(0 5 50)
D1 in out Dmod
R1 out 0 1k
.model Dmod D(Is=1e-9 N=1)
.tran 0 0.6 0.56 1u
.end
