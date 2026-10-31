#!/usr/bin/env bash
# 诊断（并可选修复）Zabbix 的 ID 分配器漂移
#
# 背景：Zabbix 绝大多数表的主键**没有 AUTO_INCREMENT**，ID 由 `ids` 表统一分配。
#       一旦有人手工 INSERT 了带显式主键的行，`ids.nextid` 就会落后，
#       之后任何导入/新增都会撞 `Duplicate entry 'xxx' for key 'PRIMARY'`。
#
# 用法:
#   bash repair-zabbix-ids.sh                 # 只诊断
#   bash repair-zabbix-ids.sh --fix           # 诊断 + 修复
#
# 需要本机 mysql 有 zabbix 库的访问权限；默认沿用 Zabbix 官方镜像的 zabbix/password。
set -uo pipefail

FIX=0
[ "${1:-}" = "--fix" ] && FIX=1

mysql_() { mysql -N -uzabbix -ppassword "${DB:-zabbix}" "$@"; }

echo "══════ Zabbix ID 分配器诊断 ══════"
echo
echo "语义（来源: /usr/share/zabbix/ui/include/classes/db/DB.php reserveIds()）:"
echo "  ids.nextid = 最后已分配出去的 ID"
echo "  下次分配 = nextid + 1"
echo "  所以正常状态是  nextid == MAX(主键)"
echo "  异常状态是  nextid <  MAX(主键)   → 下次分配必撞主键"
echo

bad=0
while IFS=$'\t' read -r tbl fld nid; do
  [ -z "${tbl:-}" ] && continue
  mx=$(mysql_ -e "SELECT IFNULL(MAX(\`$fld\`),0) FROM \`$tbl\`" 2>/dev/null)
  [ -z "$mx" ] && continue
  if [ "$nid" -lt "$mx" ]; then
    bad=$((bad+1))
    printf "  \033[31m✗ %-22s %-16s nextid=%-8s 实际最大=%-8s 落后 %s\033[0m\n" \
      "$tbl" "$fld" "$nid" "$mx" "$((mx-nid))"
    if [ "$FIX" = "1" ]; then
      mysql_ -e "UPDATE ids SET nextid=$mx WHERE table_name='$tbl' AND field_name='$fld'" 2>/dev/null \
        && printf "      → 已修复: nextid 设为 %s（下次分配 %s）\n" "$mx" "$((mx+1))"
    fi
  else
    printf "  \033[32m✓\033[0m %-22s %-16s nextid=%-8s max=%-8s\n" "$tbl" "$fld" "$nid" "$mx"
  fi
done < <(mysql_ -e "SELECT table_name, field_name, nextid FROM ids" 2>/dev/null)

echo
if [ "$bad" = "0" ]; then
  echo "全部一致，无需处理。"
else
  echo "发现 $bad 个表漂移。"
  if [ "$FIX" = "1" ]; then
    echo "已修复。建议重新导出一次配置确认。"
  else
    echo "确认是手工 INSERT 造成的后，加 --fix 修复。"
    echo "修复只改 ids 表，不动业务数据。"
  fi
fi
echo
echo "注意：不要用 'nextid = max + 1' 修复，那会跳过 1 个 ID。"
echo "      正确写法是 nextid = max（因为下次分配会自动 +1）。"
