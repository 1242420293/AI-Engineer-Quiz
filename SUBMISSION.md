# 素材检索与结果导出

Fork：https://github.com/1242420293/AI-Engineer-Quiz  
开发分支：`dev1`；最终 commit SHA 随提交结果提供。

## 运行

需要 Python 3.10+；本机实测 Windows、Python 3.12.6。在仓库根目录执行 PowerShell：

```powershell
# 已有虚拟环境时跳过第一行
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:KEEPSTACK_AI_ENABLED = "false"
$env:KEEPSTACK_OCR_ENABLED = "false"
$env:KEEPSTACK_HOST = "127.0.0.1"
$env:KEEPSTACK_DATA_DIR = "$PWD\data\quiz-demo"
.\.venv\Scripts\python.exe -m keepstack.quiz_seed
.\.venv\Scripts\python.exe -m keepstack
```

访问 http://localhost:8000，账号 `admin` / `admin`。专项生成器创建 45 个素材和 `Quiz Harbor Campaign` 集合，包含组合标签、相同创建时间及特殊字符；重复运行不会重复插入素材。

## 验证步骤

1. 搜索 `harbor`，选择上述集合；Include ALL tags 添加 `approved`、`campaign`，Exclude ANY tags 添加 `internal`，应匹配 **38 条**。
2. 每页 20 条，第二页显示第 21–38 条；切换 Oldest / Newest 后回第一页，同时间记录按唯一 ID 排序。选择 image 后应为 **31 条**。
3. 从第二页导出 CSV，应包含全部匹配记录，顺序与列表一致；检查中文、逗号、引号和换行。
4. 将 `approved` 同时加入排除条件，应为 0 条，CSV 仍有表头。纯标点关键词如 `!!!` 也返回空结果。
5. 编辑标题、描述后点击 Save changes；添加标签点击 Add tag 或按 Enter，删除点击标签旁的 ×。使用新标题、描述或标签中的独特关键词检索并导出，确认修改生效、删除标签后不再匹配。
6. 快速切换关键词，确认最终结果对应最新条件；逐个移除条件或点击 Clear all，确认条件清除。

自动化验证（前端测试另需 Node.js 18+，无需 npm 安装）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check web/app.js
node --test tests/test_web.cjs
```

## 实测结果

- Python：**40 passed, 2 skipped**；跳过可选 CLIP、OCR 测试，仍有框架弃用提示。
- JavaScript：**6 passed**，语法检查通过；实测 Node.js 18.20.4。
- 浏览器已验证登录、组合筛选、翻页、排序重置、类型筛选、空结果、清除条件、标签添加删除和 CSV 下载。从第二页实际下载并解析 CSV，确认 38 条、顺序和特殊字符正确。
- 后端测试使用独立的 47 条内存数据，覆盖组合筛选、时间并列、多页与导出一致性、鉴权、非法参数及编辑后索引更新；前端测试覆盖请求竞态、分页重置、下载失败提示及标签表单。

## 主要取舍

- 保留 FastAPI、SQLite / FTS5 和原生 JavaScript，未增加运行依赖。列表与 CSV 共用筛选和排序规则，仅返回 active 素材；筛选使用参数化 SQL 与 EXISTS，时间排序增加唯一 ID 保证稳定。
- 沿用原 FTS 索引字段、分词器和前缀检索；空白关键词不限制，无法构成检索词的非空输入返回空结果。页面隐藏语义搜索入口。
- 标签完整名称匹配，去首尾空白并转小写；包含标签全部满足，排除标签命中任意一个即排除，重复标签不影响结果。侧栏计数明确表示全库 active 素材数量。
- CSV 沿用 Bearer 登录鉴权，使用标准库和 UTF-8 BOM；`tags` 为按名称排序的 JSON 数组字符串，空标签为 `[]`。只导出清单，不包含原文件。

## 限制及未完成项

- 同步导出在内存中生成，未做超大数据集基准、流式或异步导出；跨请求一致性按数据不变验收，不提供快照。
- 需求范围内无已知未完成项。模型接入、实时订阅和线上部署不在本次范围，范围外模块未全面审计。
- CI 已配置 Python 3.10/3.12 与 Node 22，远程运行结果尚未验证。最终提交、提供 SHA、推送及确认 reviewer 访问权限待完成。
- 数据库、上传文件、日志、虚拟环境和 IDE 配置均被 Git 忽略。
