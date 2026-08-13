"""测试 SOP 与故障知识库模拟数据。"""

SOPS = [
    {
        "id": "SOP-BT-001",
        "name": "蓝牙连接异常排查 SOP",
        "category": "蓝牙",
        "scope": "耳机、车机、手环等外设连接失败、回连失败、配对失败",
        "duration": "首轮定位 30-60 分钟",
        "audience": "测试、研发、FAE",
        "outline": "确认复现稳定性、设备/固件/外设型号、协议组合、bt_stack 日志、历史缺陷比对",
        "highlight": "先补齐复现环境和日志证据,再判断协议栈、兼容性或硬件风险",
        "keywords": ["蓝牙", "连接", "耳机", "回连", "配对", "bt_stack", "协议栈"],
    },
    {
        "id": "SOP-FW-002",
        "name": "刷机失败与 OTA 升级异常排查 SOP",
        "category": "固件升级",
        "scope": "刷机卡进度、OTA 包校验失败、升级后无法启动",
        "duration": "首轮定位 20-40 分钟",
        "audience": "测试、固件研发、版本发布负责人",
        "outline": "核对包版本、签名、分区空间、线材/端口、升级日志、失败码和复现批次",
        "highlight": "先排除包和环境问题,再进入 bootloader、分区或签名链路定位",
        "keywords": ["刷机", "ota", "升级", "20%", "卡住", "签名", "分区", "固件"],
    },
    {
        "id": "SOP-APP-003",
        "name": "ANR 与 App 卡死日志采集规范",
        "category": "稳定性",
        "scope": "ANR、压测后卡死、主线程阻塞、内存异常",
        "duration": "日志采集 10-20 分钟,定位视堆栈复杂度而定",
        "audience": "App 测试、App 研发、稳定性测试",
        "outline": "采集 bugreport、logcat、traces、主线程堆栈、内存和 CPU 曲线、复现脚本",
        "highlight": "没有 traces 和复现条件时不直接定责,先补齐关键证据",
        "keywords": ["anr", "卡死", "稳定性", "压测", "logcat", "traces", "主线程", "io"],
    },
]


def all_sops() -> list[dict]:
    return SOPS
