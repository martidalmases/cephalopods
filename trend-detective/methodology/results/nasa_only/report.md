# Variable study (Mediterranean Sea, 2003–2025, 1094 sea cells)

## 1. Trends (Sen + Mann-Kendall; cells corrected with Benjamini-Hochberg)
      variable  years  pct_cells_significant  pct_of_those_rising  regional_slope_per_decade  regional_p
           sst     23                 99.269              100.000                      0.503       0.000
    mhw_months     23                 91.865               97.114                      1.352       0.000
    dq_sea_air     23                 71.298              100.000                      0.206       0.000
          nflh     23                 81.261                0.000                     -0.008       0.000
           q2m     23                 95.247              100.000                      0.281       0.000
        precip     23                 20.018                0.000                     -0.144       0.000
sst_winter_min     23                 85.375              100.000                      0.517       0.000
sst_summer_max     23                 80.804              100.000                      0.676       0.002
           tqv     23                 80.439              100.000                      0.613       0.002
           shf     23                 36.563                1.750                     -0.896       0.005
          wind     23                  0.000                  NaN                     -0.134       0.013
    dt_sea_air     23                 38.117                3.118                     -0.055       0.017
           aod     23                  9.141                0.000                     -0.009       0.035
           chl     23                 35.558                0.771                     -0.008       0.081
           lhf     23                  0.823               88.889                      1.041       0.267
          evap     23                  0.823               88.889                      0.037       0.267
     sst_range     23                  0.000                  NaN                      0.123       0.673

## 2a. Redundancy in environmental space (cell-years)
17 variables -> 14 groups at |r| >= 0.8. PCA: 8 components explain 90 % (PC1 34%, PC2 20%).

          keep            redundant_with_it
           shf                             
           tqv                             
    dt_sea_air                             
sst_summer_max                             
    dq_sea_air                             
          wind                             
           lhf               evap (r=+1.00)
sst_winter_min sst (r=+0.87); q2m (r=+0.89)
           chl                             
        precip                             
     sst_range                             
          nflh                             
           aod                             
    mhw_months                             

Then dropped by VIF: sst_range (VIF 179335).
**13 variables left: shf, tqv, dt_sea_air, sst_summer_max, dq_sea_air, wind, lhf, sst_winter_min, chl, precip, nflh, aod, mhw_months**

VIF of those (1 = independent, > 10 = redundant):
shf               5.8
tqv               2.2
dt_sea_air        2.3
sst_summer_max    4.3
dq_sea_air        8.9
wind              2.5
lhf               3.6
sst_winter_min    4.7
chl               2.0
precip            2.8
nflh              2.2
aod               1.4
mhw_months        1.4

## 2b. Redundancy in year-to-year variation (anomalies)
17 variables -> 14 groups at |r| >= 0.8. PCA: 8 components explain 90 % (PC1 34%, PC2 21%).

          keep    redundant_with_it
           shf dt_sea_air (r=+0.86)
           lhf       evap (r=+1.00)
    dq_sea_air                     
           aod                     
sst_summer_max                     
     sst_range                     
           chl                     
          nflh                     
           sst mhw_months (r=+0.84)
           q2m                     
           tqv                     
sst_winter_min                     
          wind                     
        precip                     

Then dropped by VIF: q2m (VIF 21), sst_range (VIF 21).
**12 variables left: shf, lhf, dq_sea_air, aod, sst_summer_max, chl, nflh, sst, tqv, sst_winter_min, wind, precip**

VIF of those (1 = independent, > 10 = redundant):
shf               2.0
lhf               3.2
dq_sea_air        4.6
aod               1.4
sst_summer_max    2.5
chl               1.8
nflh              1.9
sst               7.6
tqv               2.6
sst_winter_min    2.3
wind              2.3
precip            1.4

**Kept for the species step:** shf, tqv, dt_sea_air, sst_summer_max, dq_sea_air, wind, lhf, sst_winter_min, chl, precip, nflh, aod, mhw_months

## 3. Relevance per species (temporal hold-out, permutation importance)
                                          n  auc_test  p_model                                                                   relevant                                                  indicators_of_change
