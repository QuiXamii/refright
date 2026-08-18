# refright

**Verify the authenticity of every BibTeX reference — one command, full evidence, safe auto-fix.**

`refright` checks every entry in a `.bib` **or `.bbl`** file against authoritative
databases (Crossref / OpenAlex / arXiv / DBLP / DataCite), reports wrong DOIs,
wrong page numbers, wrong years, wrong volumes/issues, missing DOIs, and
entries that cannot be found anywhere — then can fix them for you, reversibly.

零第三方依赖，纯标准库实现，Python ≥ 3.10。

## 安装（Install）

```bash
git clone <this-repo> && cd refright
pip install .          # 之后可直接用 `refright` 命令
# 或者不安装，仓库根目录下直接 python -m refright …
```

## 用法（Usage）

```bash
refright ref.bib                      # 核查 → 终端紧凑摘要 + 同目录生成 ref_refright_report.html
refright ref.bib -v                   # 终端打印完整逐条目报告（-v -q 只列问题条目）
refright ref.bib --html my.html       # 手动指定报告路径/文件名
refright ref.bib --no-html            # 不生成 HTML（CI 场景配 --json 用）
refright ref.bib --json report.json   # 机器可读报告（供 CI 消费）
refright ref.bib --workers 8          # 并发度（默认 6，--workers 1 退回串行）
```

**默认输出**：终端保持紧凑——每条问题一行（`❌ key [code] 一句话`）+ 汇总计数；
同时在与输入文件相同的目录生成 `<输入文件名去扩展名>_refright_report.html`。
长报告不刷屏终端；想看细节开浏览器，或加 `-v`。

- **进度反馈**：TTY 下单行动画进度条（已完成/总数、ETA、❌⚠️ℹ️ 实时计数）；
  非 TTY（管道/CI）每完成 10% 打一行。
- **并发核查**：默认 6 个 worker 并发，各数据源独立限速限并发
  （Crossref 6、OpenAlex 4、DBLP 2、arXiv 1）；367 条冷跑约 45 秒，
  缓存命中后十几秒。
- **退出码**：任何条目有 ERROR 时返回 `1` —— 可直接用作 pre-commit hook / CI 门禁。
- **HTML 报告**（信任层）：按 severity 过滤的仪表板、字段级 "bib 值 vs 数据库记录"
  并排对比、🔧 建议修正块（~~错误值~~ → **建议值**）、一键核实链接
  （doi.org / arxiv.org / dblp.org / openalex.org）、每条目注明核查来源。
  纯静态单文件，可直接邮件转发。示例：[有错误的报告截图](docs/report_demo_errors.png) /
  [全部通过的报告](docs/demo_clean.html) /
  [bbl 实测报告](docs/demo_bbl.html)。

### 只核查正文实际引用的条目

```bash
refright ref.bib --tex main.tex                          # 只核查 main.tex 里 \cite 到的条目
refright ref.bib --tex main.tex --tex supp.tex           # 多文件（也可传目录）
```

- 识别所有 `\cite` 族命令（`\citep`/`\citet`/`\nocite`/`\autocite`…），忽略 `%` 注释；`\nocite{*}` 视为全部引用。
- **正文引用了但 bib 里不存在的 key** → ERROR `cited-not-in-bib`（编译会产生未定义引用，退出码 1）。
- bib 里**未被引用**的条目不核查，标 INFO `not-cited`（方便清理冗余条目）。

### 直接核查 .bbl（arXiv 源码包）

很多 arXiv 源码只附编译产物 `ref.bbl` 而没有 .bib 源文件。直接传 .bbl 即可
（按扩展名或内容自动识别）：

```bash
refright tests/golden/sample.bbl
```

- REVTeX/APS 结构化 bbl（`\bibfield`/`\bibinfo` 标记）：完整抽取作者、标题、
  期刊、卷、页、年、DOI、arXiv 号；样式省略 `\bibfield{title}` 时自动回退
  抓取裸标题文本。
- 普通自由格式 bbl：尽力抽取 DOI / arXiv / 年份 / `\emph` 标题 / `\textbf` 卷号。
- `--tex` 过滤同样适用（`\bibitem{key}` 与 `\cite{key}` 对应）。
- `--fix` 不支持 .bbl（它是编译产物——请改生成它的 .bib 后重新编译）。

