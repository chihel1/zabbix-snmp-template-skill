# 踩坑记录

每条都是真实发生过的，不是理论风险。

## 1. 模板名会骗人

Zabbix 里主机挂着 "Cisco Catalyst 3750V2-24FS by SNMP"，128 个监控项。
实际 `sysObjectID = 1.3.6.1.4.1.60709.1.1.1.1024`（企业号 60709，希科 SKS8300-8X）。
Cisco 私有 MIB `1.3.6.1.4.1.9` 返回 `No Such Object`，`1.3.6.1.2.1.25.*` 也不存在。

**后果**：127 个 item 静默失效，只剩 3 个端口 down 告警还在工作，而且没人发现。

**对策**：先取 `sysObjectID` + `entPhysicalModelName`，再决定用哪个模板。

## 2. `ifSpeed` 32 位溢出

```
snmpget 1.3.6.1.2.1.2.2.1.5.1   →  Gauge32: 4294967295    ← 2^32-1，垃圾值
snmpget 1.3.6.1.2.1.31.1.1.1.15.1 →  Gauge32: 10000       ← 正确
```

廉价交换机固件在 10G 端口上把 `ifSpeed`（32 位）算溢出了。
如果拿它算利用率，带宽图会变成天文数字或者负数。

**对策**：所有带宽/利用率一律用 `ifHighSpeed`（单位 Mb/s），触发器里再加一个
`last(speed)>0` 的守卫，避免未连接端口（speed=0）除零。

## 3. 触发器放错层

写了模板级 `triggers:`，导入报：

```
标签无效 "/zabbix_export/templates/template(1)": 标签 "triggers" 异常。
```

Zabbix 的模板对象**没有** triggers 子元素。触发器属于 item。

## 4. 模板里的 trigger 丢了 uuid

把触发器从模板层挪进 item 下之后，去掉 uuid 会报：

```
标签无效 "/zabbix_export/templates/template(1)/items/item(5)/triggers/trigger(1)": 标签 "uuid" 丢失。
```

**它要 uuid。** 我当时去校验器里确认，读了第 880 行那段（host 对象的规则，确实没有 uuid），
就断定 trigger 不要 uuid——但模板对象在第 2112 行还有一段几乎一样的规则，那里 uuid 是 `XML_REQUIRED`。

**同一个标签名，在 host 和 template 两个对象里规则相反**，这是本次最坑的一条。
教训：grep 到结果之后要确认**这段代码属于哪个父对象**，不能看到 `'trigger' =>` 就下结论。

## 5. 枚举数字 vs 字符串

Zabbix 6.x 导出用数字（`type: 20`、`priority: 2`），7.0 起改成大写字符串
（`type: SNMP_AGENT`、`priority: WARNING`）。
拿 6.x 的模板往 7.x 导，或者反过来，都会报「无效的值」。

## 6. 不带索引查表型 OID 会误判「不支持」

```bash
snmpget ... 1.3.6.1.2.1.31.1.1.1.18    → No Such Instance currently exists at this OID
snmpwalk ... 1.3.6.1.2.1.31.1.1.1.18   → .18.1 = ""  .18.2 = ""  ...
```

表型 OID 必须带索引（`.1`）或直接用 `snmpwalk`。
否则会把「支持但值为空」误判成「不支持」，白白砍掉一个指标。

## 7. 恒定的标量不是 CPU

私有树里 `.11.8.0` 连续采样一直是 `30`。可能是 CPU 利用率，也可能是某个配置值。
把它当 CPU 写进模板，用户会得到一个永远显示 30% 的假图表。

**对策**：采样两次看是否变化；不变的一律不当活跃指标用。

## 8. 阈值不该照抄官方模板

Zabbix 官方 Linux 模板的磁盘 await 阈值是 **20ms**，那是给企业级 SSD 定的。
一块 5400 转监控盘（Seagate SkyHawk）常态就在 20~30ms，配 20ms 阈值的结果是
**30 天响 535 次、占全部告警的 75%**，最终导致整个告警通道被静音。

**对策**：阈值必须根据设备实测分布来定。先取 30 天历史数据的 P95/峰值，
再留 2~3 倍余量。模板里的阈值一律走宏（`{$IF.UTIL.MAX}`），方便现场调。

## 9. 给「不支持的指标」留位置

不要为了模板"看起来完整"而塞一堆官方模板里的通用 item。
一个永远 unsupported 的 item 比没有更糟：它会让人误以为在监控。

**对策**：模板里只放验证过的 item；不支持的能力写进交付说明里，明确告诉用户。

## 10. 别在没确认的情况下导入

生成模板文件 ≠ 允许导入。导入会改主机配置（绑模板、解绑模板、删 item）。
**先给文件、说明影响、等用户点头。**

## 11. 解绑旧模板的连带删除

主机上解绑一个模板，Zabbix 会**删除该模板产生的所有 item、触发器、图形**，
以及它们的历史数据。128 个死键删掉是好事，但如果旧模板里有还在用的指标，
解绑前必须先确认。