species                                                                                                                                                                                                        
Acanthocardia tuberculata               387      0.62    0.010                               mhw_months, shf, dt_sea_air, wind, nflh, aod                          shf, dt_sea_air, wind, nflh, aod, mhw_months
Actinia mediterranea                    384      0.48    0.931                                                           lhf, aod, precip                                                           precip, aod
Aiptasia mutabilis                      152      0.54    0.119                   sst_winter_min, lhf, aod, nflh, sst_summer_max, chl, shf                        shf, sst_summer_max, sst_winter_min, nflh, aod
Anemonia viridis                        420      0.47    0.980                                       lhf, dt_sea_air, sst_winter_min, aod                                       dt_sea_air, sst_winter_min, aod
Anomia ephippium                        196      0.64    0.010                               aod, mhw_months, nflh, dt_sea_air, lhf, wind                               dt_sea_air, wind, nflh, aod, mhw_months
Arca noae                               387      0.69    0.010                     sst_winter_min, lhf, mhw_months, chl, wind, dt_sea_air                          dt_sea_air, wind, sst_winter_min, mhw_months
Balanophyllia (Balanophyllia) europaea  252      0.60    0.010                                          sst_winter_min, precip, wind, aod                                     wind, sst_winter_min, precip, aod
Barbatia barbata                        187      0.42    1.000                                      dt_sea_air, aod, sst_winter_min, nflh                                 dt_sea_air, sst_winter_min, nflh, aod
Caranx crysos                            98      0.67    0.010                                                                          -                                                                     -
Caulerpa cylindracea                    288      0.61    0.010                                                     precip, lhf, aod, wind                                                     wind, precip, aod
Cerastoderma glaucum                    165      0.56    0.020                        lhf, chl, shf, dt_sea_air, sst_winter_min, aod, tqv                             shf, tqv, dt_sea_air, sst_winter_min, aod
Chamelea gallina                        329      0.65    0.010  mhw_months, wind, shf, nflh, dq_sea_air, lhf, tqv, sst_summer_max, precip  shf, tqv, sst_summer_max, dq_sea_air, wind, precip, nflh, mhw_months
Chromis chromis                         185      0.60    0.010                                                                          -                                                                     -
Cladocora caespitosa                    339      0.66    0.010                                       sst_winter_min, lhf, dt_sea_air, aod                                       dt_sea_air, sst_winter_min, aod
Corallium rubrum                        237      0.76    0.010     wind, precip, aod, nflh, shf, chl, sst_summer_max, sst_winter_min, lhf          shf, sst_summer_max, wind, sst_winter_min, precip, nflh, aod
Coris julis                             204      0.48    0.733                                                                          -                                                                     -
Diplodus vulgaris                       166      0.49    0.644                                                                          -                                                                     -
Donax trunculus                         218      0.65    0.010               shf, wind, dt_sea_air, mhw_months, dq_sea_air, tqv, chl, aod               shf, tqv, dt_sea_air, dq_sea_air, wind, aod, mhw_months
Eledone cirrhosa                        303      0.83    0.010                         dq_sea_air, sst_winter_min, nflh, tqv, precip, lhf                         tqv, dq_sea_air, sst_winter_min, precip, nflh
Epinephelus marginatus                  590      0.62    0.010                             chl, tqv, aod, mhw_months, sst_summer_max, lhf                                  tqv, sst_summer_max, aod, mhw_months
Eunicella cavolini                      238      0.73    0.010       lhf, sst_winter_min, dq_sea_air, precip, nflh, dt_sea_air, wind, aod       dt_sea_air, dq_sea_air, wind, sst_winter_min, precip, nflh, aod
Eunicella singularis                    196      0.78    0.010                            dq_sea_air, lhf, aod, wind, chl, sst_winter_min                                 dq_sea_air, wind, sst_winter_min, aod
Fistularia commersonii                  403      0.79    0.010                     sst_winter_min, dq_sea_air, wind, aod, mhw_months, tqv                tqv, dq_sea_air, wind, sst_winter_min, aod, mhw_months
Illex coindetii                         262      0.74    0.010                                    dq_sea_air, nflh, precip, tqv, chl, lhf                                         tqv, dq_sea_air, precip, nflh
Leptopsammia pruvoti                    189      0.61    0.010                                 sst_winter_min, wind, nflh, sst_summer_max                            sst_summer_max, wind, sst_winter_min, nflh
Loligo vulgaris                         209      0.58    0.020                                                 dq_sea_air, nflh, lhf, tqv                                                 tqv, dq_sea_air, nflh
Mactra stultorum                        261      0.60    0.010                                          mhw_months, shf, wind, dt_sea_air                                     shf, dt_sea_air, wind, mhw_months
Mimachlamys varia                       187      0.63    0.010                                   lhf, nflh, tqv, shf, aod, sst_winter_min                                   shf, tqv, sst_winter_min, nflh, aod
Mytilus galloprovincialis               192      0.64    0.010  sst_winter_min, mhw_months, dt_sea_air, dq_sea_air, lhf, precip, shf, chl       shf, dt_sea_air, dq_sea_air, sst_winter_min, precip, mhw_months
Octopus vulgaris                        720      0.55    0.010                                                   lhf, sst_winter_min, shf                                                   shf, sst_winter_min
Paramuricea clavata                     291      0.74    0.010                                            sst_winter_min, wind, aod, nflh                                       wind, sst_winter_min, nflh, aod
Parazoanthus axinellae                  274      0.71    0.010                lhf, sst_winter_min, wind, precip, sst_summer_max, aod, chl                     sst_summer_max, wind, sst_winter_min, precip, aod
Pinna nobilis                           383      0.64    0.010                lhf, aod, sst_winter_min, precip, wind, chl, sst_summer_max                     sst_summer_max, wind, sst_winter_min, precip, aod
Posidonia oceanica                      687      0.61    0.010                                       aod, lhf, dq_sea_air, tqv, nflh, shf                                       shf, tqv, dq_sea_air, nflh, aod
Sarpa salpa                             208      0.52    0.238                                                                          -                                                                     -
Sepia officinalis                       562      0.57    0.010                            lhf, shf, mhw_months, nflh, sst_summer_max, tqv                            shf, tqv, sst_summer_max, nflh, mhw_months
Serranus scriba                         169      0.56    0.010                                                                          -                                                                     -
Sparisoma cretense                      280      0.55    0.010                            lhf, sst_winter_min, dq_sea_air, nflh, aod, chl                                 dq_sea_air, sst_winter_min, nflh, aod
Sphyraena viridensis                    177      0.63    0.010                                   dq_sea_air, mhw_months, lhf, aod, precip                                   dq_sea_air, precip, aod, mhw_months
Spondylus gaederopus                    180      0.61    0.010                                                  mhw_months, lhf, shf, chl                                                       shf, mhw_months
Thalassoma pavo                         750      0.54    0.010                                                       chl, dq_sea_air, shf                                                       shf, dq_sea_air
Venus verrucosa                         206      0.62    0.010                              shf, sst_winter_min, chl, tqv, aod, wind, lhf                                   shf, tqv, wind, sst_winter_min, aod

