#!/usr/bin/env bash
# 把模板里每一个 snmp_oid 拿到真机上验证，确保导入后没有死键
# 用法: bash verify-template-oids.sh <template.yaml> <HOST> <COMMUNITY>
set -uo pipefail

TPL="${1:?用法: verify-template-oids.sh <template.yaml> <HOST> <COMMUNITY>}"
H="${2:?缺少 HOST}"
COMM="${3:?缺少 COMMUNITY}"

TMP=$(mktemp)
python3 - "$TPL" > "$TMP" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding='utf-8'))
t = (d.get('zabbix_export', {}).get('templates') or [{}])[0]
rows = []
for it in t.get('items') or []:
    if it.get('snmp_oid'):
        rows.append((it.get('key', '?'), it['snmp_oid']))
for dr in t.get('discovery_rules') or []:
    for it in dr.get('item_prototypes') or []:
        if it.get('snmp_oid'):
            rows.append((it.get('key', '?'), it['snmp_oid'].replace('{#SNMPINDEX}', '1')))
for k, o in rows:
    print("%s\t%s" % (k, o))
PY

total=$(wc -l < "$TMP")
echo "模板: $TPL"
echo "待验证 OID: $total 个 (设备 $H)"
echo "──────────────────────────────────────────────────────────"

pass=0; fail=0; failed_list=""
while IFS=$'\t' read -r key oid; do
  [ -z "${oid:-}" ] && continue
  r=$(snmpget -v2c -c "$COMM" -t 3 -r 1 -On "$H" "$oid" 2>&1)
  if echo "$r" | grep -qiE "No Such|Timeout|Error in packet|authorizationError"; then
    printf "  \033[31m✗\033[0m %-38s %s\n" "$key" "$oid"
    fail=$((fail+1)); failed_list="$failed_list$key ($oid)\n"
  else
    printf "  \033[32m✓\033[0m %-38s %s\n" "$key" "$(echo "$r" | sed 's/.*= //' | cut -c1-38)"
    pass=$((pass+1))
  fi
done < "$TMP"

rm -f "$TMP"
echo "──────────────────────────────────────────────────────────"
printf "可用 %d / 失败 %d\n" "$pass" "$fail"

if [ "$fail" -gt 0 ]; then
  echo
  echo "失败项（导入后会是 unsupported 的死键，必须处理）:"
  printf "$failed_list" | sed 's/^/  - /'
  exit 1
fi
echo "验收通过：0 失败，可以交付。"
