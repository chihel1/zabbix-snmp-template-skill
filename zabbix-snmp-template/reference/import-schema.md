# Zabbix 导入 schema 硬规则

Zabbix 的模板导入是**严格 schema 校验**，不是"差不多就行"。YAML 里多一个标签、少一个 UUID、枚举值大小写不对，都会整包被拒。

## 通用做法：别猜，直接读它的校验器

Zabbix 7.x 的导入规则不在文档里，在**前端源码**里。任何一台装了 frontend 的机器上都有：

```bash
ls /usr/share/zabbix/ui/include/classes/import/validators/
# C54XmlValidator.php C60XmlValidator.php C62XmlValidator.php C64XmlValidator.php
# C70XmlValidator.php C72XmlValidator.php C74XmlValidator.php CXmlValidatorGeneral.php
```

取版本号最高那个（与 `zabbix_server -V` 的主次版本对应）。文件结构是 PHP 嵌套数组，`'xxx' => ['type' => ..., 'rules' => [...]]` 就是一层标签。

查某个对象的允许标签，用缩进对齐法：

```bash
F=/usr/share/zabbix/ui/include/classes/import/validators/C74XmlValidator.php
# 1) 找到根级 templates 定义
grep -n "prefix' => 'template'" $F
# 2) 从该行往下，打印与 items 同级缩进的标签（就是 template 的直接子标签）
awk 'NR>=1863 && NR<=2700 && /^[ \t]*'"'"'[a-z_]+'"'"' =>/ {
       match($0,/^[ \t]*/); if (RLENGTH==5) { match($0,/'"'"'[a-z_]+'"'"'/); print substr($0,RSTART,RLENGTH) }
     }' $F
```

导出报错信息里的路径 `/zabbix_export/templates/template(1)` 就是告诉你**哪个对象**出问题，
报错里的标签名就是**哪一层不认这个标签**。

## 三条最容易踩的规则（Zabbix 7.4 实测）

### 1. 模板对象**没有**顶层 `triggers`

```
/zabbix_export/templates/template(1)
  uuid, template, name, description, wizard_ready, readme,
  vendor{name,version}, templates, groups, items, discovery_rules
  ← 到此为止，没有 triggers
```

触发器不是模板的子元素，而是**挂在它所属的监控项下面**：

```yaml
items:
  - uuid: ...
    name: ICMP ping
    key: icmpping
    ...
    triggers:            # ← 在这里
      - expression: 'max(/模板名/icmppingloss,3m)>80'
        name: '设备不可达'
        priority: HIGH
```

一个触发器引用多个 item 时，挂在其中任意一个下面即可（通常选表达式里第一个出现的）。

### 2. 模板里的 `trigger` **必须有** `uuid`（但 host 里的不行）

**这是最容易读错的一条**：校验器里有两段结构几乎一样的 `triggers` 规则，
一段属于 `host`，一段属于 `template`，uuid 要求正好相反。

```php
// C74XmlValidator.php 第 880 行附近 —— host 对象的 item 下
'trigger' => ['rules' => ['expression' => ..., 'name' => ...]]        // 没有 uuid

// C74XmlValidator.php 第 2112 行附近 —— template 对象的 item 下
'trigger' => ['rules' => ['uuid' => [...XML_REQUIRED], 'expression' => ...]]  // 必须有 uuid
```

| 对象 | 位置 | uuid |
| --- | --- | --- |
| item | template 或 host 的 items | 必须 |
| item_prototype | discovery_rule.item_prototypes | 必须 |
| **trigger** | **template.items[].triggers[]** | **必须** |
| trigger | host.items[].triggers[] | 禁止（跟随模板，没有独立 uuid） |
| trigger_prototype | discovery_rule.trigger_prototypes | 必须 |

写错的表现：
- 少了 → `标签无效 ".../triggers/trigger(1)": 标签 "uuid" 丢失。`
- 多了（在 host 导出里）→ `标签 "uuid" 异常`

**以后每次动 uuid 规则，先确认这个对象是在 host 里还是在 template 里。**

### 3. 枚举值用**大写字符串**，不是数字（7.0 起）

```yaml
type: SNMP_AGENT          # 不是 20
value_type: UNSIGNED      # 不是 3
priority: WARNING         # 不是 2
status: DISABLED          # 不是 1
allow_traps: NO
```

**合法取值必须从校验器里抄，不要凭记忆写。** 从 `C74XmlValidator.php` 的私有枚举数组导出：

```bash
F=/usr/share/zabbix/ui/include/classes/import/validators/C74XmlValidator.php
dump() { L=$(grep -n "private \$$1 = \[" $F | cut -d: -f1);
         sed -n "${L},$((L+40))p" $F | grep -oE "CXmlConstantName::[A-Z_0-9]+" \
           | sed 's/CXmlConstantName:://' | tr '\n' ' '; echo; }
dump ITEM_TYPE; dump ITEM_VALUE_TYPE; dump EVALTYPE; dump TRIGGER_PRIORITY
```

