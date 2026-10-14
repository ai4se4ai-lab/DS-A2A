# Results — agentm2m-devbench run am-20261014-012059

Collected 2026-10-14 04:54:40 from `/scratch/mbabaei/agentm2m_devbench/runs/am-20261014-012059/results`.

## Tables

| table | files | rows | csv |
|---|---|---|---|
| rq2_changes | 48 | 432 | csv/rq2_changes.csv |
| rq3_reviewer | 48 | 48 | csv/rq3_reviewer.csv |
| rq1_runs | 1 | 144 | csv/rq1_runs.csv |

## Summaries

### rq1_by_model_config



| model | config | n | canary_pass_sum | canary_total_sum | acceptance_pass_mean | acceptance_pass_rate | mast_p1_count_mean | total_tokens_mean | repo_count |
|---|---|---|---|---|---|---|---|---|---|
| deepseek-coder-v2:16b | agentm2m | 8 | 40.000 | 40.000 | 0.000 | 0.000 | 2.000 | 15535.500 | 8 |
| deepseek-coder-v2:16b | free_text | 8 | 0.000 | 40.000 | 0.000 | 0.000 | 2.000 | 9654.625 | 8 |
| deepseek-coder-v2:16b | shared_schema | 8 | 0.000 | 40.000 | 0.000 | 0.000 | 2.000 | 5335.500 | 8 |
| devstral:24b | agentm2m | 8 | 40.000 | 40.000 | 0.000 | 0.000 | 1.000 | 11022.250 | 8 |
| devstral:24b | free_text | 8 | 24.000 | 40.000 | 0.000 | 0.000 | 2.000 | 8957.500 | 8 |
| devstral:24b | shared_schema | 8 | 0.000 | 40.000 | 0.000 | 0.000 | 1.125 | 4058.375 | 8 |
| gemma3:27b | agentm2m | 8 | 40.000 | 40.000 | 0.000 | 0.000 | 0.250 | 12898.875 | 8 |
| gemma3:27b | free_text | 8 | 13.000 | 40.000 | 0.000 | 0.000 | 0.250 | 12057.625 | 8 |
| gemma3:27b | shared_schema | 8 | 5.000 | 40.000 | 0.000 | 0.000 | 1.000 | 6533.750 | 8 |
| llama3.1:8b | agentm2m | 8 | 40.000 | 40.000 | 0.000 | 0.000 | 1.875 | 11821.750 | 8 |
| llama3.1:8b | free_text | 8 | 18.000 | 40.000 | 0.000 | 0.000 | 2.000 | 10149.000 | 8 |
| llama3.1:8b | shared_schema | 8 | 0.000 | 40.000 | 0.000 | 0.000 | 2.000 | 5505.000 | 8 |
| phi4:14b | agentm2m | 8 | 40.000 | 40.000 | 0.000 | 0.000 | 0.375 | 12805.500 | 8 |
| phi4:14b | free_text | 8 | 7.000 | 40.000 | 0.000 | 0.000 | 0.500 | 8682.500 | 8 |
| phi4:14b | shared_schema | 8 | 0.000 | 40.000 | 0.000 | 0.000 | 1.000 | 5233.250 | 8 |
| qwen2.5-coder:7b | agentm2m | 8 | 40.000 | 40.000 | 0.000 | 0.000 | 0.000 | 11442.875 | 8 |
| qwen2.5-coder:7b | free_text | 8 | 4.000 | 40.000 | 0.000 | 0.000 | 0.000 | 8484.750 | 8 |
| qwen2.5-coder:7b | shared_schema | 8 | 0.000 | 40.000 | 0.000 | 0.000 | 0.000 | 4104.500 | 8 |

### rq2_by_model_config