relevant = shuffling it lowers the AUC on the held-out later years (q < 0.05).
indicators_of_change = relevant AND the variable has a significant regional trend.
Trust a row only if p_model < 0.05: otherwise the model does not beat chance on the later years.

### Which variables carry the weight (35 species with a valid model)
                species_relevant  median_importance  mean_share  cumulative_share
variable                                                                         
lhf                           24             0.0238      0.1434            0.1434
sst_winter_min                18             0.0135      0.1357            0.2792
dq_sea_air                    13             0.0121      0.1148            0.3940
mhw_months                    12             0.0042      0.0984            0.4924
aod                           20             0.0094      0.0732            0.5656
shf                           14             0.0069      0.0680            0.6336
wind                          17             0.0089      0.0623            0.6959
nflh                          14             0.0059      0.0604            0.7562
tqv                           12             0.0070      0.0598            0.8160
precip                        11             0.0053      0.0575            0.8735
chl                           14             0.0093      0.0554            0.9289
dt_sea_air                     9             0.0049      0.0417            0.9705
sst_summer_max                 7             0.0015      0.0295            1.0000

**Minimal set (70 % of the importance): lhf, sst_winter_min, dq_sea_air, mhw_months, aod, shf, wind, nflh**
Of those, with a significant regional trend as well: sst_winter_min, dq_sea_air, mhw_months, aod, shf, wind, nflh
Everything relevant anywhere (the widest set): aod, chl, dq_sea_air, dt_sea_air, lhf, mhw_months, nflh, precip, shf, sst_summer_max, sst_winter_min, tqv, wind
