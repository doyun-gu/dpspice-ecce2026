* Series RLC resonant circuit (Rim et al. 2025 benchmark)
* Linear -> IDP single-shift transient is auto-selected.
V1 N001 0 SINE(0 1 92.3k)
R1 N001 N002 3.0
L1 N002 N003 100.04u
C1 N003 0 30.07n
R2 N003 0 2k
.tran 0 0.2m
.end
