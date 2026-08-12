# R44L6 StatsBomb Open Data 全赛事零标签规模普查

- study_id: `r44l6_statsbomb_open_domain_census`
- purpose: 一次性识别可支持后续独立外部域平局研究的高样本量赛事；不做效果研究。
- source pin: `hudl/open-data@b0bc9f22dd77c206ddedc1d742893b3bbe64baec`
- research_only=true; formal_weight=0
- Provider/API-Football/付费API=0
- 正式model/data/config/CURRENT改动=0/0/0/0

本阶段禁止读取任何赛果标签。`competitions.json`允许读取赛事身份、赛季身份、性别、国家与赛季名。每个match容器只允许读取：`match_id`, `match_date`, `kick_off`, `competition`, `season`, `home_team`, `away_team`。禁止读取比分、胜负、净胜球、平局标签或事件结果。

输出仅包含：
- 每个competition_id/season_id的identity场数、日期范围、唯一性/身份一致性；
- 每个competition累计场数、赛季数、>=50/100/200/300场的赛季数量；
- 高规模候选清单：累计>=600场，或至少一个赛季>=300场；
- `label_fields_accessed=0`, `model_fits=0`, `thresholds_selected=0`。

本普查不消费任何标签，不构成科学PASS，也不自动选定最终研究域。后续必须再与项目历史使用记录交叉核验，并对选定域单独做lineup/XI/event schema零标签门。
