# DeepInfra models and prices

Fetched from DeepInfra's public model list on 2026-11-06: `https://api.deepinfra.com/models/list`.
Prices are USD per **million** tokens, converted from DeepInfra's cents-per-token figures.
Only `text-generation` models with both an input and an output price are listed. DeepInfra's list also has
169 non-text-generation entries (embeddings, rerankers, image, audio); they are not per-token chat models and are not listed here.

**231 models**, sorted by total price (input + output) ascending. `Tools` means the model advertises tool calling.

| # | Model | Input $/Mtok | Output $/Mtok | Total $/Mtok | Context | Tools |
|---:|---|---:|---:|---:|---:|---|
| 1 | `meta-llama/Llama-3.2-1B-Instruct` | 0.005 | 0.01 | 0.015 | 131072 | yes |
| 2 | `meta-llama/Llama-3.2-3B-Instruct` | 0.02 | 0.02 | 0.04 | 131072 | yes |
| 3 | `mistralai/Mistral-Nemo-Instruct-2407` | 0.029 | 0.03 | 0.059 | 131072 | yes |
| 4 | `meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo` | 0.02 | 0.04 | 0.06 | 131072 | yes |
| 5 | `Qwen/Qwen3.5-0.8B` | 0.01 | 0.05 | 0.06 | 262144 | yes |
| 6 | `meta-llama/Meta-Llama-3.1-8B-Instruct` | 0.02 | 0.05 | 0.07 | 131072 | yes |
| 7 | `meta-llama/Meta-Llama-3-8B-Instruct` | 0.03 | 0.04 | 0.07 | 8192 | yes |
| 8 | `Qwen/Qwen2.5-Coder-7B` | 0.025 | 0.05 | 0.075 | 32768 |  |
| 9 | `mistralai/Mistral-7B-Instruct-v0.3` | 0.028 | 0.054 | 0.082 | 32768 | yes |
| 10 | `Sao10K/L3-8B-Lunaris-v1-Turbo` | 0.04 | 0.05 | 0.09 | 8192 |  |
| 11 | `google/gemma-2-9b-it` | 0.03 | 0.06 | 0.09 | 8192 |  |
| 12 | `Sao10K/L3-8B-Lunaris-v1` | 0.03 | 0.06 | 0.09 | 8192 |  |
| 13 | `meta-llama/Llama-Guard-3-8B` | 0.055 | 0.055 | 0.11 | 131072 |  |
| 14 | `microsoft/WizardLM-2-7B` | 0.055 | 0.055 | 0.11 | 32768 |  |
| 15 | `mistralai/Mistral-7B-Instruct-v0.1` | 0.055 | 0.055 | 0.11 | 32768 | yes |
| 16 | `mistralai/Mistral-7B-Instruct-v0.2` | 0.055 | 0.055 | 0.11 | 32768 |  |
| 17 | `openchat/openchat_3.5` | 0.055 | 0.055 | 0.11 | 8192 |  |
| 18 | `openchat/openchat-3.6-8b` | 0.055 | 0.055 | 0.11 | 8192 |  |
| 19 | `Qwen/Qwen2-7B-Instruct` | 0.055 | 0.055 | 0.11 | 32768 | yes |
| 20 | `google/gemma-4-E4B-it` | 0.02 | 0.1 | 0.12 | 131072 | yes |
| 21 | `Qwen/Qwen3.5-2B` | 0.02 | 0.1 | 0.12 | 262144 | yes |
| 22 | `deepseek-ai/DeepSeek-OCR` | 0.03 | 0.1 | 0.13 | 8192 |  |
| 23 | `mistralai/Mistral-Small-24B-Instruct-2501` | 0.05 | 0.08 | 0.13 | 32768 |  |
| 24 | `google/codegemma-7b-it` | 0.07 | 0.07 | 0.14 | 8192 |  |
| 25 | `google/gemma-1.1-7b-it` | 0.07 | 0.07 | 0.14 | 8192 |  |
| 26 | `Qwen/Qwen2.5-7B-Instruct` | 0.04 | 0.1 | 0.14 | 32768 |  |
| 27 | `ibm-granite/granite-4.2-3b` | 0.03 | 0.12 | 0.15 | 131072 | yes |
| 28 | `google/gemma-3-4b-it` | 0.05 | 0.1 | 0.15 | 131072 | yes |
| 29 | `microsoft/Phi-4-multimodal-instruct` | 0.05 | 0.1 | 0.15 | 131072 |  |
| 30 | `mistralai/Mistral-Small-3.1-24B-Instruct-2503` | 0.05 | 0.1 | 0.15 | 128000 |  |
| 31 | `openai/gpt-oss-20b` | 0.03 | 0.14 | 0.17 | 131072 | yes |
| 32 | `Qwen/Qwen3.5-4B` | 0.03 | 0.15 | 0.18 | 262144 | yes |
| 33 | `mistralai/Devstral-Small-2505` | 0.06 | 0.12 | 0.18 | 128000 | yes |
| 34 | `nvidia/NVIDIA-Nemotron-Nano-9B-v2` | 0.04 | 0.16 | 0.2 | 131072 | yes |
| 35 | `google/gemma-3-12b-it` | 0.05 | 0.15 | 0.2 | 131072 | yes |
| 36 | `KoboldAI/LLaMA2-13B-Tiefighter` | 0.1 | 0.1 | 0.2 | 4096 |  |
| 37 | `openai/gpt-oss-120b` | 0.037 | 0.17 | 0.207 | 131072 | yes |
| 38 | `microsoft/phi-4` | 0.07 | 0.14 | 0.21 | 16384 |  |
| 39 | `Qwen/Qwen2.5-Coder-32B-Instruct` | 0.06 | 0.15 | 0.21 | 32768 |  |
| 40 | `nvidia/NVIDIA-Nemotron-3.5-Lightning` | 0.06 | 0.16 | 0.22 | 262144 | yes |
| 41 | `deepseek-ai/DeepSeek-V4-Flash-0731` | 0.06 | 0.18 | 0.24 | 1048576 | yes |
| 42 | `inclusionAI/Ling-3.0-flash` | 0.06 | 0.18 | 0.24 | 131072 | yes |
| 43 | `inclusionAI/Ling-3.0-flash-Fin` | 0.06 | 0.18 | 0.24 | 262144 | yes |
| 44 | `inclusionAI/Ling-3.0-flash-VL` | 0.06 | 0.18 | 0.24 | 131072 | yes |
| 45 | `google/gemma-3-27b-it` | 0.08 | 0.16 | 0.24 | 131072 | yes |
| 46 | `nvidia/Nemotron-3-Nano-30B-A3B` | 0.05 | 0.2 | 0.25 | 262144 | yes |
| 47 | `Qwen/Qwen3.5-9B` | 0.1 | 0.15 | 0.25 | 262144 | yes |
| 48 | `Austism/chronos-hermes-13b-v2` | 0.13 | 0.13 | 0.26 | 4096 |  |
| 49 | `Gryphe/MythoMax-L2-13b-turbo` | 0.13 | 0.13 | 0.26 | 4096 |  |
| 50 | `meta-llama/Llama-2-13b-chat-hf` | 0.13 | 0.13 | 0.26 | 4096 |  |
| 51 | `deepseek-ai/DeepSeek-V4-Flash` | 0.09 | 0.18 | 0.27 | 1048576 | yes |
| 52 | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | 0.075 | 0.2 | 0.275 | 128000 | yes |
| 53 | `microsoft/Phi-3-medium-4k-instruct` | 0.14 | 0.14 | 0.28 | 4096 |  |
| 54 | `allenai/olmOCR-2-7B-1025` | 0.09 | 0.19 | 0.28 | 16384 |  |
| 55 | `NovaSky-AI/Sky-T1-32B-Preview` | 0.12 | 0.18 | 0.3 | 32768 |  |
| 56 | `Qwen/QwQ-32B-Preview` | 0.12 | 0.18 | 0.3 | 32768 |  |
| 57 | `bigcode/starcoder2-15b-instruct-v0.1` | 0.15 | 0.15 | 0.3 |  |  |
| 58 | `ibm-granite/granite-4.2-8b` | 0.06 | 0.25 | 0.31 | 131072 | yes |
| 59 | `Qwen/Qwen3-Coder-30B-A3B-Instruct` | 0.07 | 0.26 | 0.33 | 262144 | yes |
| 60 | `mistralai/Devstral-Small-2507` | 0.07 | 0.28 | 0.35 | 128000 | yes |
| 61 | `Qwen/Qwen3-32B` | 0.08 | 0.28 | 0.36 | 40960 | yes |
| 62 | `meta-llama/Llama-Guard-4-12B` | 0.18 | 0.18 | 0.36 | 163840 |  |
| 63 | `Qwen/Qwen3-14B` | 0.12 | 0.24 | 0.36 | 40960 | yes |
| 64 | `stepfun-ai/Step-3.5-Flash` | 0.09 | 0.3 | 0.39 | 262144 | yes |
| 65 | `meta-llama/Llama-4-Scout-17B-16E-Instruct` | 0.1 | 0.3 | 0.4 | 327680 | yes |
| 66 | `nvidia/Nemotron-Content-Safety-3.5` | 0.2 | 0.2 | 0.4 | 131072 |  |
| 67 | `google/gemma-4-26B-A4B-it` | 0.07 | 0.34 | 0.41 | 262144 | yes |
| 68 | `XiaomiMiMo/MiMo-V2.5` | 0.14 | 0.28 | 0.42 | 262144 | yes |
| 69 | `XiaomiMiMo/MiMo-V2.6-Flash` | 0.14 | 0.28 | 0.42 | 1048576 | yes |
| 70 | `microsoft/phi-4-reasoning-plus` | 0.07 | 0.35 | 0.42 | 32768 |  |
| 71 | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | 0.1 | 0.32 | 0.42 | 131072 | yes |
| 72 | `google/gemma-4-31B-it-turbo` | 0.09 | 0.34 | 0.43 | 262144 | yes |
| 73 | `zai-org/GLM-4.7-Flash` | 0.06 | 0.4 | 0.46 | 202752 | yes |
| 74 | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B` | 0.075 | 0.4 | 0.475 | 262144 | yes |
| 75 | `cognitivecomputations/dolphin-2.6-mixtral-8x7b` | 0.24 | 0.24 | 0.48 | 32768 |  |
| 76 | `Qwen/Qwen3.8-Flash` | 0.113 | 0.382 | 0.495 | 1000000 | yes |
| 77 | `ByteDance/Seed-2.0-mini` | 0.1 | 0.4 | 0.5 | 256000 | yes |
| 78 | `google/gemini-2.0-flash-001` | 0.1 | 0.4 | 0.5 | 1000000 | yes |
| 79 | `deepseek-ai/DeepSeek-V3.2-Exp` | 0.21 | 0.32 | 0.53 | 163840 | yes |
| 80 | `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B` | 0.27 | 0.27 | 0.54 | 131072 |  |
| 81 | `google/gemma-2-27b-it` | 0.27 | 0.27 | 0.54 | 8192 |  |
| 82 | `Qwen/QwQ-32B` | 0.15 | 0.4 | 0.55 | 131072 |  |
| 83 | `google/gemma-4-31B-it` | 0.2 | 0.4 | 0.6 | 262144 | yes |
| 84 | `Qwen/Qwen3-30B-A3B` | 0.12 | 0.5 | 0.62 | 40960 | yes |
| 85 | `meta-llama/Llama-3.3-70B-Instruct` | 0.23 | 0.4 | 0.63 | 131072 | yes |
| 86 | `Qwen/Qwen3-235B-A22B-Instruct-2507` | 0.09 | 0.55 | 0.64 | 262144 | yes |
| 87 | `deepseek-ai/DeepSeek-V3.2` | 0.26 | 0.38 | 0.64 | 163840 | yes |
| 88 | `nvidia/NVIDIA-Nemotron-3.5-Super-VL` | 0.15 | 0.5 | 0.65 | 262144 | yes |
| 89 | `zai-org/GLM-5.3-Flash` | 0.15 | 0.5 | 0.65 | 1048576 | yes |
| 90 | `tencent/Hy3` | 0.13 | 0.53 | 0.66 | 262144 | yes |
| 91 | `openbmb/MiniCPM-Llama3-V-2_5` | 0.34 | 0.34 | 0.68 | 8192 |  |
| 92 | `meta-llama/Llama-3.2-11B-Vision-Instruct` | 0.345 | 0.345 | 0.69 | 131072 |  |
| 93 | `Qwen/Qwen3-VL-4B-Instruct` | 0.1 | 0.6 | 0.7 | 262144 | yes |
| 94 | `meta-llama/Meta-Llama-3-70B-Instruct` | 0.3 | 0.4 | 0.7 | 8192 | yes |
| 95 | `Qwen/Qwen3-235B-A22B` | 0.18 | 0.54 | 0.72 | 40960 | yes |
| 96 | `openai/gpt-oss-120b-Turbo` | 0.15 | 0.6 | 0.75 | 131072 | yes |
| 97 | `Qwen/Qwen3-VL-30B-A3B-Instruct` | 0.15 | 0.6 | 0.75 | 262144 | yes |
| 98 | `Qwen/QVQ-72B-Preview` | 0.25 | 0.5 | 0.75 | 32000 |  |
| 99 | `cognitivecomputations/dolphin-2.9.1-llama-3-70b` | 0.35 | 0.4 | 0.75 | 8192 |  |
| 100 | `lizpreciatior/lzlv_70b_fp16_hf` | 0.35 | 0.4 | 0.75 | 4096 |  |
| 101 | `mattshumer/Reflection-Llama-3.1-70B` | 0.35 | 0.4 | 0.75 | 8192 |  |
| 102 | `meta-llama/Llama-3.2-90B-Vision-Instruct` | 0.35 | 0.4 | 0.75 | 32768 |  |
| 103 | `Qwen/Qwen2-72B-Instruct` | 0.35 | 0.4 | 0.75 | 32768 | yes |
| 104 | `Qwen/Qwen2.5-72B-Instruct` | 0.36 | 0.4 | 0.76 | 32768 | yes |
| 105 | `allenai/Olmo-3.1-32B-Instruct` | 0.2 | 0.6 | 0.8 | 65536 | yes |
| 106 | `deepseek-ai/DeepSeek-V4.1-Flash` | 0.2 | 0.6 | 0.8 | 1048576 | yes |
| 107 | `deepseek-ai/DeepSeek-V4.1-Flash-1017` | 0.2 | 0.6 | 0.8 | 1048576 | yes |
| 108 | `nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL` | 0.2 | 0.6 | 0.8 | 131072 |  |
| 109 | `Qwen/Qwen2.5-VL-32B-Instruct` | 0.2 | 0.6 | 0.8 | 128000 |  |
| 110 | `Gryphe/MythoMax-L2-13b` | 0.4 | 0.4 | 0.8 | 4096 |  |
| 111 | `meta-llama/Meta-Llama-3.1-70B-Instruct` | 0.4 | 0.4 | 0.8 | 131072 | yes |
| 112 | `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo` | 0.4 | 0.4 | 0.8 | 131072 | yes |
| 113 | `nvidia/Llama-3.3-Nemotron-Super-49B-v1.5` | 0.4 | 0.4 | 0.8 | 131072 | yes |
| 114 | `ibm-granite/granite-4.2-30b` | 0.16 | 0.65 | 0.81 | 131072 | yes |
| 115 | `deepreinforce-ai/Ornith-1.0-35B` | 0.1 | 0.75 | 0.85 | 262144 | yes |
| 116 | `Qwen/Qwen3-VL-8B-Instruct` | 0.18 | 0.69 | 0.87 | 262144 | yes |
| 117 | `allenai/olmOCR-2` | 0.14 | 0.8 | 0.94 | 16384 |  |
| 118 | `allenai/olmOCR-7B-0825` | 0.14 | 0.8 | 0.94 | 16384 |  |
| 119 | `allenai/olmOCR-7B-1025` | 0.14 | 0.8 | 0.94 | 16384 |  |
| 120 | `PaddlePaddle/PaddleOCR-VL-0.9B` | 0.14 | 0.8 | 0.94 | 16384 |  |
| 121 | `microsoft/WizardLM-2-8x22B` | 0.48 | 0.48 | 0.96 | 65536 |  |
| 122 | `meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8` | 0.2 | 0.8 | 1 | 1048576 |  |
| 123 | `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning` | 0.2 | 0.8 | 1 | 262144 | yes |
| 124 | `meta-llama/Llama-4-Maverick-17B-128E-Instruct-Turbo` | 0.5 | 0.5 | 1 | 8192 |  |
| 125 | `google/gemma-4-31B-it-Ultra` | 0.27 | 0.76 | 1.03 | 131072 | yes |
| 126 | `Qwen/Qwen3.6-35B-A3B` | 0.1 | 0.95 | 1.05 | 262144 | yes |
| 127 | `mistralai/Mixtral-8x7B-Instruct-v0.1` | 0.54 | 0.54 | 1.08 | 32768 | yes |
| 128 | `Qwen/Qwen3-VL-235B-A22B-Instruct` | 0.2 | 0.88 | 1.08 | 262144 | yes |
| 129 | `Qwen/Qwen3-VL-4B-Thinking` | 0.1 | 1 | 1.1 | 262144 | yes |
| 130 | `Qwen/Qwen3.5-35B-A3B` | 0.14 | 1 | 1.14 | 262144 | yes |
| 131 | `deepseek-ai/DeepSeek-V3-0324` | 0.24 | 0.9 | 1.14 | 163840 | yes |
| 132 | `openai/gpt-oss-120b-Ultra` | 0.2 | 0.95 | 1.15 | 131072 | yes |
| 133 | `Qwen/Qwen3-Next-80B-A3B-Instruct` | 0.09 | 1.1 | 1.19 | 262144 | yes |
| 134 | `inclusionAI/Ling-3.1-flash` | 0.3 | 0.9 | 1.2 | 262144 | yes |
| 135 | `zai-org/GLM-4.6V` | 0.3 | 0.9 | 1.2 | 131072 | yes |
| 136 | `Phind/Phind-CodeLlama-34B-v2` | 0.6 | 0.6 | 1.2 | 4096 |  |
| 137 | `deepseek-ai/DeepSeek-V3.1` | 0.25 | 0.95 | 1.2 | 163840 | yes |
| 138 | `deepseek-ai/DeepSeek-V3` | 0.32 | 0.89 | 1.21 | 163840 | yes |
| 139 | `deepseek-ai/DeepSeek-V3.1-Terminus` | 0.27 | 0.95 | 1.22 | 163840 | yes |
| 140 | `MiniMaxAI/MiniMax-M2.1` | 0.27 | 0.95 | 1.22 | 196608 | yes |
| 141 | `MiniMaxAI/MiniMax-M2.7` | 0.25 | 1 | 1.25 | 196608 | yes |
| 142 | `MiniMaxAI/MiniMax-M2` | 0.254 | 1.02 | 1.274 | 262144 | yes |
| 143 | `Qwen/Qwen3-VL-30B-A3B-Thinking` | 0.29 | 0.99 | 1.28 | 262144 | yes |
| 144 | `mistralai/Mixtral-8x22B-Instruct-v0.1` | 0.65 | 0.65 | 1.3 | 65536 | yes |
| 145 | `MiniMaxAI/MiniMax-M2.5` | 0.15 | 1.15 | 1.3 | 196608 | yes |
| 146 | `zai-org/GLM-4.5-Air` | 0.2 | 1.1 | 1.3 | 131072 | yes |
| 147 | `Qwen/Qwen3-Coder-480B-A35B-Instruct-Turbo` | 0.3 | 1 | 1.3 | 262144 | yes |
| 148 | `XiaomiMiMo/MiMo-V2.6-Pro` | 0.43 | 0.87 | 1.3 | 1048576 | yes |
| 149 | `stepfun-ai/Step-3.7-Flash` | 0.2 | 1.15 | 1.35 | 262144 | yes |
| 150 | `MiniMaxAI/MiniMax-M3` | 0.28 | 1.1 | 1.38 | 524288 | yes |
| 151 | `NousResearch/Hermes-3-Llama-3.1-70B` | 0.7 | 0.7 | 1.4 | 131072 |  |
| 152 | `meta-llama/Llama-2-70b-chat-hf` | 0.64 | 0.8 | 1.44 | 4096 |  |
| 153 | `meta-models/Muse-Glimmer-30B` | 0.3 | 1.2 | 1.5 | 131072 | yes |
| 154 | `deepseek-ai/DeepSeek-R1-Distill-Llama-70B` | 0.7 | 0.8 | 1.5 | 131072 |  |
| 155 | `Sao10K/L3-70B-Euryale-v2.1` | 0.7 | 0.8 | 1.5 | 8192 |  |
| 156 | `deepinfra/airoboros-70b` | 0.7 | 0.9 | 1.6 | 4096 |  |
| 157 | `meta-llama/Meta-Llama-3.1-405B-Instruct` | 0.8 | 0.8 | 1.6 | 32768 | yes |
| 158 | `thinkingmachines/Inkling-Small` | 0.45 | 1.2 | 1.65 | 524288 | yes |
| 159 | `Sao10K/L3.1-70B-Euryale-v2.2` | 0.85 | 0.85 | 1.7 | 131072 |  |
| 160 | `Sao10K/L3.3-70B-Euryale-v2.3` | 0.85 | 0.85 | 1.7 | 131072 |  |
| 161 | `google/gemini-3.1-flash-lite` | 0.25 | 1.5 | 1.75 | 1000000 | yes |
| 162 | `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp` | 0.44 | 1.32 | 1.76 | 1048576 | yes |
| 163 | `allenai/olmOCR-7B-0725-FP8` | 0.27 | 1.5 | 1.77 | 16384 |  |
| 164 | `zai-org/GLM-4.5` | 0.38 | 1.6 | 1.98 | 131072 | yes |
| 165 | `Qwen/Qwen3-Coder-480B-A35B-Instruct` | 0.4 | 1.6 | 2 | 262144 | yes |
| 166 | `NousResearch/Hermes-3-Llama-3.1-405B` | 1 | 1 | 2 | 131072 |  |
| 167 | `MiniMaxAI/MiniMax-M2.7-Turbo` | 0.38 | 1.7 | 2.08 | 196608 | yes |
| 168 | `zai-org/GLM-4.7` | 0.4 | 1.75 | 2.15 | 202752 | yes |
| 169 | `ByteDance/Seed-1.8` | 0.25 | 2 | 2.25 | 256000 | yes |
| 170 | `Qwen/Qwen3-VL-8B-Thinking` | 0.18 | 2.09 | 2.27 | 262144 | yes |
| 171 | `moonshotai/Kimi-K2-Instruct-0905` | 0.4 | 2 | 2.4 | 131072 | yes |
| 172 | `nvidia/Llama-3.1-Nemotron-70B-Instruct` | 1.2 | 1.2 | 2.4 | 131072 | yes |
| 173 | `moonshotai/Kimi-K2-Thinking` | 0.47 | 2 | 2.47 | 131072 | yes |
| 174 | `moonshotai/Kimi-K2-Instruct` | 0.5 | 2 | 2.5 | 131072 | yes |
| 175 | `zai-org/GLM-4.6` | 0.5 | 2 | 2.5 | 202752 | yes |
| 176 | `Qwen/Qwen3-235B-A22B-Thinking-2507` | 0.23 | 2.3 | 2.53 | 262144 | yes |
| 177 | `deepseek-ai/DeepSeek-R1-0528` | 0.5 | 2.15 | 2.65 | 163840 | yes |
| 178 | `deepseek-ai/DeepSeek-Prover-V2-671B` | 0.5 | 2.18 | 2.68 | 163840 |  |
| 179 | `zai-org/GLM-5` | 0.6 | 2.08 | 2.68 | 202752 | yes |
| 180 | `Qwen/Qwen3.5-122B-A10B` | 0.29 | 2.4 | 2.69 | 262144 | yes |
| 181 | `Qwen/Qwen3.8-27B` | 0.2 | 2.5 | 2.7 | 262144 | yes |
| 182 | `moonshotai/Kimi-K2.5` | 0.45 | 2.25 | 2.7 | 262144 | yes |
| 183 | `nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B` | 0.5 | 2.2 | 2.7 | 262144 | yes |
| 184 | `google/gemini-2.5-flash` | 0.3 | 2.5 | 2.8 | 1000000 | yes |
| 185 | `Qwen/Qwen3.5-27B` | 0.26 | 2.6 | 2.86 | 262144 | yes |
| 186 | `deepseek-ai/DeepSeek-R1` | 0.7 | 2.4 | 3.1 | 163840 | yes |
| 187 | `zai-org/GLM-5.2` | 0.75 | 2.4 | 3.15 | 1048576 | yes |
| 188 | `tencent/Hy4-preview` | 0.834 | 2.501 | 3.335 | 1048576 | yes |
| 189 | `Qwen/Qwen3.5-397B-A17B` | 0.45 | 3 | 3.45 | 262144 | yes |
| 190 | `ByteDance/Seed-2.0-code` | 0.5 | 3 | 3.5 | 256000 | yes |
| 191 | `ByteDance/Seed-2.0-pro` | 0.5 | 3 | 3.5 | 256000 | yes |
| 192 | `Qwen/Qwen3.6-27B` | 0.32 | 3.2 | 3.52 | 262144 | yes |
| 193 | `moonshotai/Kimi-K2.5-Turbo` | 0.6 | 3 | 3.6 | 262144 | yes |
| 194 | `stepfun-ai/Step-5-Preview` | 1 | 2.7 | 3.7 | 1048576 | yes |
| 195 | `deepseek-ai/DeepSeek-V4-Pro` | 1.3 | 2.6 | 3.9 | 1048576 | yes |
| 196 | `deepseek-ai/DeepSeek-V4-Pro-0813` | 1.3 | 2.6 | 3.9 | 1048576 | yes |
| 197 | `Qwen/Qwen3-VL-235B-A22B-Thinking` | 0.45 | 3.49 | 3.94 | 262144 | yes |
| 198 | `deepseek-ai/DeepSeek-R1-0528-Turbo` | 1 | 3 | 4 | 32768 | yes |
| 199 | `deepseek-ai/DeepSeek-R1-Turbo` | 1 | 3 | 4 | 40960 | yes |
| 200 | `deepseek-ai/DeepSeek-V3-0324-Turbo` | 1 | 3 | 4 | 32768 | yes |
| 201 | `XiaomiMiMo/MiMo-V2.5-Pro` | 1 | 3 | 4 | 1048576 | yes |
| 202 | `moonshotai/Kimi-K2.7-Code` | 0.68 | 3.4 | 4.08 | 262144 | yes |
| 203 | `moonshotai/Kimi-K2.6` | 0.75 | 3.5 | 4.25 | 262144 | yes |
| 204 | `google/gemini-3.7-flash` | 0.75 | 3.75 | 4.5 | 1000000 | yes |
| 205 | `zai-org/GLM-5.1` | 1.05 | 3.5 | 4.55 | 202752 | yes |
| 206 | `zai-org/GLM-5.3` | 0.9 | 4 | 4.9 | 1048576 | yes |
| 207 | `thinkingmachines/Inkling` | 0.95 | 4.05 | 5 | 524288 | yes |
| 208 | `anthropic/claude-haiku-4-5` | 1 | 5 | 6 | 200000 | yes |
| 209 | `nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16` | 1 | 5 | 6 | 262144 | yes |
| 210 | `Qwen/Qwen3.8-Max` | 1.65 | 4.951 | 6.601 | 256000 | yes |
| 211 | `Qwen/Qwen3-Max` | 1.2 | 6 | 7.2 | 256000 | yes |
| 212 | `Qwen/Qwen3-Max-Thinking` | 1.2 | 6 | 7.2 | 256000 | yes |
| 213 | `Qwen/Qwen3.8-2.4T-A95B` | 2 | 6 | 8 | 262144 | yes |
| 214 | `nvidia/Nemotron-4-340B-Instruct` | 4.2 | 4.2 | 8.4 | 4096 | yes |
| 215 | `Qwen/Qwen3.7-Max` | 2.5 | 7.5 | 10 | 256000 | yes |
| 216 | `google/gemini-3.5-flash` | 1.5 | 9 | 10.5 | 1000000 | yes |
| 217 | `google/gemini-2.5-pro` | 1.25 | 10 | 11.25 | 1000000 | yes |
| 218 | `anthropic/claude-sonnet-5-5` | 2 | 10 | 12 | 1000000 | yes |
| 219 | `moonshotai/Kimi-K3.1-Pro` | 2 | 10 | 12 | 1048576 | yes |
| 220 | `google/gemini-3.1-pro` | 2 | 12 | 14 | 1000000 | yes |
| 221 | `moonshotai/Kimi-K3` | 2.85 | 14.25 | 17.1 | 1048576 | yes |
| 222 | `anthropic/claude-sonnet-4-6` | 3 | 15 | 18 | 1000000 | yes |
| 223 | `anthropic/claude-sonnet-5` | 3 | 15 | 18 | 1000000 | yes |
| 224 | `anthropic/claude-3-7-sonnet-latest` | 3.3 | 16.5 | 19.8 | 200000 | yes |
| 225 | `anthropic/claude-4-sonnet` | 3.3 | 16.5 | 19.8 | 200000 | yes |
| 226 | `anthropic/claude-opus-5-5` | 4 | 20 | 24 | 1000000 | yes |
| 227 | `anthropic/claude-opus-4-7` | 5 | 25 | 30 | 1000000 | yes |
| 228 | `anthropic/claude-opus-4-8` | 5 | 25 | 30 | 1000000 | yes |
| 229 | `anthropic/claude-opus-5` | 5 | 25 | 30 | 1000000 | yes |
| 230 | `anthropic/claude-fable-5` | 10 | 50 | 60 | 1000000 | yes |
| 231 | `anthropic/claude-4-opus` | 16.5 | 82.5 | 99 | 200000 | yes |

## Wired into kell now

These ten are the `deepinfra` connection in `backend/deploy/provider_connections.json`. The first two reuse the
existing kell model names, so they act as a second endpoint for the same model (failover); the rest are new routable models.

| kell model | DeepInfra model | Input $/Mtok | Output $/Mtok |
|---|---|---:|---:|
| `glm-5.3-flash` | `zai-org/GLM-5.3-Flash` | 0.15 | 0.5 |
| `gemma-4-31b-it` | `google/gemma-4-31B-it` | 0.2 | 0.4 |
| `glm-5.3` | `zai-org/GLM-5.3` | 0.9 | 4 |
| `deepseek-v3.2` | `deepseek-ai/DeepSeek-V3.2` | 0.26 | 0.38 |
| `deepseek-v4-pro` | `deepseek-ai/DeepSeek-V4-Pro` | 1.3 | 2.6 |
| `deepseek-v4.1-flash` | `deepseek-ai/DeepSeek-V4.1-Flash` | 0.2 | 0.6 |
| `kimi-k2` | `moonshotai/Kimi-K2-Instruct` | 0.5 | 2 |
| `minimax-m2.5` | `MiniMaxAI/MiniMax-M2.5` | 0.15 | 1.15 |
| `qwen3-coder` | `Qwen/Qwen3-Coder-480B-A35B-Instruct` | 0.4 | 1.6 |
| `gpt-oss-120b` | `openai/gpt-oss-120b` | 0.037 | 0.17 |

Prices move; re-fetch the list before relying on a number. The kell connection declares public data classes only
(`PUBLIC_SOURCE`, `PUBLIC_DERIVED`, `GLOBAL_PROCEDURE`): DeepInfra has no signed DPA or explicit zero-retention promise in this repo.

