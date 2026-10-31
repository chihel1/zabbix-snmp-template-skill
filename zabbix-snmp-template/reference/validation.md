# 校验与交付

## 三重校验，缺一不可

### 第 1 重：结构校验（不需要设备）

```bash
python3 scripts/validate-template.py 模板.yaml
```

检查项：
- YAML 能被解析
- `zabbix_export.version` 存在且是字符串
- 模板**没有**顶层 `triggers`
- item / item_prototype / discovery_rule / trigger_prototype 都有 `uuid`
- trigger **没有** `uuid`
- uuid 全局唯一
- 表达式里引用的模板名 == `template:` 字段
- 每个 item 有 `name` / `key` / `type` / `value_type`

结构错了就在这一步拦住，别浪费一次导入尝试。

### 第 2 重：OID 真机校验（必须有设备）

```bash
bash scripts/verify-template-oids.sh 模板.yaml <设备IP> <community>
```

脚本会把模板里所有 `snmp_oid` 抽出来（`{#SNMPINDEX}` 用 1 代替），逐个 `snmpget`。

**验收标准：失败数必须为 0。** 有一个失败就说明模板里会存在死键，回到 discovery 重查。

输出示例：
```
  ✓ xike.hw.model          STRING: "SKS8300-8X"
  ✓ net.if.in[{#IFNAME}]   Counter64: 110449599204
=== 可用 27 / 失败 0 ===
```

### 第 3 重：导入实测（需要 Zabbix 前端权限）

前两重只能保证「结构对、OID 通」。真正的导入验证必须有人在前端操作。

```bash
# 如果没有前端账号，明确告知用户「结构已按 schema 校验，但导入动作需你执行」
```

导入后检查：
1. 模板出现在 Data collection → Templates
2. 主机上链接新模板
3. 发现规则点一次 **Execute now**（不用等 1 小时周期）
4. 进 Latest data 确认每个 item 都有值、没有 "Not supported"

## 导入步骤（写给用户）

```text
1. Data collection → Templates → Import
2. 选择 <模板>.yaml → Import
3. 打开主机 <host>
4. Templates 里解绑旧模板（会连带删除其所有监控项）
5. 链接新模板
6. 发现规则 → Execute now
7. Latest data 抽查 3~5 个 item 是否 normal
```

## 交付时必须说明的

| 项 | 为什么必须说 |
| --- | --- |
| 哪些 item 是**实测**的、哪些是**推断**的 | 推断值会被写进告警阈值，用户有权知道 |
| 哪些触发器**默认 DISABLED**、原因 | 避免用户以为「导入就全开了」 |
| 设备**本身不支持**哪些能力 | 否则用户会反复问「为什么没有光功率/LLDP」 |
| 旧模板解绑的**副作用** | 解绑 = 删除该模板产生的所有 item，可能上千个 |
| 你可能**没能验证**的部分 | 诚实标注「导入动作未实测」比事后甩锅好 |

## 常见导入报错对照

| 报错 | 含义 | 修法 |
| --- | --- | --- |
| `标签 "triggers" 异常` | 模板对象不允许顶层 triggers | 把触发器挪进 item 下 |
| `标签 "uuid" 丢失` | template 里的 trigger 少了 uuid | 给 trigger 加 uuid |
| `无效的参数 "/1/uuid": 需用 UUIDv4` | uuid 版本位不是 4，或写成了数字 | 用 `uuid.uuid4().hex` 重新生成 |
| `Duplicate entry 'NNNN' for key 'PRIMARY'` | Zabbix 的 `ids` 分配器漂移（通常是手工改过库） | `scripts/repair-zabbix-ids.sh` 诊断并修复 |
| `无效的值 "2"`（priority/status） | 用了数字枚举 | 改大写字符串 `WARNING`/`ENABLED` |
| `uuid 重复` | 复制粘贴时没换 uuid | 重新生成，见下 |
| `模板未找到`（表达式里） | 表达式中的模板名与 `template:` 不一致 | 全局替换 |
| `值 "7.4" 不匹配` | version 被当成 float | 加引号 `'7.4'` |

## 生成 UUID：必须是合法 UUIDv4

Zabbix 会校验版本位，**不是随便 32 位十六进制都行**：

```
无效的参数 "/1/uuid": 需用 UUIDv4
```

UUIDv4 约束（32 位小写十六进制、不带连字符）：

| 位置 | 要求 | 示例 `7f7eae1f58e14912a7ac9f0f5c9c7892` |
| --- | --- | --- |
| 全部 32 位 | `[0-9a-f]` | ✓ |
| 第 13 位（index 12） | 必须是 `4` | `...9149` **`4`** `12a...` |
| 第 17 位（index 16） | `8`/`9`/`a`/`b` | `...5c9c7` **`8`** `92` |

```bash
python3 -c "import uuid;print(uuid.uuid4().hex)"                       # 单个
python3 -c "import uuid;[print(uuid.uuid4().hex) for _ in range(50)]"  # 批量
```

### 两个坑

**1. 不要用手编的规律 ID**（如 `1000...0001`）。
第 13 位是 `0` 而不是 `4`，直接被判不是 v4。

**2. 纯数字 uuid 会被 YAML 当成整数。**
`uuid: 10000000000000000000000000000001` 在 YAML 里是 int，
Zabbix 收到的是数字，同样报「需用 UUIDv4」。
`uuid.uuid4().hex` 一定含 `a-f` 字母，天然不会被误判；
处理已有模板时也不要判断类型，直接无条件覆盖。

`validate-template.py` 会同时校验「是 v4」和「是字符串」，跑一遍就能挡住。