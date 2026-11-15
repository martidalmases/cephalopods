# Variable study (Mediterranean Sea, 2003–2025, 1094 sea cells)

## 1. Trends (Sen + Mann-Kendall; cells corrected with Benjamini-Hochberg)
      variable  years  pct_cells_significant  pct_of_those_rising  regional_slope_per_decade  regional_p
            ph     23                 92.962                0.000                     -0.021       0.000
           sst     23                 99.269              100.000                      0.503       0.000
           ssh     23                 82.358               99.556                      0.033       0.000
    mhw_months     23                 91.865               97.114                      1.352       0.000
    dq_sea_air     23                 71.298              100.000                      0.206       0.000
          nflh     23                 81.261                0.000                     -0.008       0.000
           q2m     23                 95.247              100.000                      0.281       0.000
        precip     23                 20.018                0.000                     -0.144       0.000
           sss     23                 22.212               97.119                      0.110       0.000
sst_winter_min     23                 85.375              100.000                      0.517       0.000
           no3     23                 43.144                3.178                     -0.037       0.000
           tqv     23                 80.439              100.000                      0.613       0.002
sst_summer_max     23                 80.804              100.000                      0.676       0.002
           shf     23                 36.563                1.750                     -0.896       0.005
          wind     23                  0.000                  NaN                     -0.134       0.013
    dt_sea_air     23                 38.117                3.118                     -0.055       0.017
           aod     23                  9.141                0.000                     -0.009       0.035
           chl     23                 35.558                0.771                     -0.008       0.081
          evap     23                  0.823               88.889                      0.037       0.267
           lhf     23                  0.823               88.889                      1.041       0.267
     sst_range     23                  0.000                  NaN                      0.123       0.673
            o2     23                  2.102               82.609                      0.136       0.712

## 2a. Redundancy in environmental space (cell-years)
22 variables -> 18 groups at |r| >= 0.8. PCA: 10 components explain 90 % (PC1 35%, PC2 15%).

          keep            redundant_with_it
           ssh                             
            ph                             
           tqv                             
    mhw_months                             
           shf                             
     sst_range                             
sst_summer_max                             
    dq_sea_air                             
          wind                             
           lhf               evap (r=+1.00)
           sst                 o2 (r=-0.88)
sst_winter_min chl (r=-0.83); q2m (r=+0.89)
        precip                             
           no3                             
          nflh                             
           sss                             
    dt_sea_air                             
           aod                             

Then dropped by VIF: dq_sea_air (VIF 10), sst_range (VIF 156395), sst (VIF 32).
**15 variables left: ssh, ph, tqv, mhw_months, shf, sst_summer_max, wind, lhf, sst_winter_min, precip, no3, nflh, sss, dt_sea_air, aod**

VIF of those (1 = independent, > 10 = redundant):
ssh               3.4
ph                3.1
tqv               2.8
mhw_months        1.4
shf               3.0
sst_summer_max    2.9
wind              2.2
lhf               4.2
sst_winter_min    5.4
precip            3.3
no3               1.6
nflh              1.5
sss               2.7
dt_sea_air        2.6
aod               1.8

## 2b. Redundancy in year-to-year variation (anomalies)
22 variables -> 19 groups at |r| >= 0.8. PCA: 11 components explain 90 % (PC1 33%, PC2 17%).

          keep    redundant_with_it
        precip                     
           sss                     
           shf dt_sea_air (r=+0.86)
           lhf       evap (r=+1.00)
    dq_sea_air                     
           aod                     
sst_summer_max                     
     sst_range                     
           sst         ph (r=-0.84)
    mhw_months                     
           q2m                     
           ssh                     
           tqv                     
sst_winter_min                     
           chl                     
          nflh                     
          wind                     
           no3                     
            o2                     

Then dropped by VIF: q2m (VIF 24), sst_range (VIF 22), sst (VIF 12).
**16 variables left: precip, sss, shf, lhf, dq_sea_air, aod, sst_summer_max, mhw_months, ssh, tqv, sst_winter_min, chl, nflh, wind, no3, o2**

VIF of those (1 = independent, > 10 = redundant):
precip            1.5
sss               1.3
shf               2.1
lhf               3.7
dq_sea_air        4.1
aod               1.4
sst_summer_max    2.1
mhw_months        2.6
ssh               2.6
tqv               2.5
sst_winter_min    2.6
chl               1.8
nflh              1.9
wind              2.4
no3               1.4
o2                1.2

**Kept for the species step:** ssh, ph, tqv, mhw_months, shf, sst_summer_max, wind, lhf, sst_winter_min, precip, no3, nflh, sss, dt_sea_air, aod

