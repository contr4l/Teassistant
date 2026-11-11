# 关联核验 V3 合同与固定分母

日期：2026-11-11。用户明确要求现有 200 道题核验通过率达到 95% 以上，本批定义为至少 **190 / 固定 200** 有证据确认。对应 FR-02 题答关联，不修改老师价值通过标准，不等于答案数学正确率、正式题库 ready 或全部 PRD 验收。

## 固定评测与独立检查

独立检查者 `gpt-6.1-sol` / `high` 在实施前固定 `work/association-v3/review-manifest.json`：200 个唯一题目 ID、8 对各 25 题、16 个原件摘要及来源定位。manifest SHA-256 为 `652f17654d0da9748bfdc8562af2e73f0252d8c78673c7790c6d316b2b7eb33c`。不覆盖 manifest，不把缺失、失败、未确认、重复或不支持题移出分母，也不加入替代题凑数。

通过需同时满足：固定 200 题中至少 190 题有据确认；独立的数字、小数、正负号、上下标、分式、图形及顺序负例没有误确认。不得降低图像相似度阈值或直接改状态来满足指标。

## 证据层级

先前 V2 实测：文字一致，但只有 47 题的全部有序图像原尺寸 RGBA 相同。Root 本批从原件读取 Equation Native 流，发现 1249 对对应的完整原生 MTEF payload 逐字节相同；普通配图有 10 对不同，涉及 7 题。此为实施前只读调查，不当作 V3 已达到指标的实测结果。

V3 使用以下确定性证据，不执行模型调用：

1. 保留有序文字、表格、公式和媒体位置；只做安全 NFC/全角 ASCII 及空白处理，不把 `²` 变成 `2`，不删除小数点或符号。
2. 普通图片保持原宽高及解码 RGBA 精确比较；不同几何图不因同题号、文字或公式相同而通过。
3. 对唯一绑定的 OLE 预览，检查只读 CFB 容器、唯一顶层 Equation Native 流、28 字节包装头、版本和对象长度以及可识别的 MTEF 版本头。检查通过后，对 **整个原生 payload 的全部字节** 求 SHA-256；不去掉未知记录，不删数学字段，不提取少数相同字符冒充等价。
4. 两边有效原生 payload 相同，可用 `native_mtef_exact` 作为该公式的源对象身份依据，预览的尺寸差异仍保留在证据中。若两边有效原生 payload 不同，即使预览图片相同也不能确认；无法利用原生证据时保留既有精确图像通路及失败原因。
5. OMML 仍检查完整结构；结构或位置变化不能因拼接文字相同通过。任何未绑定媒体、遗漏对象或未知差异保留未确认。

原生字节身份是 **source-object identity**，不是 AST 语义解析。包装头/版本/长度检查不能被描述为完整 MTEF 记录语法校验，末尾 END 字节也不能证明任意未知记录语法正确。全部未知记录字节仍参与精确身份比较。

该证据沿用题源内容质量合格的业务前提，保留原图与原对象；不宣称已独立证明每张 PNG 的数学语义。厂商记录过损坏文档中原生数据与显示数据不同步的情况，若发现实际冲突必须保持未确认。[Wiris 说明](https://docs.wiris.com/interface-and-usability/a-different-equation-opens-in-mathtype-than-the-one-double-clicked-in-word)

## 接口与文件归属

- 实现 A：实际 `gpt-6-luna` / `high`，新增 `native_equation.py` 和对应测试。`extract_native_equation(ole_bytes)` 返回 status、method、payload_sha256、payload_size、mtef_version、ole_sha256、reason_code。method 为 `native_mtef_exact_v1`；无效或无法检查返回 unavailable，不读环境、不联网、不执行对象。
- 实现 B：实际 `gpt-6-luna` / `high`，新增 `source_association.py` 和测试。`compare_association(source_blocks, analysis_blocks, root)` 返回 confirmed/unverified 及可追溯的文本、结构和逐媒体证据，保留 V2 的基本证据字段。
- Root：导入器接入、CLI/固定评测报告、摘要/说明和集成。每个 OLE occurrence 保存 native_equation 签名，不保存凭据；来源输出升级 `source-bank-v3`，原件、V1 和 V2 不覆盖。
- 独立检查者：新的关联验收测试、固定分母真实重算及独立报告；不接受实现者自报计数替代实际原件检查。

新增 `olefile` 已同步 `pyproject.toml` 与 `uv.lock`，使用根 uv 环境。容器读法依据 [olefile 官方 API](https://olefile.readthedocs.io/en/latest/Howto.html)，不自行执行嵌入对象或转换宏。

## 交付状态

固定评测报告分别显示 confirmed、unverified、failed、missing、extra、duplicate，计算 confirmed / 200。题目还必须结构成功且有可用答案才能计作本批通过；新增字段不能伪造真实难度或统一考点。

即使确认率达到 95%，只要仍有未确认题，来源批次仍可为 partial、构建命令仍按原约定退出 3；固定评测达标与“整批无异常”是不同状态。正式组卷资格、难度评分和模型调用继续独立记录。
