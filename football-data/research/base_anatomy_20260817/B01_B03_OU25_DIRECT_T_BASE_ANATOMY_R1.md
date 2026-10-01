# B01-B03 OU2.5 → Direct-T 底座解剖 R1

状态：`POSTVIEW_BASE_ANATOMY_COMPLETE_NO_PROMOTION`  
范围：B01/B02/B03，1200 场；B04 分析行数 = 0；research-only；formal_weight=0。

## 1. 核心结论

400 场单包确认不稳定的主因不是 calendar-date clustering。B01/B02/B03 的 cluster design effect 分别为 0.934/0.944/0.983，都没有方差膨胀。真正瓶颈是净效应极小：三包标准化效应仅 0.050/0.095/0.058。

B01-B03 描述性合并：ΔLogLoss=-0.008626，date-block bootstrap90=[-0.014553,-0.002656]。该合并是 post-view 描述，不是事后 confirmatory gate。

## 2. 底座机制：0-3 与 4+ 相互抵消

- 0-3 球：n=799，mean ΔLL=+0.013766（candidate 更差）
- 4+ 球：n=401，mean ΔLL=-0.053242（candidate 明显更好）
- 三个 batch 都出现相同方向。

因此 OU2.5 不是均匀改善 T，而更像 high-total / upper-tail correction。净收益只是两个更大、方向相反分量相消后的残差。

## 3. 日期簇 / 有效样本量

| batch | n | date blocks | ΔLL | cluster SE | design effect | effective N | neg-date rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| B01 | 400 | 150 | -0.006382 | 0.006149 | 0.934 | 428.2 | 54.7% |
| B02 | 400 | 156 | -0.011530 | 0.005879 | 0.944 | 423.6 | 60.9% |
| B03 | 400 | 151 | -0.007965 | 0.006856 | 0.983 | 407.0 | 59.6% |

单日删除（leave-one-date-out）不会翻转三包点估计方向；但 date mean 的 10%-90% 区间很宽，说明日际结果噪声大，而不是同日相关性导致有效样本量坍塌。

## 4. 功效

以 B01-B03 pooled ΔLL=-0.008626 作为纯描述性效应假设，并保持当前 cluster variance：

- 400 场：power ≈ 40.4%
- 800 场：≈ 63.3%
- 1200 场：≈ 78.4%
- 1600 场：≈ 87.7%
- 1800 场：≈ 90.8%
- 80% power：约 1257 场
- 90% power：约 1741 场
- 95% power：约 2200 场

这解释了 400 场为什么会出现 B02 PASS 而 B01/B03 CI 跨 0：单包本身就是低功效设计。

## 5. 报价时间 / 异步

- <=1min：n=566，ΔLL=+0.001763，bootstrap90=[-0.006424,0.010148]
- >6h：n=586，ΔLL=-0.017498，bootstrap90=[-0.026887,-0.008353]

但 quote-age Pearson/Spearman 与 ΔLL 几乎为 0。>6h 指示变量在控制 batch/competition/baseline/shift 后仍为负；再控制 realized 4+ outcome 后系数从 -0.01666 缩至 -0.01126 且 p=0.124。结论：这是 timing/regime composition 信号，不是“旧报价更好”的因果证据。

## 6. 联赛异质性（仅描述）

| competition | n | pooled ΔLL | 3 batch negative | bootstrap90 |
|---|---:|---:|---:|---|
| PREMIER LEAGUE | 259 | -0.018629 | 3/3 | [-0.03476,-0.00317] |
| LIGA | 227 | -0.017363 | 3/3 | [-0.03206,-0.00263] |
| BUNDESLIGA | 184 | -0.008939 | 3/3 | [-0.02703,+0.00816] |
| SERIE A | 224 | -0.005021 | 3/3 | [-0.01460,+0.00464] |
| EUROPA LEAGUE | 79 | -0.003797 | 2/3 | [-0.02334,+0.01668] |
| CHAMPIONS LEAGUE | 95 | +0.003431 | 2/3 | [-0.01629,+0.02336] |
| LIGUE 1 | 132 | +0.008782 | 0/3 | [-0.00496,+0.02434] |

PL 与 Liga 的 pooled descriptive CI 在 0 以下；Ligue 1 三包都为正。与此同时 competition joint robust Wald p=0.402，不足以把联赛差异升级成 selector。

## 7. 研究裁决

1. 不把 pooled 1200 的显著性改写成 confirmatory PASS。
2. 不在 B01-B03 上按联赛、同步时差或 4+ 标签后验造 selector。
3. 当前底座问题是 **单一 OU2.5 cut point 只能移动总进球强度，无法稳定描述 tail shape，导致 0-3 损失与 4+ 收益相消**。
4. 后续如果有合规数据，研究问题应是“更丰富的 total-shape 信息能否减少这种相消”，而不是增加 gold-standard reserve 或继续机械开 400 场包。

## 8. 数据来源与边界

- post-view source run: `32000375197`
- post-view artifact: `9278178012`
- artifact ZIP SHA-256: `4af1f68c4d7769ffc957fbd93d1d9c0102a4e4275ab41842e027686d601d6f73`
- B01 freeze: run `31993949268`, artifact `9276244641`
- B02 freeze: run `31995652017`, artifact `9276762428`
- B03 freeze: run `31997077090`, artifact `9277190607`

Combined viewed-only CSV 在进入统计分析前按 batch 流式过滤，只写入 B01/B02/B03 共 1200 行；B04 行未进入分析 DataFrame。无训练、无参数搜索、无 selector、无 formal model/data/config/CURRENT 变更。
