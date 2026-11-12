# MathType 转换增强合同

日期2026-11-12，对应FR-15、DEV-004/404，以及用户本轮增强LaTeX转换的明确要求。沿用原生结构→MathML→LaTeX→XeLaTeX路径，不以图片、OCR、LLM猜写或丢弃未知字符代替转换。本批不新增业务模型或题库人工审核，不自动提交。

## 固定范围与归属

先验分母为上一轮真实失败10题中的53个不同OLE对象，7个unsupported（6个unmapped_private_mtef_character、1个unmapped_mtef_character），涉及6题、8次失败出现。原10题顺序、题目内容、答案与原件保持；该分母不是全部题库。私有baseline转换器、输入SHA、原失败清单均先冻结，禁止覆盖。

Root负责合同、集成、真实导出及说明；Luna/high A负责math_latex.py和test_math_latex.py内的安全映射与结构正反例；Luna/high B负责独立全库基线/候选扫描与诊断工具（scripts/diagnose_latex_bank.py，独立脚本不改转换器）；Sol/high独立核对新支持对象与原图、反例、实际PDF及旧源保护。实际可用Luna为gpt-6-luna，不改称6.1 Luna。

实际PDF检查补充：首版重放暴露作答区整体移到下一页而题干停在页尾。Root另负责latex_exporting.py的局部不分页约束和模板版本更新，保持公式、题单、字体及紧凑/教师正文不变；以第二版实际PDF重新核验。

## 转换约束

build_formula_catalog、validate_formula_catalog和FormulaConversion合同不变。新增编码支持须绑定可证明的MTEF记录、字体/字符身份或模板槽位；不得将全部EC07/EC08或未知PUA统一删除。不得放宽未知record、template variant、消费完整性、TeX命令或XML边界。转换规则修订须更新converter身份，旧目录和旧PDF不重写；新候选重新构建并验证目录。共享调用方Root CLI、Web Worker与导出器均须知悉身份变化。

## 验收

1. 原7个对象逐一定位到原DOCX OLE与预览，候选转换7/7成功；分式、根号、幂、正负号、绝对值、不等号等语义独立对照。未知相似结构仍须明确拒绝，不能依靠样本SHA例外。
2. 原10题全部学生与教师公式可用，保持题单，题答LaTeX实际编译并逐页查看；几何图仍按原图保存，最终公式不可用原PNG替代。
3. 用冻结旧转换器与候选对同一题库进行只读扫描，按不同原生对象、出现次数、题目分别给出分母和剩余错误，不以单一样卷推导全库支持。题库原件和旧产物不变。
4. 既有187次/100对象的通过样卷重建，新旧已成功对象MathML及LaTeX语义不漂移；结构/安全反例及make check通过。局部改动无需重做无关前端功能。
5. 代码、合成测试及摘要证据可入源码；原卷、原生真实对象、预览、扫描报告及导出留在忽略目录。不能把原解析正确性、教师满意度或任意Word文档支持视为通过。
