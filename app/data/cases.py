"""历史缺陷案例模拟数据。"""

CASES = [
    {"id": "BUG-BT-042", "module": "蓝牙", "symptom": "某机型与指定耳机偶现回连失败",
     "root_cause": "固件版本与蓝牙协议栈兼容问题",
     "resolution": "升级协议栈补丁后回归通过,同步增加指定耳机组合回归用例",
     "keywords": ["蓝牙", "耳机", "回连", "协议栈", "固件", "兼容"]},
    {"id": "BUG-OTA-017", "module": "OTA", "symptom": "OTA 升级包校验失败并回滚",
     "root_cause": "发布包签名链路配置不一致",
     "resolution": "统一签名配置,补充发布前包校验脚本",
     "keywords": ["ota", "升级", "签名", "校验", "回滚", "刷机"]},
    {"id": "BUG-ANR-063", "module": "App 稳定性", "symptom": "压测 2 小时后 App 卡死并出现 ANR",
     "root_cause": "主线程存在同步 IO,高频场景下阻塞消息队列",
     "resolution": "将 IO 移到后台线程,补充 traces 与 CPU 曲线采集规范",
     "keywords": ["anr", "卡死", "压测", "主线程", "io", "traces"]},
    {"id": "BUG-LOG-009", "module": "日志规范", "symptom": "问题可复现但日志缺少关键 tag",
     "root_cause": "测试包未打开调试日志开关",
     "resolution": "更新日志采集清单,要求复现前确认 debug flag 与 log level",
     "keywords": ["日志", "logcat", "tag", "debug", "复现", "采集"]},
    {"id": "BUG-STAB-028", "module": "稳定性", "symptom": "高温压测后设备重启",
     "root_cause": "温控阈值配置与功耗场景不匹配",
     "resolution": "调整 thermal 配置并补充高温长稳回归",
     "keywords": ["稳定性", "高温", "压测", "重启", "thermal", "功耗"]},
]


def all_cases() -> list[dict]:
    return CASES