**对策**：解绑前导出一次主机配置做备份；或在 UI 里先看 `` 有哪些 item 还 normal。

## 12. `value_type: UPTIME` 不存在

```
标签无效 "/zabbix_export/templates/template(1)/items/item(5)/value_type": 意外常量"UPTIME"。
```

Zabbix 的 item 值类型只有 `FLOAT` `CHAR` `LOG` `UNSIGNED` `TEXT` `BINARY`。
"Uptime" 不是值类型，而是 **单位**（`units: uptime`）。

正确写法：
```yaml
value_type: UNSIGNED
units: uptime
preprocessing:
  - type: MULTIPLIER
    parameters: ['0.01']      # sysUpTime 是 Timeticks(1/100 秒)
```

## 13. LLD 过滤器 `evaltype: AND` 不合法

合法值是 `AND_OR` / `XML_AND` / `XML_OR` / `FORMULA`。
写 `AND` 或 `OR` 会被 schema 拒，单条件用 `AND_OR` 即可。

## 14. 教训：枚举值必须从校验器里抄

上面 12、13 两条，都是"凭直觉写枚举"造成的。Zabbix 的枚举名和 UI 上的叫法、
和旧版本导出文件里的写法，三者都不完全一致。

**规矩**：写任何 `type` / `value_type` / `priority` / `status` / `evaltype` / `operator` / `preprocessing.type`
之前，先去 `C74XmlValidator.php` 对应的私有数组里确认。查法见 `import-schema.md`。

## 15. uuid 必须是 UUIDv4，而且不能是数字

```
导入失败
无效的参数 "/1/uuid": 需用 UUIDv4
```

为了做 diff 方便，我用了自编的规律 ID：`10000000000000000000000000000001`。
它第 13 位是 `0`，不是 v4 要求的 `4`，直接被拒。

还有更隐蔽的第二层：这些 ID 全是数字，PyYAML 重新序列化时**不加引号**，
于是 `uuid: 1000...0001` 在 YAML 里变成了 **int**。
我的批量替换脚本里有 `isinstance(v, str)` 判断，结果 47 个 uuid 只换了 8 个。

**对策**：
- 一律 `uuid.uuid4().hex`，天然是 v4、天然含字母
- 处理存量模板时对 `uuid` 字段**不要做类型判断**，无条件覆盖
- `validate-template.py` 现在同时校验「是 v4」和「是字符串」

## 16. 手工写 Zabbix 数据库会破坏 ID 分配器（导入报 Duplicate entry）

```
导入失败
Error in query [INSERT INTO hostmacro ...] [Duplicate entry '8326' for key 'PRIMARY']
```

看起来像导入 bug，其实是**之前有人手工往表里插了一行**。

Zabbix 绝大多数表的主键**没有 AUTO_INCREMENT**：

```sql
CREATE TABLE `hostmacro` (
  `hostmacroid` bigint(20) unsigned NOT NULL,   -- 注意：没有 AUTO_INCREMENT
  PRIMARY KEY (`hostmacroid`)
)
```

ID 由 `ids` 表统一分配，语义是（见 `DB.php::reserveIds()`）：

```php
$max_nextid = bcadd($res['nextid'], $count, 0);   // UPDATE ids SET nextid = nextid + count
return bcadd($res['nextid'], 1, 0);               // 返回 nextid + 1
```

- `ids.nextid` = **最后已分配出去的 ID**
- 下次分配 = `nextid + 1`
- 所以正常状态是 **`nextid == MAX(主键)`**

本案例：有人用 `INSERT INTO hostmacro (hostmacroid, ...) VALUES (8326, ...)` 手工插了一行，
业务表最大 ID 变成 8326，但 `ids.nextid` 还停在 8325 → 下次分配拿 8326 → 撞主键。

### 修复

```sql
UPDATE ids SET nextid = (SELECT MAX(hostmacroid) FROM hostmacro)
WHERE table_name='hostmacro' AND field_name='hostmacroid';
```

**不是** `max + 1`——那会白跳一个 ID。
现成脚本：`scripts/repair-zabbix-ids.sh`（默认只诊断，`--fix` 才改）。

### 判断口径（别搞反）

| 状态 | 含义 |
| --- | --- |
| `nextid == MAX(pk)` | ✅ 正常 |
| `nextid < MAX(pk)` | ❌ 漂移，下次必撞 |
| `nextid > MAX(pk)` | ⚠️ 有空洞，无害 |

我第一次体检时按 `nextid < MAX+1` 判断，结果**每一张表**都被标成异常——
这种"全部命中"的结果一定要先怀疑判断标准，而不是真去批量改库。

### 顺带一条更普遍的教训

**能不动数据库就不动。** 那次是为了临时调一个告警阈值才直接写 `hostmacro`，
当时看起来只是加一行，几天后却变成了"模板永远导入失败"。
改 Zabbix 配置尽量走 API/前端；真要走 SQL，记得同步 `ids`。
