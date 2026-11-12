"""執行期數字檢查：回答裡出現的數字，必須能在本輪工具結果或使用者問題中找到。

抓的是「模型自己算出來或編出來的數字」（例如把 96.47% 自己四捨五入成 96.5%、把件數換算成萬件）。
口徑是否說反由 eval 的 LLM-as-judge 檢查，這裡只做零成本的確定性檢查。
"""

import re

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 條次、序號、月份日期這類小整數太常見，不列入檢查
SMALL = 31


def _norm(s: str) -> str:
    s = s.replace(",", "")
    return s.rstrip("0").rstrip(".") if "." in s else s


def numbers(text: str) -> set[str]:
    return {_norm(m) for m in _NUM.findall(text)}


PLACEHOLDER = re.compile(r"[\[【［][^\]】］]{0,20}(數值|結果|餘額|金額|日期|時數)[^\]】］]{0,20}[\]】］]")


# 完全沒查資料卻在談指標：除非是拒絕或反問，否則就是憑空下結論（數字檢查抓不到「東莞廠最高」這種沒有數字的說法）
METRIC_WORDS = re.compile(r"良率|直通率|不良率|準交率|進料不良|報廢|出貨量|投入數")
DECLINE = re.compile(r"權限|範圍|無法|不能|不會提供|看不到|[?？]")


def untooled_claim(answer: str, called_tools: bool) -> bool:
    return not called_tools and bool(METRIC_WORDS.search(answer)) and not DECLINE.search(answer)


def ungrounded(answer: str, sources: list[str]) -> list[str]:
    """回傳有問題的片段；空 list 代表通過。"""
    problems: list[str] = []
    if PLACEHOLDER.search(answer):
        problems.append("placeholder")
    allowed: set[str] = set()
    for s in sources:
        allowed |= numbers(s)
    out = []
    for n in numbers(answer):
        if n in allowed:
            continue
        try:
            if float(n) <= SMALL and float(n).is_integer():
                continue
        except ValueError:
            continue
        out.append(n)
    return problems + sorted(out)