| model | config | n | precision_mean | recall_mean | tokens_mean | repo_count |
|---|---|---|---|---|---|---|
| deepseek-coder-v2:16b | agentm2m | 24 | 1.000 | 1.000 | 529.292 | 24 |
| deepseek-coder-v2:16b | free_text | 24 | 0.375 | 0.708 | 9689.792 | 24 |
| deepseek-coder-v2:16b | shared_schema | 24 | 0.136 | 1.000 | 5271.292 | 24 |
| devstral:24b | agentm2m | 24 | 1.000 | 1.000 | 413.292 | 24 |
| devstral:24b | free_text | 24 | 0.797 | 0.958 | 9364.875 | 24 |
| devstral:24b | shared_schema | 24 | 0.136 | 1.000 | 3845.250 | 24 |
| gemma3:27b | agentm2m | 24 | 1.000 | 1.000 | 439.250 | 24 |
| gemma3:27b | free_text | 24 | 0.566 | 0.667 | 12368.958 | 24 |
| gemma3:27b | shared_schema | 24 | 0.136 | 1.000 | 6748.292 | 24 |
| llama3.1:8b | agentm2m | 24 | 1.000 | 1.000 | 458.583 | 24 |
| llama3.1:8b | free_text | 24 | 0.250 | 0.792 | 10472.083 | 24 |
| llama3.1:8b | shared_schema | 24 | 0.136 | 1.000 | 5307.125 | 24 |
| phi4:14b | agentm2m | 24 | 1.000 | 1.000 | 478.167 | 24 |
| phi4:14b | free_text | 24 | 0.623 | 0.917 | 9022.500 | 24 |
| phi4:14b | shared_schema | 24 | 0.136 | 1.000 | 5182.167 | 24 |
| qwen2.5-coder:7b | agentm2m | 24 | 1.000 | 1.000 | 446.917 | 24 |
| qwen2.5-coder:7b | free_text | 24 | 0.646 | 0.750 | 8703.958 | 24 |
| qwen2.5-coder:7b | shared_schema | 24 | 0.136 | 1.000 | 4067.250 | 24 |

### rq3_by_model



| model | n | retroactive_coverage_mean | tokens_mean | repo_count |
|---|---|---|---|---|
| deepseek-coder-v2:16b | 8 | 1.000 | 28776.750 | 8 |
| devstral:24b | 8 | 1.000 | 17773.875 | 8 |
| gemma3:27b | 8 | 1.000 | 20057.375 | 8 |
| llama3.1:8b | 8 | 1.000 | 15779.250 | 8 |
| phi4:14b | 8 | 1.000 | 19949.000 | 8 |
| qwen2.5-coder:7b | 8 | 1.000 | 15498.375 | 8 |

## Figures

- fig_rq2_precision: figures/fig_rq2_precision.png / .pdf
- fig_rq2_tokens: figures/fig_rq2_tokens.png / .pdf
- fig_rq1_canaries: figures/fig_rq1_canaries.png / .pdf

## Completeness

49/50 stage cells finished OK.

