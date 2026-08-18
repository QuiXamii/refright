# refright 中文文档

`refright` 核查 `.bib` 或 `.bbl` 文件里的每一条文献，逐条对照 Crossref、OpenAlex、arXiv、DBLP、DataCite 五个数据库。DOI 不存在、DOI 解析到了另一篇论文、页码错、卷号错、arXiv 编号指向别人的预印本、所有数据库都查不到的条目，都会被抓出来。机械性的错误还能自动修。

零第三方依赖，纯标准库，Python ≥ 3.10。

## 安装

```bash
git clone https://github.com/QuiXamii/refright
cd refright
pip install .          # 之后可以直接用 refright 命令
# 不安装也行：在仓库根目录下 python -m refright …
```

## 用法

```bash
refright ref.bib                      # 核查，终端只给紧凑摘要，同目录生成 ref_refright_report.html
refright ref.bib -v                   # 终端打印完整逐条目报告（-v -q 只列问题条目）
refright ref.bib --html my.html       # 手动指定报告路径和文件名
refright ref.bib --no-html            # 不生成 HTML（CI 场景配 --json 用）
refright ref.bib --json report.json   # 机器可读报告
refright ref.bib --workers 8          # 并发度（默认 6，--workers 1 退回串行）
```

终端输出刻意保持短：每条问题一行（`❌ key [code] 一句话`），最后一行汇总计数。证据都进了 HTML 报告，默认写在与输入文件相同的目录，文件名是输入名加 `_refright_report` 后缀。报告里每条发现都有字段级的"bib 值 vs 数据库记录"对照、🔧 建议修正（~~错误值~~ → **建议值**）和一键核实链接，每条目标明是哪个数据源核查的。纯静态单文件，可以直接邮件转发。示例：[有错误的报告](docs/demo_broken.html)、[全部通过的报告](docs/demo_clean.html)、[bbl 实测报告](docs/demo_bbl.html)。

核查是并发的，各数据源独立限速（Crossref 6 并发、OpenAlex 4、DBLP 2、arXiv 1）。367 条的文献库冷跑约 45 秒，缓存命中后十几秒。TTY 下有单行动画进度条（含 ETA 和 ❌⚠️ℹ️ 实时计数），管道或 CI 里每完成 10% 打一行，大文献库不会看起来卡死。任何条目有 ERROR 时退出码为 1，可以直接当 pre-commit hook 或 CI 门禁用。

### 只核查正文实际引用的条目

```bash
refright ref.bib --tex main.tex                          # 只核查 main.tex 里 \cite 到的条目
refright ref.bib --tex main.tex --tex supp.tex           # 多文件，也可以传目录
```

所有 `\cite` 族命令都算数（`\citep`/`\citet`/`\nocite`/`\autocite`…），`%` 注释会被忽略，`\nocite{*}` 视为全部引用。正文引用了但 bib 里不存在的 key 报 ERROR `cited-not-in-bib`，因为编译时会产生未定义引用。bib 里没人引用的条目不核查，标 INFO `not-cited`，方便顺手清理冗余。

### 直接核查 .bbl（arXiv 源码包）

很多 arXiv 源码只附编译产物 `ref.bbl`，没有 .bib 源文件。直接传 .bbl 即可，工具按扩展名或内容自动识别：

```bash
refright tests/golden/sample.bbl
```

REVTeX/APS 结构化 bbl（`\bibfield`/`\bibinfo` 标记）能完整抽取作者、标题、期刊、卷、页、年、DOI、arXiv 号；样式省略 `\bibfield{title}` 时回退抓取裸标题文本。自由格式的普通 bbl 尽力抽取 DOI、arXiv 号、年份、`\emph` 标题、`\textbf` 卷号。`--tex` 过滤同样适用（`\bibitem{key}` 和 `\cite{key}` 对应）。`--fix` 不支持 .bbl：它是编译产物，请改生成它的 .bib 再重新编译。

### 自动修复

```bash
refright ref.bib --fix                             # dry-run：列出修复项和 diff 预览，不写文件
refright ref.bib --fix --fix-out fixed.bib         # 修复结果另存新文件，原文件不动
refright ref.bib --fix --write                     # 原地修复（先自动备份 ref.bib.<时间戳>.bak）
refright ref.bib --fix --write --fix-warnings      # 连同 WARNING 级（如期号）一起修
```

修复只做外科手术式的修改：定点替换或插入字段行，绝不整体重写文件，注释和格式原样保留。默认只修 ERROR 级（错 DOI、错页码/卷号/年份）和 INFO 级 `missing-doi`（补 DOI），WARNING 级要显式加 `--fix-warnings`。原地写入必先创建时间戳备份，可以用备份完整回滚。修完建议再跑一次 `refright <bib>` 复查。

## 能抓到什么