## 3. Relevance per species (temporal hold-out, permutation importance)
                                          n  auc_test  p_model                                                                        relevant                                                           indicators_of_change
species                                                                                                                                                                                                                      
Acanthocardia tuberculata               374      0.66    0.010                                 mhw_months, shf, ph, wind, ssh, aod, dt_sea_air                                ssh, ph, mhw_months, shf, wind, dt_sea_air, aod
Actinia mediterranea                    374      0.52    0.188                                                           lhf, aod, ssh, precip                                                               ssh, precip, aod
Aiptasia mutabilis                      147      0.58    0.010  sss, ssh, lhf, sst_winter_min, dt_sea_air, aod, sst_summer_max, shf, nflh, no3      ssh, shf, sst_summer_max, sst_winter_min, no3, nflh, sss, dt_sea_air, aod
Anemonia viridis                        407      0.48    0.950                                                   lhf, sst_winter_min, aod, ssh                                                       ssh, sst_winter_min, aod
Anomia ephippium                        183      0.59    0.010                                                   lhf, ph, aod, dt_sea_air, ssh                                                       ssh, ph, dt_sea_air, aod
Arca noae                               367      0.69    0.010                                            sst_winter_min, lhf, mhw_months, ssh                                                ssh, mhw_months, sst_winter_min
Balanophyllia (Balanophyllia) europaea  239      0.66    0.010                                        sst_winter_min, sss, ssh, aod, tqv, wind                                       ssh, tqv, wind, sst_winter_min, sss, aod
Barbatia barbata                        181      0.43    1.000                                                                               -                                                                              -
Caranx crysos                            91      0.66    0.010                                            sst_winter_min, sst_summer_max, wind                                           sst_summer_max, wind, sst_winter_min
Caulerpa cylindracea                    281      0.60    0.010                              sst_winter_min, aod, lhf, precip, dt_sea_air, wind                                  wind, sst_winter_min, precip, dt_sea_air, aod
Cerastoderma glaucum                    154      0.52    0.188                                 lhf, shf, sst_winter_min, no3, dt_sea_air, nflh                                     shf, sst_winter_min, no3, nflh, dt_sea_air
Chamelea gallina                        315      0.69    0.010                       mhw_months, ph, no3, wind, shf, lhf, tqv, dt_sea_air, aod                           ph, tqv, mhw_months, shf, wind, no3, dt_sea_air, aod
Chromis chromis                         177      0.64    0.010                                                     sst_winter_min, ssh, precip                                                    ssh, sst_winter_min, precip
Cladocora caespitosa                    327      0.68    0.010                                                        sst_winter_min, lhf, aod                                                            sst_winter_min, aod
Corallium rubrum                        234      0.79    0.010           sss, precip, sst_summer_max, ph, sst_winter_min, nflh, wind, shf, no3          ph, shf, sst_summer_max, wind, sst_winter_min, precip, no3, nflh, sss
Coris julis                             196      0.59    0.010                                                   sst_winter_min, sss, tqv, aod                                                  tqv, sst_winter_min, sss, aod
Diplodus vulgaris                       158      0.51    0.545                                                                               -                                                                              -
Donax trunculus                         211      0.65    0.010   dt_sea_air, wind, mhw_months, shf, precip, tqv, no3, ssh, sst_winter_min, aod  ssh, tqv, mhw_months, shf, wind, sst_winter_min, precip, no3, dt_sea_air, aod
Eledone cirrhosa                        291      0.78    0.010                                                      sss, ssh, tqv, precip, no3                                                     ssh, tqv, precip, no3, sss
Epinephelus marginatus                  579      0.61    0.010                                                             nflh, sss, tqv, lhf                                                                 tqv, nflh, sss
Eunicella cavolini                      234      0.73    0.010                               sst_winter_min, sss, lhf, precip, dt_sea_air, aod                                   sst_winter_min, precip, sss, dt_sea_air, aod
Eunicella singularis                    184      0.83    0.010                                                       sss, sst_winter_min, wind                                                      wind, sst_winter_min, sss
Fistularia commersonii                  380      0.81    0.010                   sst_winter_min, sss, tqv, aod, shf, wind, ssh, ph, mhw_months                  ssh, ph, tqv, mhw_months, shf, wind, sst_winter_min, sss, aod
Illex coindetii                         250      0.59    0.040                                         sss, tqv, ssh, nflh, precip, dt_sea_air                                        ssh, tqv, precip, nflh, sss, dt_sea_air
Leptopsammia pruvoti                    185      0.65    0.010                                       sss, sst_winter_min, sst_summer_max, wind                                      sst_summer_max, wind, sst_winter_min, sss
Loligo vulgaris                         194      0.55    0.079                                                                  sss, tqv, nflh                                                                 tqv, nflh, sss
Mactra stultorum                        238      0.60    0.010                mhw_months, dt_sea_air, sst_winter_min, wind, shf, no3, sss, aod               mhw_months, shf, wind, sst_winter_min, no3, sss, dt_sea_air, aod
Mimachlamys varia                       176      0.62    0.010                                                   nflh, lhf, shf, sss, tqv, aod                                                       tqv, shf, nflh, sss, aod
Mytilus galloprovincialis               180      0.61    0.010               ph, mhw_months, lhf, sst_winter_min, no3, dt_sea_air, shf, precip                   ph, mhw_months, shf, sst_winter_min, precip, no3, dt_sea_air
Octopus vulgaris                        696      0.56    0.010                                                                               -                                                                              -
Paramuricea clavata                     287      0.77    0.010                        sst_winter_min, sss, wind, precip, shf, dt_sea_air, nflh                       shf, wind, sst_winter_min, precip, nflh, sss, dt_sea_air
Parazoanthus axinellae                  267      0.76    0.010            sst_winter_min, lhf, sss, sst_summer_max, wind, precip, no3, ph, aod                ph, sst_summer_max, wind, sst_winter_min, precip, no3, sss, aod
Pinna nobilis                           368      0.69    0.010                             sst_winter_min, lhf, sss, aod, sst_summer_max, wind                                 sst_summer_max, wind, sst_winter_min, sss, aod
Posidonia oceanica                      664      0.64    0.010                                          lhf, aod, shf, sss, tqv, nflh, ph, ssh                                              ssh, ph, tqv, shf, nflh, sss, aod
Sarpa salpa                             200      0.56    0.010                                                                               -                                                                              -
Sepia officinalis                       539      0.58    0.010                                             lhf, shf, sss, mhw_months, nflh, ph                                                 ph, mhw_months, shf, nflh, sss
Serranus scriba                         163      0.59    0.010                                                                               -                                                                              -
Sparisoma cretense                      278      0.59    0.010                        sss, lhf, ph, ssh, no3, sst_winter_min, aod, precip, shf                            ssh, ph, shf, sst_winter_min, precip, no3, sss, aod
Sphyraena viridensis                    168      0.67    0.010                                      sss, mhw_months, ssh, lhf, no3, ph, precip                                          ssh, ph, mhw_months, precip, no3, sss
Spondylus gaederopus                    175      0.59    0.010                                                                               -                                                                              -
Thalassoma pavo                         725      0.60    0.010                                                    sss, ssh, aod, ph, lhf, nflh                                                        ssh, ph, nflh, sss, aod
Venus verrucosa                         196      0.62    0.010                                shf, mhw_months, ssh, sst_winter_min, dt_sea_air                               ssh, mhw_months, shf, sst_winter_min, dt_sea_air

