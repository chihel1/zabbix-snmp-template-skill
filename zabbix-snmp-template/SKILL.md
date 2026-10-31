---
name: zabbix-snmp-template
description: 当需要为交换机/路由器/防火墙/带外管理卡等 SNMP 设备编写或修正 Zabbix 模板、排查 Zabbix 模板导入报错、或探测设备真实型号与可用 OID 时使用；包含从零发现设备能力、生成可导入 YAML、以及真机逐 OID 验证的完整流程
---

# zabbix-snmp-template

为网络设备（尤其是无文档的国产/白牌交换机）生成**能真正导入、且每一项都有数据**的 Zabbix 模板。

核心立场：**宁可少一个指标，也不给一个错的数据源。** 猜出来的 OID 会在半年后变成一条没人看得懂的假告警。

## 任务路由

| 任务 | 先读 | 再读 |
| --- | --- | --- |
| 给一台新设备建模板 | `reference/discovery.md` | `reference/import-schema.md` |
| Zabbix 导入报错（标签无效/取值非法） | `reference/import-schema.md` | `reference/validation.md` |
| 已有模板要修/加指标 | `reference/discovery.md` | `reference/pitfalls.md` |
| 不确定某个 OID 能不能用 | `reference/discovery.md` 的「私有 OID 反推」 | `reference/pitfalls.md` |
| 导入前的最后检查 | `reference/validation.md` | — |

## 硬规则

1. **绝不根据 Zabbix 里已挂的模板名判断设备型号。** 必须先取 `sysObjectID` 查企业号。白牌设备常被手工挂上 Cisco/H3C 模板，导致上千个死键。
2. **绝不写没验证过的 OID。** 每个 `snmp_oid` 都必须先在真机上 `snmpget` 通。无法确认含义的标量，要么不写，要么写但把触发器设成 `DISABLED` 并在 description 里说明。
3. **绝不用 `ifSpeed` 算带宽。** 大量廉价设备 `1.3.6.1.2.1.2.2.1.5` 返回 `4294967295`（32 位溢出），必须用 `ifHighSpeed`。
4. **改任何 Zabbix 配置前先征得同意。** 生成文件不算改动；导入、解绑模板、改主机配置都必须先问。
5. **不猜密码、不爆破。** 没有 SNMP community 或前端账号时直接向用户要。
6. **不要直接写 Zabbix 数据库。** 若不得已写了（比如临时调阈值），必须同步修 `ids` 表，
   否则之后每一次导入/新增都会撞主键。诊断与修复见 `scripts/repair-zabbix-ids.sh`。

## 工作流

### 第 0 步：拿到访问方式
需要两样东西，缺哪个问用户要哪个：
- 设备的 **SNMP version + community**（v2c 即可）
- 一台能**从网络访问该设备 161/udp** 的跳板机（有 `snmpwalk`/`snmpget`）

在跳板机上装工具（Debian/Ubuntu）：
```bash
DEBIAN_FRONTEND=noninteractive apt-get install -y snmp
```

### 第 1 步：确认设备真实身份
```bash
snmpget -v2c -c "$COMM" -t 3 -r 2 -On "$HOST" 1.3.6.1.2.1.1.1.0   # sysDescr（常无意义，如 "Switch"）
snmpget -v2c -c "$COMM" -t 3 -r 2 -On "$HOST" 1.3.6.1.2.1.1.2.0   # sysObjectID ← 看企业号
snmpget -v2c -c "$COMM" -t 3 -r 2 -On "$HOST" 1.3.6.1.2.1.47.1.1.1.1.13.1  # entPhysicalModelName ← 真实型号
snmpget -v2c -c "$COMM" -t 3 -r 2 -On "$HOST" 1.3.6.1.2.1.47.1.1.1.1.11.1  # entPhysicalSerialNum
```
`sysObjectID` 的末段是企业号：`1.3.6.1.4.1.<enterprise>...`。常见：Cisco=9、H3C=25506、华为=2011、**其他一律当未知厂商处理**。

### 第 2 步：跑能力矩阵
用 `scripts/probe-device.sh` 一次性探完标准 MIB 支持情况，输出一张表。重点看：
- `1.3.6.1.2.1.2.2.1.*`（ifTable）
- `1.3.6.1.2.1.31.1.1.1.*`（ifXTable，64 位计数器）
- `1.3.6.1.2.1.47.*`（ENTITY）
- `1.3.6.1.2.1.17.4.3.*`（BRIDGE，MAC 表）
- `1.3.6.1.2.1.25.*`（HOST-RESOURCES，多数交换机**没有**）
- `1.3.6.1.4.1.8802.*`（LLDP，很多白牌**没有**）
- `1.3.6.1.2.1.99.*`（ENTITY-SENSOR / 光功率 DDM，多数**没有**）

### 第 3 步：反推私有 OID（无文档设备）
```bash
snmpwalk -v2c -c "$COMM" -On "$HOST" 1.3.6.1.4.1.<enterprise> > /tmp/priv.txt
# 所有标量（全局指标）
grep -E '\.0 = ' /tmp/priv.txt
```
判断含义的方法，**按可靠性排序**：
1. **量级匹配**：`536870912` = 512MB（内存总量）、`100712448` ≈ 96MB（已用量）
2. **采样差分**：间隔 10~15 秒采两次，**有波动**的才是 CPU/温度这类活跃指标；恒定值可能是配置或计数器
3. **与设备规格对照**：web 界面/说明书上的内存、温度区间
4. **不要**靠 OID 顺序猜（`.11.2/.11.3/.11.4/.11.5` 连续四个 85~92 的值，只能推断是 4 路温度传感器，不能确定哪路对应哪个芯片）

反推不出来的，**写进模板但标注"推断值"并把触发器默认禁用**。

### 第 4 步：生成模板 YAML
复制 `assets/skeleton.yaml` 改。严格遵守 `reference/import-schema.md` 的硬规则——
最容易踩的是**触发器必须嵌在 item 里面**，模板不能有顶层 `triggers`。

### 第 5 步：三重校验
```bash
python3 scripts/validate-template.py <模板.yaml>          # 结构 + UUID + 表达式模板名一致性
bash    scripts/verify-template-oids.sh <模板.yaml> "$HOST" "$COMM"   # 逐 OID 真机验证
```
第二项必须做到 **0 失败** 才能交付。

### 第 6 步：交付
给出下载方式 + 导入步骤 + 需要解绑的旧模板，并明确告知：
- 哪些项是实测的、哪些是推断的
- 哪些能力设备本身不支持（所以模板里没有）
- 触发器里哪些默认是 `DISABLED`、为什么

## 交付物清单

- `模板.yaml`（Zabbix 7.x 可直接导入）
- 设备能力矩阵（支持/不支持哪些 MIB）
- OID 验证记录（每个 OID 的实际返回值）
- 导入步骤与解绑说明

## 反面案例（真实踩过的坑）

一台交换机在 Zabbix 里挂着 "Cisco Catalyst 3750V2-24FS by SNMP"，128 个监控项里有 127 个静默失效。直到查 `sysObjectID` 才发现是企业号 `60709`（希科 SKS8300-8X），Cisco 私有 MIB `1.3.6.1.4.1.9` 直接返回 `No Such Object`。

**结论：模板名是人工选的，不可信；`sysObjectID` 是设备自己报的，可信。**
