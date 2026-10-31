# 设备发现：真实型号与能力矩阵

## 1. 确认设备真实身份

**永远不要**根据 Zabbix 里已挂的模板名判断型号。模板是人工选的，`sysObjectID` 是设备自己报的。

```bash
H=192.168.1.220; COMM=xike
Q="snmpget -v2c -c $COMM -t 3 -r 2 -On"

$Q $H 1.3.6.1.2.1.1.1.0                      # sysDescr，白牌常返回 "Switch"，无信息量
$Q $H 1.3.6.1.2.1.1.2.0                      # sysObjectID ← 企业号在这里
$Q $H 1.3.6.1.2.1.1.5.0                      # sysName
$Q $H 1.3.6.1.2.1.47.1.1.1.1.13.1            # entPhysicalModelName ← 真实型号
$Q $H 1.3.6.1.2.1.47.1.1.1.1.11.1            # entPhysicalSerialNum
$Q $H 1.3.6.1.2.1.47.1.1.1.1.2.1             # entPhysicalDescr
```

`sysObjectID` 形如 `1.3.6.1.4.1.<enterprise>.<...>`，`<enterprise>` 是 IANA 注册号：

| 企业号 | 厂商 |
| --- | --- |
| 9 | Cisco |
| 2011 | 华为 |
| 25506 | H3C / 新华三 |
| 3902 | 中兴 |
| 8909 | TP-Link |
| 其他 | 当未知厂商处理，走「私有树反推」 |

私有 MIB 树 = `1.3.6.1.4.1.<enterprise>`

## 2. 能力矩阵探测

一次性探完标准 MIB，决定模板能用哪些「免费」指标：

```bash
H=...; COMM=...; C="-v2c -c $COMM -t 2 -r 1 -On"

probe() {  # probe <标签> <OID>
  r=$(snmpget $C "$H" "$2" 2>&1)
  if echo "$r" | grep -qiE "No Such|Timeout|Error in packet"; then
    printf "  ✗ %-26s %s\n" "$1" "$(echo "$r" | sed 's/.*: //' | cut -c1-40)"
  else
    printf "  ✓ %-26s %s\n" "$1" "$(echo "$r" | sed 's/.*= //' | cut -c1-40)"
  fi
}

echo "[MIB-II 系统组]"
probe sysDescr     1.3.6.1.2.1.1.1.0
probe sysObjectID  1.3.6.1.2.1.1.2.0
probe sysUpTime    1.3.6.1.2.1.1.3.0

echo "[ifTable]"
probe ifDescr      1.3.6.1.2.1.2.2.1.2.1
probe ifSpeed      1.3.6.1.2.1.2.2.1.5.1
probe ifOperStatus 1.3.6.1.2.1.2.2.1.8.1

echo "[ifXTable 64位计数器]"
probe ifHCInOctets  1.3.6.1.2.1.31.1.1.1.6.1
probe ifHCOutOctets 1.3.6.1.2.1.31.1.1.1.10.1
probe ifHighSpeed   1.3.6.1.2.1.31.1.1.1.15.1
probe ifAlias       1.3.6.1.2.1.31.1.1.1.18.1

echo "[ENTITY]"
probe entModelName  1.3.6.1.2.1.47.1.1.1.1.13.1

echo "[BRIDGE / MAC 表]"
probe dot1dTpFdbPort 1.3.6.1.2.1.17.4.3.1.2

echo "[HOST-RESOURCES（多数交换机没有）]"
probe hrProcessorLoad 1.3.6.1.2.1.25.3.3.1.2
probe hrStorageDescr  1.3.6.1.2.1.25.2.3.1.3

echo "[LLDP（很多白牌没有）]"
probe lldpRemSysName  1.0.8802.1.1.2.1.4.1.1.9

echo "[光功率 DDM（多数没有）]"
probe entSensorValue  1.3.6.1.2.1.99.1.1.1.4

echo "[Cisco 私有（验证是否真 Cisco）]"
probe ciscoMIB        1.3.6.1.4.1.9.2.1.1.0
```

判断口径：

| 探测结果 | 模板里怎么办 |
| --- | --- |
| ifXTable 可用 | 用 `ifHCInOctets`/`ifHCOutOctets`（64 位），不用 32 位的 `ifInOctets` |
| ifHighSpeed 可用、ifSpeed 异常 | 带宽一律用 `ifHighSpeed` |
| HOST-RESOURCES 不可用 | CPU/内存必须去私有树找 |
| LLDP/DDM 不可用 | 模板里干脆不做，并在交付说明里写明「设备不支持」 |

## 3. 私有 OID 反推（无文档设备）

```bash
snmpwalk -v2c -c $COMM -On $H 1.3.6.1.4.1.<enterprise> > /tmp/priv.txt
wc -l /tmp/priv.txt
grep -E '\.0 = ' /tmp/priv.txt | head -60        # 所有标量 = 全局指标
```

2742 条 OID、316 个标量是常见规模。按可靠性从高到低判断含义：

### (a) 量级匹配 —— 最可靠
```
100.1.11.6.0 = 536870912     → 512 MiB，几乎肯定是内存总量
100.1.11.7.0 = 100712448     → 96 MiB，内存已用
```

### (b) 采样差分 —— 判断「活跃指标 vs 静态配置」
```bash
for o in 100.1.11.2 100.1.11.8; do
  echo "$o 首值=$(snmpget $C $H .1.3.6.1.4.1.60709.$o.0 | sed 's/.*= //')"
done
sleep 12
for o in 100.1.11.2 100.1.11.8; do
  echo "$o 12秒后=$(snmpget $C $H .1.3.6.1.4.1.60709.$o.0 | sed 's/.*= //')"
done
```
- 有小幅波动（±1~3）→ 传感器类（温度/电压）
- 单调递增 → 计数器（包数/字节数）
- 恒定不变 → 配置值或状态枚举，**不要**当 CPU 用

### (c) 与设备规格对照
web 界面 / 说明书上的内存容量、温度区间、光模块数量，反过来匹配 OID 值。

### 反推不出来的，不要硬写
`100.1.11.8.0` 恒定为 30，既像 CPU 利用率又像配置项——这种情况下：
- 要么不写进模板
- 要么写进去但 `status: DISABLED`，并在 `description` 里写明「推断值，需人工核对」

**宁可少一个指标，也不给一个会误导的数据源。**

## 4. 记住这些数字是「陷阱」

| OID | 陷阱 |
| --- | --- |
| `1.3.6.1.2.1.2.2.1.5` (ifSpeed) | 白牌设备常返回 `4294967295`（2^32-1，32 位溢出）。用 `ifHighSpeed` |
| `1.3.6.1.2.1.1.1.0` (sysDescr) | 可能只有 "Switch" 一个词 |
| `ifAlias` | 可能全空字符串，不代表不支持，只是没配描述 |
| 不带索引的 `snmpget` | 表型 OID 必须带索引后缀（`.1`），否则返回 `No Such Instance`，容易误判为「不支持」 |
