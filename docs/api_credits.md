# SwarmLabs 模型 API / 算力免费额度申请追踪（本地，不进 git）

> 用途：集中管理可申请到的免费模型额度与 GPU 算力，直接降低 BYO-LLM 默认通道成本、补充训练/基准算力。
> 申请需用户本人实名/手机号，AI 无法直接代申请。
> 2026-08-07 全网复核更新：补全国内主流模型平台 + 海外免费层 + 初创算力资助 + 开源基金。
> 状态：全部"待申请"（除 Cloudflare 已在用）。

## 模型 API 免费额度（注册即得）
| 平台 | 免费额度 | 申请入口 | 状态 | 备注 |
|---|---|---|---|---|
| 智谱 GLM | 新用户 2000万 Tokens；GLM-4-Flash / 4.7-Flash 永久免费 | https://open.bigmodel.cn | 待申请 | 中文推理默认之一；30 并发 |
| 硅基流动 SiliconFlow | 新用户 2000万 Tokens，9B 以下模型永久免费 | https://siliconflow.cn | 待申请 | 国内低延迟；GLM-4-Flash 免费 |
| 阿里云百炼 | 每模型 100万（累积可超 7000万）；Qwen-Turbo 永久每月100万 | https://www.aliyun.com/product/bailian | 待申请 | 模型最全；学生 300 元券 |
| 火山引擎（豆包） | 每模型 50万；协作计划每日 200万（刷新） | https://www.volcengine.com/product/ark | 待申请 | 定时脚本"永动机" |
| 腾讯混元 | 通用 100万 + Embedding 100万；Lite 永久免费 | https://cloud.tencent.com/product/hunyuan | 待申请 | 有效期 1 年 |
| 百度千帆 | 每模型 100万；ERNIE-Speed/Lite 永久免费 | https://cloud.baidu.com/product/wenxinworkshop | 待申请 | 多模型独立额度 |
| 科大讯飞星火 | 每模型 20万；Lite 不限量 | https://xinghuo.xfyun.cn | 待申请 | 中文理解 |
| 国家超算平台 | 1000万 Tokens | https://www.scnet.cn | 待申请 | MiniMax/DeepSeek/Qwen |
| 魔搭社区 ModelScope | 每天 2000 次调用 | https://modelscope.cn | 待申请 | 需绑定阿里云 |
| 美团 LongCat | 每日 5500万 Tokens（刷新） | 官方为准 | 待申请 | 高吞吐 |
| Cloudflare Workers AI | 每天 10,000 次 | 已在用 | **已用** | 边缘轻量模型 |
| Google Gemini API | 免费层每日 1440 次 | https://ai.google.dev | 待申请 | 多模态；需海外网络 |
| Groq | 每日 14,400 次 | https://console.groq.com | 待申请 | <100ms 延迟 |
| OpenRouter / GitHub Models | 每日免费请求 | https://openrouter.ai / github.com/marketplace/models | 待申请 | 模型聚合 |

## 免费 GPU 算力
| 平台 | 免费算力 | 申请入口 | 状态 | 备注 |
|---|---|---|---|---|
| Kaggle | 30 GPU 小时/周 | https://www.kaggle.com | 待申请 | 跑 benchmark / 复现 |
| 百度 AI Studio | 免费 V100（白名单） | https://aistudio.baidu.com | 待申请 | PINN / 飞桨 Notebook 开发 |
| 阿里云 PAI | 企业级免费试用 | https://www.aliyun.com/product/bigdata/learn | 待申请 | 模型微调 |
| 华为云 ModelArts | 免费试用 | https://www.huaweicloud.com/product/modelarts | 待申请 | — |
| 国家超算互联网 | 师生额度 | https://www.scnet.cn | 待申请 | — |
| Google Colab / Modal | T4 免费层 / $30 月免费 | colab.google.com / modal.com | 待申请 | 海外训练备选 |

## 初创企业算力资助（需公司实体，后续申请）
| 项目 | 额度 | 入口 | 状态 | 门槛 |
|---|---|---|---|---|
| Microsoft Founders Hub | 最高 $150k（入门 $1k–5k） | foundershub.microsoft.com | 待申请 | 无需 VC |
| NVIDIA Inception | $10k–100k | nvidia.com/inception | 待申请 | AI 产品 + 官网 |
| DigitalOcean Hatch | $100k | digitalocean.com | 待申请 | 无 VC |
| Google Cloud for Startups | 最高 $350k | cloud.google.com/startups | 待申请 | 需 VC |
| AWS Activate | 最高 $200k | aws.amazon.com/activate | 待申请 | VC / Inception |

## 开源基金 / 社区资助（长期可争取）
| 基金 | 额度 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| NLnet | €5k–50k | nlnet.nl/propose | 待申请 | 夏季后重开 open call |
| Sovereign Tech Agency | 不定 | sovereign.tech | 待申请 | 关键基础设施 |
| Trelis AI Grants | $500 | trelis.com | 待申请 | AI 个人资助 |
| GitHub Sponsors / Open Collective | 社区捐助 | github.com/sponsors | 待申请 | README 放赞助入口 |

## 申请优先级（建议今天起逐个注册）
1. 智谱 GLM / 硅基流动（2000万永久，最划算）
2. 阿里云百炼（额度最大 + 学生券）
3. 火山引擎（每日 200万刷新）
4. 腾讯混元 / 百度千帆 / 讯飞（多平台冗余）
5. 国家超算 / 魔搭（补充）
6. Kaggle（免费 GPU 跑基准）
7. 海外：Gemini / Groq / OpenRouter（多模态与低延迟）
8. 公司成立后：Founders Hub → Inception → Google/AWS
9. 开源：NLnet 重开后投递工具层项目