| stage | cell_id | cell | state | steps_ok | steps_total | node | elapsed | failed_steps | records_done | records_expected |
|---|---|---|---|---|---|---|---|---|---|---|
| setup | 0 | - | OK | 5 | 5 | ng31305 | 765 |  |  |  |
| devbench | 0 | repo=chakin,model=qwen2.5-coder-7b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 1 | repo=chakin,model=llama3.1-8b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 2 | repo=chakin,model=phi4-14b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 3 | repo=chakin,model=deepseek-coder-v2-16b | OK | 3 | 3 | ng30707 | 1120 |  | 13 | 13.000 |
| devbench | 4 | repo=chakin,model=devstral-24b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 5 | repo=chakin,model=gemma3-27b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 6 | repo=geotext,model=qwen2.5-coder-7b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 7 | repo=geotext,model=llama3.1-8b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 8 | repo=geotext,model=phi4-14b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 9 | repo=geotext,model=deepseek-coder-v2-16b | OK | 3 | 3 | ng30707 | 812 |  | 13 | 13.000 |
| devbench | 10 | repo=geotext,model=devstral-24b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 11 | repo=geotext,model=gemma3-27b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 12 | repo=particle-swarm-optimization,model=qwen2.5-coder-7b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 13 | repo=particle-swarm-optimization,model=llama3.1-8b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 14 | repo=particle-swarm-optimization,model=phi4-14b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 15 | repo=particle-swarm-optimization,model=deepseek-coder-v2-16b | OK | 3 | 3 | ng20202 | 1081 |  | 13 | 13.000 |
| devbench | 16 | repo=particle-swarm-optimization,model=devstral-24b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 17 | repo=particle-swarm-optimization,model=gemma3-27b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 18 | repo=readtime,model=qwen2.5-coder-7b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 19 | repo=readtime,model=llama3.1-8b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 20 | repo=readtime,model=phi4-14b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 21 | repo=readtime,model=deepseek-coder-v2-16b | OK | 3 | 3 | ng20202 | 1295 |  | 13 | 13.000 |
| devbench | 22 | repo=readtime,model=devstral-24b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 23 | repo=readtime,model=gemma3-27b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 24 | repo=stocktrends,model=qwen2.5-coder-7b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 25 | repo=stocktrends,model=llama3.1-8b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 26 | repo=stocktrends,model=phi4-14b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 27 | repo=stocktrends,model=deepseek-coder-v2-16b | OK | 3 | 3 | ng20302 | 1563 |  | 13 | 13.000 |
| devbench | 28 | repo=stocktrends,model=devstral-24b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 29 | repo=stocktrends,model=gemma3-27b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 30 | repo=ArXiv_digest,model=qwen2.5-coder-7b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 31 | repo=ArXiv_digest,model=llama3.1-8b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 32 | repo=ArXiv_digest,model=phi4-14b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 33 | repo=ArXiv_digest,model=deepseek-coder-v2-16b | OK | 3 | 3 | ng10701 | 1378 |  | 13 | 13.000 |
| devbench | 34 | repo=ArXiv_digest,model=devstral-24b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 35 | repo=ArXiv_digest,model=gemma3-27b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 36 | repo=hone,model=qwen2.5-coder-7b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 37 | repo=hone,model=llama3.1-8b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 38 | repo=hone,model=phi4-14b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 39 | repo=hone,model=deepseek-coder-v2-16b | OK | 3 | 3 | ng10707 | 1753 |  | 13 | 13.000 |
| devbench | 40 | repo=hone,model=devstral-24b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 41 | repo=hone,model=gemma3-27b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 42 | repo=lice,model=qwen2.5-coder-7b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 43 | repo=lice,model=llama3.1-8b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 44 | repo=lice,model=phi4-14b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 45 | repo=lice,model=deepseek-coder-v2-16b | OK | 3 | 3 | ng10711 | 1398 |  | 13 | 13.000 |
| devbench | 46 | repo=lice,model=devstral-24b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| devbench | 47 | repo=lice,model=gemma3-27b | OK | 3 | 3 | - |  |  | 13 | 13.000 |
| collect | 0 | - | RUNNING | 3 | 4 | ng20304 | 52 |  |  |  |

## GPU use