relevant = shuffling it lowers the AUC on the held-out later years (q < 0.05).
indicators_of_change = relevant AND the variable has a significant regional trend.
Trust a row only if p_model < 0.05: otherwise the model does not beat chance on the later years.

### Which variables carry the weight (36 species with a valid model)
                species_relevant  median_importance  mean_share  cumulative_share
variable                                                                         
sst_winter_min                21             0.0280      0.1726            0.1726
sss                           21             0.0272      0.1511            0.3237
lhf                           17             0.0130      0.0929            0.4167
mhw_months                    10             0.0079      0.0844            0.5011
ssh                           15             0.0108      0.0721            0.5732
shf                           14             0.0068      0.0688            0.6420
tqv                           10             0.0083      0.0583            0.7003
aod                           18             0.0074      0.0525            0.7528
ph                            12             0.0045      0.0518            0.8046
nflh                           9             0.0033      0.0386            0.8432
wind                          14             0.0044      0.0359            0.8791
sst_summer_max                 6             0.0021      0.0317            0.9108
no3                           10             0.0029      0.0301            0.9410
dt_sea_air                    12             0.0031      0.0296            0.9705
precip                        12             0.0016      0.0295            1.0000

**Minimal set (70 % of the importance): sst_winter_min, sss, lhf, mhw_months, ssh, shf, tqv**
Of those, with a significant regional trend as well: sst_winter_min, sss, mhw_months, ssh, shf, tqv
Everything relevant anywhere (the widest set): aod, dt_sea_air, lhf, mhw_months, nflh, no3, ph, precip, shf, ssh, sss, sst_summer_max, sst_winter_min, tqv, wind
