#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zabbix 7.x 模板 YAML 结构校验器

只做「不需要 Zabbix、也不需要设备」就能发现的那类错误——也就是导入时
会被 schema 直接拒绝的那些。跑完这一关再去连真机验证 OID。

用法:
    python3 validate-template.py <template.yaml>
退出码: 0 = 通过, 1 = 有错误
"""
import re
import sys

try:
    import yaml
except ImportError:
    sys.exit("需要 PyYAML: apt-get install -y python3-yaml")

RED = "\033[31m"
YEL = "\033[33m"
GRN = "\033[32m"
RST = "\033[0m"

# 全部取自 Zabbix 7.4 的
# /usr/share/zabbix/ui/include/classes/import/validators/C74XmlValidator.php
# 换 Zabbix 版本时请重新核对（方法见 reference/import-schema.md）
ENUMS = {
    "type": {"ZABBIX_PASSIVE", "TRAP", "SIMPLE", "INTERNAL", "ZABBIX_ACTIVE",
             "EXTERNAL", "ODBC", "IPMI", "SSH", "TELNET", "CALCULATED", "JMX",
             "SNMP_TRAP", "DEPENDENT", "HTTP_AGENT", "SNMP_AGENT", "SCRIPT", "BROWSER"},
    # 注意：没有 UPTIME。sysUpTime 这类要用 UNSIGNED + units: uptime
    "value_type": {"FLOAT", "CHAR", "LOG", "UNSIGNED", "TEXT", "BINARY"},
    "priority": {"NOT_CLASSIFIED", "INFO", "WARNING", "AVERAGE", "HIGH", "DISASTER"},
    "status": {"ENABLED", "DISABLED"},
    "allow_traps": {"YES", "NO"},
    "manual_close": {"YES", "NO"},
    # LLD 过滤表达式：AND / OR 是错的，正确是 AND_OR / XML_AND / XML_OR
    "evaltype": {"AND_OR", "XML_AND", "XML_OR", "FORMULA"},
    "operator": {"MATCHES_REGEX", "NOT_MATCHES_REGEX", "EXISTS", "NOT_EXISTS"},
}

errors = []
warnings = []


def uuid_problems(u):
    """Zabbix 要求合法的 UUIDv4（32 位小写十六进制，版本位=4，变体位=8/9/a/b）"""
    if not isinstance(u, str):
        return ["必须是字符串。注意：纯数字的 uuid 经过 YAML 序列化会被当成整数，"
                "务必用带 a-f 字母的 uuid 或加引号"]
    if not re.fullmatch(r"[0-9a-f]{32}", u):
        return ["必须是 32 位小写十六进制（不带连字符）"]
    if u[12] != "4":
        return ["第 13 位必须是 `4`（UUIDv4 版本位）。像 1000...0001 这种自编号会报"
                "「需用 UUIDv4」"]
    if u[16] not in "89ab":
        return ["第 17 位必须是 8/9/a/b 之一（UUIDv4 变体位）"]
    return []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def check_enum(obj, key, where):
    if key in obj and isinstance(obj[key], (int, float)):
        err("%s: `%s` 用了数字 %r，7.0+ 必须用大写字符串（如 %s）"
            % (where, key, obj[key], " / ".join(sorted(ENUMS.get(key, {"ENABLED"})))))
    elif key in obj and key in ENUMS and obj[key] not in ENUMS[key]:
        err("%s: `%s` = %r 不是合法枚举值，可选: %s"
            % (where, key, obj[key], ", ".join(sorted(ENUMS[key]))))


def main(path):
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8"))
    except Exception as e:
        sys.exit("%sYAML 解析失败: %s%s" % (RED, e, RST))

    if not isinstance(doc, dict) or "zabbix_export" not in doc:
        sys.exit("%s缺少根节点 zabbix_export%s" % (RED, RST))

    zx = doc["zabbix_export"]
    ver = zx.get("version")
    if ver is None:
        err("zabbix_export.version 缺失")
    elif not isinstance(ver, str):
        err("zabbix_export.version = %r 必须是字符串（写成 '7.4'，不是 7.4）" % (ver,))

    tpls = zx.get("templates") or []
    if not tpls:
        err("没有 templates 节点")

    all_uuid = {}
    tpl_names = []

    for ti, t in enumerate(tpls, 1):
        tname = t.get("template")
        where = "templates/template(%d)" % ti
        if not tname:
            err("%s: 缺少 template 名称" % where)
            tname = "<unnamed>"
        tpl_names.append(tname)
        where = "模板[%s]" % tname

        # 1) 模板对象不允许顶层 triggers
        if "triggers" in t:
            err("%s: 不允许顶层 `triggers`。触发器必须挂在 item 下面（item.triggers）" % where)

        # 2) 模板自身 uuid
        if not t.get("uuid"):
            err("%s: 缺少 uuid" % where)

        def reg_uuid(obj, w):
            u = obj.get("uuid")
            if u:
                for problem in uuid_problems(u):
                    err("%s: uuid %r %s" % (w, u, problem))
                all_uuid.setdefault(str(u), []).append(w)

        reg_uuid(t, where)

        # 3) items
        for it in t.get("items") or []:
            iw = "%s.items[%s]" % (where, it.get("key") or it.get("name") or "?")
            for req in ("name", "key", "type", "value_type"):
                if req not in it:
                    err("%s: 缺少必填字段 `%s`" % (iw, req))
            if not it.get("uuid"):
                err("%s: 缺少 uuid（item 必须有）" % iw)
            reg_uuid(it, iw)
            for k in ("type", "value_type", "status", "allow_traps"):
                check_enum(it, k, iw)

            if it.get("value_type") == "UPTIME":
                err("%s: `value_type: UPTIME` 在 Zabbix 7.4 中不存在。"
                    "改用 value_type: UNSIGNED + units: uptime"
                    "（Timeticks 还要加 preprocessing MULTIPLIER 0.01 换算成秒）" % iw)

            if it.get("value_type") == "CHAR" and str(it.get("trends", "")) not in ("0", "0d", ""):
                warn("%s: CHAR 类型 item 的 trends 建议为 '0'" % iw)
            if it.get("type") == "SNMP_AGENT" and not it.get("snmp_oid"):
                err("%s: SNMP_AGENT 类型必须有 snmp_oid" % iw)
            if it.get("type") == "CALCULATED" and not it.get("params"):
                err("%s: CALCULATED 类型必须有 params" % iw)

            # 4) 模板 item 下的 trigger：必须有 uuid
            #    （注意：host 对象的 item 下 trigger 不允许 uuid，这是两段不同的规则）
            for tr in it.get("triggers") or []:
                ttw = "%s.triggers[%s]" % (iw, tr.get("name", "?"))
                if not tr.get("uuid"):
                    err("%s: 缺少 uuid（模板里的 trigger 必须有；报错形如 `标签 uuid 丢失`）" % ttw)
                else:
                    reg_uuid(tr, ttw)
                for req in ("expression", "name"):
                    if req not in tr:
                        err("%s: 缺少必填字段 `%s`" % (ttw, req))
                for k in ("priority", "status", "manual_close"):
                    check_enum(tr, k, ttw)
                if tr.get("status") == "DISABLED":
                    if not tr.get("description"):
                        warn("%s: 默认禁用的触发器建议写清楚为什么禁用" % ttw)

        # 5) 发现规则
        for dr in t.get("discovery_rules") or []:
            dw = "%s.discovery_rules[%s]" % (where, dr.get("name") or dr.get("key") or "?")
            for req in ("name", "key"):
                if req not in dr:
                    err("%s: 缺少必填字段 `%s`" % (dw, req))
            if not dr.get("uuid"):
                err("%s: 缺少 uuid（discovery_rule 必须有）" % dw)
            reg_uuid(dr, dw)
            if "triggers" in dr:
                err("%s: discovery_rule 不接受 `triggers`，用 trigger_prototypes" % dw)

            flt = dr.get("filter") or {}
            if flt:
                ev = flt.get("evaltype")
                if ev is not None:
                    if isinstance(ev, (int, float)):
                        err("%s.filter: evaltype 用了数字，必须是字符串" % dw)
                    elif ev not in ENUMS["evaltype"]:
                        err("%s.filter: evaltype = %r 非法，可选: %s"
                            "（注意 `AND`/`OR` 是错的，应为 `AND_OR`/`XML_AND`/`XML_OR`）"
                            % (dw, ev, ", ".join(sorted(ENUMS["evaltype"]))))
                for c in flt.get("conditions") or []:
                    op = c.get("operator")
                    if op is not None and op not in ENUMS["operator"]:
                        err("%s.filter.conditions: operator = %r 非法，可选: %s"
                            % (dw, op, ", ".join(sorted(ENUMS["operator"]))))
                    if "macro" not in c:
                        err("%s.filter.conditions: 缺少 macro" % dw)

            for ip_ in dr.get("item_prototypes") or []:
                iw = "%s.item_prototypes[%s]" % (dw, ip_.get("key") or "?")
                if not ip_.get("uuid"):
                    err("%s: 缺少 uuid（item_prototype 必须有）" % iw)
                reg_uuid(ip_, iw)
                for req in ("name", "key", "type", "value_type"):
                    if req not in ip_:
                        err("%s: 缺少必填字段 `%s`" % (iw, req))
                for k in ("type", "value_type", "status"):
                    check_enum(ip_, k, iw)
                if ip_.get("type") == "SNMP_AGENT" and not ip_.get("snmp_oid"):
                    err("%s: SNMP_AGENT 类型必须有 snmp_oid" % iw)

            for tp in dr.get("trigger_prototypes") or []:
                tw = "%s.trigger_prototypes[%s]" % (dw, tp.get("name", "?"))
                if not tp.get("uuid"):
                    err("%s: 缺少 uuid（trigger_prototype 必须有，和 trigger 相反）" % tw)
                reg_uuid(tp, tw)
                for req in ("expression", "name"):
                    if req not in tp:
                        err("%s: 缺少必填字段 `%s`" % (tw, req))
                for k in ("priority", "status", "discover", "manual_close"):
                    check_enum(tp, k, tw)

        # 6) 表达式里的模板名必须与 template: 一致
        #    只扫真正的表达式字符串（函数调用形如 func(/模板名/item.key)）
        exprs = []
        for it in t.get("items") or []:
            for tr in it.get("triggers") or []:
                if tr.get("expression"):
                    exprs.append(tr["expression"])
                if tr.get("recovery_expression"):
                    exprs.append(tr["recovery_expression"])
        for dr in t.get("discovery_rules") or []:
            for tp in dr.get("trigger_prototypes") or []:
                if tp.get("expression"):
                    exprs.append(tp["expression"])
                if tp.get("recovery_expression"):
                    exprs.append(tp["recovery_expression"])
        for ex in exprs:
            for m in set(re.findall(r"\(/([^/,]+)/", ex)):
                if m != tname:
                    err("模板[%s]: 表达式里引用了模板 `%s`，与 template 名 `%s` 不一致"
                        % (tname, m, tname))

    # 7) uuid 唯一
    for u, ws in all_uuid.items():
        if len(ws) > 1:
            err("uuid 重复 %s 出现在: %s" % (u, ", ".join(ws)))

    # 输出
    print("═" * 58)
    print("模板: %s" % ", ".join(tpl_names))
    print("UUID: %d 个，%d 个唯一" % (sum(len(v) for v in all_uuid.values()), len(all_uuid)))
    for ti, t in enumerate(tpls, 1):
        it = t.get("items") or []
        dr = t.get("discovery_rules") or []
        nested = sum(len(i.get("triggers") or []) for i in it)
        protos = sum(len(d.get("item_prototypes") or []) for d in dr)
        tprotos = sum(len(d.get("trigger_prototypes") or []) for d in dr)
        print("  监控项 %d / 发现规则 %d / 项原型 %d / 触发器(挂 item) %d / 触发器原型 %d"
              % (len(it), len(dr), protos, nested, tprotos))
    print("═" * 58)

    for w in warnings:
        print("%s[警告]%s %s" % (YEL, RST, w))
    for e in errors:
        print("%s[错误]%s %s" % (RED, RST, e))

    if errors:
        print()
        print("%s校验失败: %d 个错误%s" % (RED, len(errors), RST))
        return 1
    print("%s✓ 结构校验通过%s（下一步: verify-template-oids.sh 连真机验证 OID）" % (GRN, RST))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: python3 validate-template.py <template.yaml>")
    sys.exit(main(sys.argv[1]))