Zabbix 7.4 实测结果：

| 枚举 | 合法值 |
| --- | --- |
| `item.type` | `ZABBIX_PASSIVE` `TRAP` `SIMPLE` `INTERNAL` `ZABBIX_ACTIVE` `EXTERNAL` `ODBC` `IPMI` `SSH` `TELNET` `CALCULATED` `JMX` `SNMP_TRAP` `DEPENDENT` `HTTP_AGENT` `SNMP_AGENT` `SCRIPT` `BROWSER` |
| `item.value_type` | `FLOAT` `CHAR` `LOG` `UNSIGNED` `TEXT` `BINARY` ← **没有 UPTIME** |
| `trigger.priority` | `NOT_CLASSIFIED` `INFO` `WARNING` `AVERAGE` `HIGH` `DISASTER` |
| `discovery_rule.filter.evaltype` | `AND_OR` `XML_AND` `XML_OR` `FORMULA` ← **没有 AND / OR** |
| `filter.conditions[].operator` | `MATCHES_REGEX` `NOT_MATCHES_REGEX` `EXISTS` `NOT_EXISTS` |
| `status` / `allow_traps` / `manual_close` | `ENABLED`/`DISABLED`、`YES`/`NO` |
| `preprocessing[].type` | `MULTIPLIER` `CHANGE_PER_SECOND` `JAVASCRIPT` `REGEX` `TRIM` `JSONPATH` `SNMP_WALK_VALUE` … |

### `UPTIME` 是头号陷阱

`value_type` 里**没有** `UPTIME`，写成 `UPTIME` 会报：

```
标签无效 ".../items/item(N)/value_type": 意外常量"UPTIME"。
```

sysUpTime 这类正确的写法是：

```yaml
value_type: UNSIGNED
units: uptime                                  # 显示成 "25 days, 9:28" 而不是秒数
preprocessing:                                 # Timeticks 是 1/100 秒，必须换算
  - type: MULTIPLIER
    parameters:
      - '0.01'
```

如果是普通秒数（不是 Timeticks）就不需要 `preprocessing`。

### `evaltype: AND` 也是错的

LLD 过滤器的 `evaltype` 只有 `AND_OR` / `XML_AND` / `XML_OR` / `FORMULA`。
单条件时用 `AND_OR` 等价（Zabbix 自己导出也是这么写的）。

## 对象字段顺序（供参考）

YAML 映射的键顺序**不影响校验**（校验器按 key 查白名单，不按位置匹配），
但按官方顺序写便于与 Zabbix 自己导出的文件做 diff。

```
template : uuid, template, name, description, wizard_ready, readme,
           vendor{name,version}, templates, groups, items, discovery_rules
item     : uuid, name, type, snmp_oid, key, delay, history, value_type, trends,
           status, allow_traps, allowed_hosts, units, params, authtype, username,
           password, description, inventory_link, valuemap{name}, logtimefmt,
           preprocessing{step{type,parameters{parameter}}}, interface_ref,
           master_item{key}, timeout, url, query_fields, parameters,
           headers, retrieve_mode, request_method, output_format,
           ssl_cert_file, ssl_key_file, ssl_key_password, verify_peer, verify_host,
           tags{tag{tag,value}}, triggers{trigger{...}}
trigger  : expression, recovery_mode, recovery_expression, correlation_mode,
           correlation_tag, name, event_name, opdata, url_name, url,
           status, priority, description, type, manual_close,
           dependencies{dependency{name,expression,recovery_expression}},
           tags{tag{tag,value}}
```

## 两个必需的字符串字段

- 模板级 `version` 必须是 `'7.4'` 这种带引号的字符串（写成 `7.4` 会被当成 float）
- `trends`/`history` 写成 `'0'`/`7d`/`365d`；字符型 item 的 `trends` 必须是 `'0'`

## 检查清单

导入前确认：

- [ ] `zabbix_export.version` 与目标 Zabbix 版本一致
- [ ] 模板没有顶层 `triggers`
- [ ] 所有 `item` / `item_prototype` / `discovery_rule` / `trigger_prototype` 都有 uuid
- [ ] 所有 `trigger`（在 template 的 items 下）**都有** uuid
- [ ] uuid 全局唯一
- [ ] 所有 uuid 都是**合法 UUIDv4**（第 13 位=`4`，第 17 位∈`89ab`），且是字符串不是数字
- [ ] 表达式里的模板名与 `template:` 字段完全一致（含空格和大小写）
- [ ] 枚举值全是大写字符串
- [ ] 用 `scripts/validate-template.py` 跑一遍
