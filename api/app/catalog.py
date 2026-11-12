"""Arcwell Electronics 的主檔：事業群、廠區、產線、產品、不良代碼、客戶、供應商。全部虛構。"""

BUSINESS_GROUPS = {"PWR": "電源事業群", "OPT": "光電事業群"}

PLANTS = {"TW": "台灣桃園廠", "CN": "中國東莞廠", "VN": "越南北寧廠"}

# 產線 → (廠區, 事業群, 可生產的產品族)
LINES = {
    "TW-SMT1": ("TW", "PWR", ("server_psu", "adapter")),
    "TW-ASY1": ("TW", "PWR", ("server_psu", "adapter")),
    "TW-SMT2": ("TW", "OPT", ("led_module", "auto_lamp")),
    "CN-SMT1": ("CN", "PWR", ("adapter", "server_psu")),
    "CN-ASY1": ("CN", "PWR", ("adapter",)),
    "CN-SMT2": ("CN", "OPT", ("led_module",)),
    "CN-ASY2": ("CN", "OPT", ("auto_lamp", "led_module")),
    "VN-SMT1": ("VN", "OPT", ("led_module", "auto_lamp")),
    "VN-SMT2": ("VN", "PWR", ("adapter", "server_psu")),
    "VN-ASY1": ("VN", "PWR", ("adapter",)),
}

# 產品族 → (中文名稱, 事業群, 基準良率, 工單投入量範圍)
FAMILIES = {
    "server_psu": ("伺服器電源", "PWR", 0.945, (100, 600)),
    "adapter": ("筆電變壓器", "PWR", 0.985, (500, 2000)),
    "led_module": ("LED 模組", "OPT", 0.972, (400, 1600)),
    "auto_lamp": ("車用照明", "OPT", 0.958, (150, 800)),
}

PARTS = {
    "SP-1200A": "server_psu", "SP-1600B": "server_psu", "SP-2000C": "server_psu", "SP-2400D": "server_psu",
    "SP-3000E": "server_psu",
    "AD-065A": "adapter", "AD-090B": "adapter", "AD-120C": "adapter", "AD-180D": "adapter", "AD-240E": "adapter",
    "LM-24V1": "led_module", "LM-24V2": "led_module", "LM-48V1": "led_module", "LM-48V2": "led_module",
    "LM-96V1": "led_module",
    "AL-HL01": "auto_lamp", "AL-HL02": "auto_lamp", "AL-TL01": "auto_lamp", "AL-TL02": "auto_lamp",
    "AL-DRL1": "auto_lamp",
}

# 不良代碼 → (類別, 說明)
DEFECT_CODES = {
    "SLD-01": ("焊接", "錫橋"), "SLD-02": ("焊接", "空焊"), "SLD-03": ("焊接", "錫珠"),
    "CMP-01": ("零件", "缺件"), "CMP-02": ("零件", "極性反"), "CMP-03": ("零件", "零件損壞"),
    "APR-01": ("外觀", "刮傷"), "APR-02": ("外觀", "異物"), "APR-03": ("外觀", "標籤錯誤"),
    "FUN-01": ("功能", "輸出電壓異常"), "FUN-02": ("功能", "啟動失敗"), "FUN-03": ("功能", "亮度不足"),
    "FUN-04": ("功能", "色溫偏移"),
}

STATIONS = ("ICT", "FCT", "OQC")

CUSTOMERS = {
    "C01": ("Northgate Compute", "北美", "A"), "C02": ("Halvorsen Mobility", "歐洲", "A"),
    "C03": ("Brightwave Lighting", "歐洲", "B"), "C04": ("Teraview Systems", "北美", "A"),
    "C05": ("Orchard Devices", "亞洲", "B"), "C06": ("Vantor Automotive", "歐洲", "A"),
    "C07": ("Silvercrest Retail", "北美", "B"), "C08": ("Pinebrook Energy", "亞洲", "C"),
    "C09": ("Redfield Labs", "北美", "C"), "C10": ("Cobalt Bay Industries", "亞洲", "B"),
    "C11": ("Ashby Motors", "歐洲", "B"), "C12": ("Lumen Harbor", "亞洲", "C"),
}

# 供應商 → (名稱, 國家, 材料)
SUPPLIERS = {
    "S01": ("Hanwei Solder", "台灣", "錫膏"), "S02": ("Kinto Circuits", "台灣", "PCB"),
    "S03": ("Delmar Semicon", "美國", "IC"), "S04": ("Yongfa Passive", "中國", "被動元件"),
    "S05": ("Lumichip Optics", "台灣", "LED 晶粒"), "S06": ("Corwin Connect", "日本", "連接器"),
    "S07": ("Baoan PCB", "中國", "PCB"), "S08": ("Viet Metal Works", "越南", "外殼"),
    "S09": ("Rongda Solder", "中國", "錫膏"), "S10": ("Sakura Magnetics", "日本", "磁性元件"),
}