| file | samples | util_mean_% | util_max_% | mem_max_MiB |
|---|---|---|---|---|
| gpu_devbench_5517225_0_r0.csv | 17 | 19.500 | 41.000 | 6805.000 |
| gpu_devbench_5517225_10_r0.csv | 18 | 43.100 | 69.000 | 19455.000 |
| gpu_devbench_5517225_11_r0.csv | 35 | 55.500 | 97.000 | 20609.000 |
| gpu_devbench_5517225_12_r0.csv | 10 | 25.400 | 57.000 | 6805.000 |
| gpu_devbench_5517225_13_r0.csv | 12 | 25.400 | 43.000 | 9291.000 |
| gpu_devbench_5517225_14_r0.csv | 28 | 27.400 | 58.000 | 12351.000 |
| gpu_devbench_5517225_15_r0.csv | 5 | 0.000 | 0.000 | 17691.000 |
| gpu_devbench_5517225_16_r0.csv | 21 | 38.000 | 96.000 | 19455.000 |
| gpu_devbench_5517225_17_r0.csv | 40 | 42.200 | 100.000 | 20613.000 |
| gpu_devbench_5517225_18_r0.csv | 19 | 27.100 | 94.000 | 6803.000 |
| gpu_devbench_5517225_19_r0.csv | 22 | 19.900 | 40.000 | 9291.000 |
| gpu_devbench_5517225_1_r0.csv | 25 | 25.000 | 64.000 | 9291.000 |
| gpu_devbench_5517225_20_r0.csv | 26 | 37.400 | 92.000 | 12351.000 |
| gpu_devbench_5517225_22_r0.csv | 36 | 35.600 | 67.000 | 19455.000 |
| gpu_devbench_5517225_23_r0.csv | 39 | 51.900 | 97.000 | 20611.000 |
| gpu_devbench_5517225_24_r0.csv | 16 | 31.800 | 76.000 | 6805.000 |
| gpu_devbench_5517225_25_r0.csv | 17 | 26.500 | 51.000 | 9291.000 |
| gpu_devbench_5517225_26_r0.csv | 29 | 40.900 | 75.000 | 12351.000 |
| gpu_devbench_5517225_28_r0.csv | 29 | 50.600 | 87.000 | 19455.000 |
| gpu_devbench_5517225_29_r0.csv | 51 | 52.500 | 100.000 | 20611.000 |
| gpu_devbench_5517225_2_r0.csv | 25 | 26.900 | 53.000 | 12351.000 |
| gpu_devbench_5517225_30_r0.csv | 14 | 30.900 | 93.000 | 6805.000 |
| gpu_devbench_5517225_31_r0.csv | 17 | 30.100 | 80.000 | 9291.000 |
| gpu_devbench_5517225_32_r0.csv | 26 | 42.300 | 97.000 | 12349.000 |
| gpu_devbench_5517225_34_r0.csv | 25 | 45.500 | 90.000 | 19455.000 |
| gpu_devbench_5517225_35_r0.csv | 58 | 43.200 | 82.000 | 20613.000 |
| gpu_devbench_5517225_36_r0.csv | 17 | 26.800 | 45.000 | 6805.000 |
| gpu_devbench_5517225_37_r0.csv | 24 | 29.400 | 85.000 | 9291.000 |
| gpu_devbench_5517225_38_r0.csv | 29 | 51.900 | 100.000 | 12351.000 |
| gpu_devbench_5517225_3_r0.csv | 30 | 0.200 | 2.000 | 17691.000 |
| gpu_devbench_5517225_40_r0.csv | 49 | 34.300 | 100.000 | 19455.000 |
| gpu_devbench_5517225_41_r0.csv | 69 | 45.700 | 100.000 | 20611.000 |
| gpu_devbench_5517225_42_r0.csv | 22 | 23.600 | 100.000 | 6805.000 |
| gpu_devbench_5517225_43_r0.csv | 19 | 32.700 | 64.000 | 9291.000 |
| gpu_devbench_5517225_44_r0.csv | 43 | 26.300 | 65.000 | 12351.000 |
| gpu_devbench_5517225_46_r0.csv | 26 | 43.600 | 82.000 | 19455.000 |
| gpu_devbench_5517225_47_r0.csv | 50 | 48.100 | 86.000 | 20611.000 |
| gpu_devbench_5517225_4_r0.csv | 20 | 42.500 | 91.000 | 19455.000 |
| gpu_devbench_5517225_5_r0.csv | 28 | 53.900 | 97.000 | 20609.000 |
| gpu_devbench_5517225_6_r0.csv | 15 | 17.600 | 29.000 | 6805.000 |
| gpu_devbench_5517225_7_r0.csv | 20 | 27.700 | 100.000 | 9291.000 |
| gpu_devbench_5517225_8_r0.csv | 25 | 25.300 | 58.000 | 12351.000 |
| gpu_devbench_5517225_9_r0.csv | 27 | 0.200 | 2.000 | 17691.000 |
| gpu_devbench_5521050_15_r0.csv | 18 | 19.000 | 46.000 | 18839.000 |
| gpu_devbench_5521050_21_r0.csv | 22 | 21.800 | 50.000 | 18799.000 |
| gpu_devbench_5521050_27_r0.csv | 26 | 18.100 | 69.000 | 18887.000 |
| gpu_devbench_5521050_33_r0.csv | 23 | 27.700 | 82.000 | 18863.000 |
| gpu_devbench_5521050_39_r0.csv | 29 | 21.100 | 57.000 | 18845.000 |
| gpu_devbench_5521050_3_r0.csv | 19 | 18.400 | 62.000 | 18823.000 |
| gpu_devbench_5521050_45_r0.csv | 23 | 22.000 | 68.000 | 18895.000 |
| gpu_devbench_5521050_9_r0.csv | 14 | 22.100 | 70.000 | 18791.000 |
| gpu_setup_5517224_r0.csv | 13 | 0.200 | 2.000 | 20445.000 |