| finding code                     | severity | 含义                                               | 可自动修复 |
| -------------------------------- | -------- | -------------------------------------------------- | ---------- |
| `doi-unresolvable`               | ERROR    | DOI 404；按标题反查（Crossref→OpenAlex）给出正确 DOI | ✅          |
| `title-mismatch`                 | ERROR    | DOI 解析到了另一篇论文（张冠李戴）                  | ❌ 需人工   |
| `pages-mismatch`                 | ERROR    | 页码/文章号与出版方或 DBLP 记录不符                 | ✅          |
| `volume-mismatch` / `year-mismatch` | ERROR | 卷号/年份与记录不符                                 | ✅          |
| `arxiv-id-not-found` / `-mismatch` | ERROR  | arXiv 编号不存在 / 指向别的论文                     | ❌ 需人工   |
| `arxiv-id-conflict`              | ERROR    | 同一条目出现两个不同 arXiv 编号（正文与链接不一致） | ❌ 需人工   |
| `cited-not-in-bib`               | ERROR    | 正文 `\cite` 了该 key，但 bib 中不存在此条目        | ❌ 需人工   |
| `issue-mismatch`                 | WARNING  | 期号与记录不符                                      | ✅（需 --fix-warnings） |
| `journal-mismatch` / `author-mismatch` | WARNING | 期刊名/第一作者与记录不符                     | ❌          |
| `duplicate-key`                  | WARNING  | 同一 key 在 bib 中重复定义，需人工合并              | ❌          |
| `possible-version-mismatch`      | WARNING  | 标题反查命中年份差 >1 年（可能重印版/不同版本）     | ❌          |
| `unreliable-title-match`         | WARNING  | 标题反查命中但第一作者不符（可能同名不同论文）      | ❌          |
| `doi-check-failed`               | WARNING  | 网络错误/限流导致核查失败（不是 404），建议重试     | ❌          |
| `arxiv-check-failed`             | WARNING  | arXiv API 批量请求失败，无法确认编号，建议重试      | ❌          |
| `year-online-first`              | WARNING  | 与在线首发年份差 1 年（可能是正式卷期年份）         | ❌          |
| `missing-doi`                    | INFO     | 期刊论文缺 DOI（附建议值）                          | ✅          |
| `doi-url-prefix`                 | INFO     | DOI 字段写成完整 URL，建议只留本体                  | ✅          |
| `datacite-doi`                   | INFO     | DOI 注册在 DataCite（figshare/Zenodo 等），确认存在 | ❌          |
| `published-version-available`    | INFO     | arXiv 预印本已有正式出版版本                        | ❌          |
| `not-cited`                      | INFO     | 条目未被 --tex 指定的文档引用，未核查               | ❌          |
| `not-found-in-databases`         | WARNING  | 任何数据库都检不到，必须人工核查                    | ❌          |

## 误报控制

会乱叫的工具没人愿意用，所以大量代码花在"不报错"上。期刊名做词干归一化（`Phys. Rev. Lett.` 就是 `Physical Review Letters`）；任何一侧缺失的字段不参与比对；卷或页一致时容忍 online-first 的 ±1 年年份差；丛书卷号不比对；新旧 APS DOI 格式都认。首页式引用静默通过（bib `pages={2863}` 等于记录 `2863-2866`，记录只给首页也认），页码前导零归一化（`061` 就是 `61`）。标题反查一律走版本门和作者门，命不中同版本同作者就不做字段比对，只发 WARNING。DBLP 对重印版有独立记录，按年份一致性优选，NIPS 2012 原文不会被 2017 年 CACM 重印顶掉。BibTeX 的 `number` 自动别名到期号；`journal={arXiv:xxxx}` 预印本引用风格自动改走 arXiv 核查；Crossref 字段先做 HTML entity unescape（`&amp;` 就是 `&`）。网络错误和限流只报"核查失败请重试"，绝不伪装成 404。

## 测试

```bash
python tests/run_golden.py   # 黄金集：正确 bib 零误报；植入错误精确复现；HTML 证据链完整
python tests/test_fix.py     # --fix 修复后只剩需要人工的 ERROR；备份与原件逐字节一致
python tests/test_tex.py     # --tex：只核查实际引用条目；幽灵 key 报 ERROR；注释不泄漏
python tests/test_bbl.py     # .bbl：合成 REVTeX fixture 解析；植入错误精确复现
```

fixture 全部用公开经典论文合成，期望稳定：`ref_fixed.bib` 必须 0 error / 0 warning；`ref_broken.bib` 植入 4 处错误、1 处期号错误、1 处缺 DOI，必须精确复现；`sample.bbl` 植入 arXiv 编号正文/链接冲突和文章号丢首字母各一处，外加一条"标题被样式省略"的警告；`citations.tex` 是 `--tex` 过滤的夹具，含注释干扰和幽灵 key。

## 结构

```
refright/
  bibparser.py    .bib 解析（容忍末字段无逗号、大小写 DOI 键、空值字段）
  bblparser.py    .bbl 解析（REVTeX 结构化 + 自由格式尽力抽取）
  texscan.py      扫描 .tex 提取实际 \cite 的 key（--tex 过滤）
  sources.py      五个数据源的客户端（sqlite 缓存 + 按源限速的并发）
  match.py        标题 / 期刊 / 作者模糊匹配（期刊名词干归一化）
  engine.py       逐条目核查规则和误报门控
  fixer.py        自动修复（dry-run 预览 / 备份 / 外科手术式改写）
  report.py       终端（紧凑/完整）+ JSON 报告
  report_html.py  单文件 HTML 人工核查报告
  cli.py          refright 命令入口
```

缓存：`~/.cache/refright/cache.sqlite`（TTL 7 天，`--no-cache` 绕过）。

## License

MIT