### 自动修复

```bash
refright ref.bib --fix                             # dry-run：列出修复项 + diff 预览，不写文件
refright ref.bib --fix --fix-out fixed.bib         # 修复结果另存新文件，原文件不动
refright ref.bib --fix --write                     # 原地修复（先自动备份 ref.bib.<时间戳>.bak）
refright ref.bib --fix --write --fix-warnings      # 连同 WARNING 级（如期号）一起修
```

安全契约：

- 只做**外科手术式**修改——定点替换/插入字段行，绝不整体重写文件（注释、格式原样保留）；
- 默认只修 ERROR 级（错 DOI、错页码/卷号/年份）和 INFO 级 `missing-doi`（补 DOI）；
  WARNING 级需显式 `--fix-warnings`；
- 原地写入必先创建时间戳备份，可用备份完整回滚；
- 修复后建议再跑一次 `refright <bib>` 复查（测试已保证修复结果零 ERROR）。

## 能抓到什么（What it catches）

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

**误报控制**：期刊缩写词干归一化（`Phys. Rev. Lett.` ≡ `Physical Review Letters`）；
任一侧缺失的字段不参与比对；卷/页一致时容忍 online-first 的 ±1 年年份差；
丛书卷号不比对；新旧 APS DOI 格式均可识别；
首页式引用静默通过（bib `pages={2863}` ≡ 记录 `2863-2866`，反之记录只给首页亦然）；
页码前导零归一化（`061` ≡ `61`）；标题反查一律走版本门 + 作者门，
命不中同版本同作者就不做字段比对，只发 WARNING；DBLP 同名多版本记录按年份一致性
优选（NIPS 2012 原文 vs CACM 2017 重印不会张冠李戴）；BibTeX `number` 自动别名到期号；
`journal={arXiv:xxxx}` 预印本引用风格自动识别并改走 arXiv 核查；
Crossref 字段先做 HTML entity unescape（`&amp;` ≡ `&`）；
网络错误/限流与"真的不存在"严格区分（429 自动退避重试）。

## 测试（验收）

```bash
python tests/run_golden.py   # 黄金集：修复前精确复现 5 处已知错误；修复版零误报；HTML 证据链完整
python tests/test_fix.py     # --fix 修复后复检零 ERROR；备份与原件逐字节一致
python tests/test_tex.py     # --tex：只核查实际引用条目；缺失 key 报 ERROR；注释不泄漏
python tests/test_bbl.py     # .bbl：63 条 REVTeX bbl 解析；2 处已知真错误精确复现；&amp; 误报回归
```

* `tests/golden/ref_fixed.bib` —— 公开经典论文合成 fixture，字段全部正确 → 必须 0 error / 0 warning
* `tests/golden/ref_broken.bib` —— 同批条目植入 4 处错误 + 1 处期号错误 + 1 处缺 DOI → 必须精确复现
* `tests/golden/sample.bbl` —— 合成 REVTeX bbl（7 条）→ 必须精确复现植入的
  2 处真错误（arXiv 编号正文/链接冲突、文章号丢首字母）+ 1 处"标题被样式省略"警告
* `tests/golden/citations.tex` —— --tex 过滤测试夹具（含注释干扰与幽灵 key）

## 结构（Layout）

```
refright/
  bibparser.py    健壮的 .bib 解析（容忍末字段无逗号、大小写 DOI 键、空值字段）
  bblparser.py    .bbl 解析（REVTeX 结构化 + 自由格式尽力抽取）
  texscan.py      扫描 .tex 提取实际 \cite 的 key（--tex 过滤）
  sources.py      Crossref / OpenAlex / DBLP / arXiv / DataCite 客户端（sqlite 缓存 + 按源限速的并发）
  match.py        标题 / 期刊 / 作者模糊匹配（期刊名采用词干归一化）
  engine.py       逐条目核查规则（含误报控制）
  fixer.py        自动修复（dry-run 预览 / 备份 / 外科手术式改写）
  report.py       终端（紧凑/完整）+ JSON 报告
  report_html.py  单文件 HTML 人工核查报告
  cli.py          refright / python -m refright 入口
```

缓存：`~/.cache/refright/cache.sqlite`（TTL 7 天，`--no-cache` 绕过）。

## License

MIT
