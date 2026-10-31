#!/usr/bin/env bash
# 探测 SNMP 设备的真实身份与 MIB 能力矩阵
# 用法: bash probe-device.sh <HOST> <COMMUNITY> [enterprise-number]
set -uo pipefail

H="${1:?用法: probe-device.sh <HOST> <COMMUNITY> [enterprise]}"
COMM="${2:?缺少 community}"
ENT="${3:-}"

C="-v2c -c $COMM -t 3 -r 2 -On"
ok=0; bad=0

probe() {
  local label="$1" oid="$2" r
  r=$(snmpget $C "$H" "$oid" 2>&1)
  if echo "$r" | grep -qiE "No Such|Timeout|Error in packet|authorizationError"; then
    printf "  \033[31m✗\033[0m %-22s %s\n" "$label" "$(echo "$r" | sed 's/.*: //' | cut -c1-46)"
    bad=$((bad+1))
  else
    printf "  \033[32m✓\033[0m %-22s %s\n" "$label" "$(echo "$r" | sed 's/.*= //' | cut -c1-46)"
    ok=$((ok+1))
  fi
}

echo "══════ 身份 ══════"
probe sysDescr          1.3.6.1.2.1.1.1.0
probe sysObjectID       1.3.6.1.2.1.1.2.0
probe sysName           1.3.6.1.2.1.1.5.0
probe sysLocation       1.3.6.1.2.1.1.6.0
probe sysContact        1.3.6.1.2.1.1.4.0
probe sysUpTime         1.3.6.1.2.1.1.3.0
probe entModelName      1.3.6.1.2.1.47.1.1.1.1.13.1
probe entSerialNum      1.3.6.1.2.1.47.1.1.1.1.11.1
probe entPhysicalDescr  1.3.6.1.2.1.47.1.1.1.1.2.1

echo
echo "══════ 接口能力 ══════"
probe ifDescr           1.3.6.1.2.1.2.2.1.2.1
probe ifType            1.3.6.1.2.1.2.2.1.3.1
probe ifSpeed           1.3.6.1.2.1.2.2.1.5.1
probe ifAdminStatus     1.3.6.1.2.1.2.2.1.7.1
probe ifOperStatus      1.3.6.1.2.1.2.2.1.8.1
probe ifInOctets        1.3.6.1.2.1.2.2.1.10.1
probe ifInErrors        1.3.6.1.2.1.2.2.1.14.1
probe ifOutOctets       1.3.6.1.2.1.2.2.1.16.1
probe ifOutErrors       1.3.6.1.2.1.2.2.1.20.1

echo
echo "══════ ifXTable（64 位计数器，优先用这些）══════"
probe ifName            1.3.6.1.2.1.31.1.1.1.1.1
probe ifHCInOctets      1.3.6.1.2.1.31.1.1.1.6.1
probe ifHCInUcastPkts   1.3.6.1.2.1.31.1.1.1.7.1
probe ifHCOutOctets     1.3.6.1.2.1.31.1.1.1.10.1
probe ifHighSpeed       1.3.6.1.2.1.31.1.1.1.15.1
probe ifAlias           1.3.6.1.2.1.31.1.1.1.18.1

echo
echo "══════ 其他标准 MIB ══════"
probe bridgeMAC         1.3.6.1.2.1.17.4.3.1.2
probe hrProcessorLoad   1.3.6.1.2.1.25.3.3.1.2
probe hrStorageDescr    1.3.6.1.2.1.25.2.3.1.3
probe lldpRemSysName    1.0.8802.1.1.2.1.4.1.1.9
probe entSensorValue    1.3.6.1.2.1.99.1.1.1.4
probe ciscoPrivate      1.3.6.1.4.1.9.2.1.1.0

echo
echo "══════ 接口列表 ══════"
snmpwalk -v2c -c "$COMM" -t 3 -r 1 -On "$H" 1.3.6.1.2.1.31.1.1.1.1 2>/dev/null \
  | sed 's/^/  /' | head -40
n=$(snmpwalk -v2c -c "$COMM" -t 3 -r 1 -On "$H" 1.3.6.1.2.1.31.1.1.1.1 2>/dev/null | wc -l)
echo "  共 $n 个接口"

if [ -n "$ENT" ]; then
  echo
  echo "══════ 私有树 1.3.6.1.4.1.$ENT（标量）══════"
  snmpwalk -v2c -c "$COMM" -t 3 -r 1 -On "$H" "1.3.6.1.4.1.$ENT" > /tmp/priv_$ENT.txt 2>/dev/null
  echo "  总 OID 数: $(wc -l < /tmp/priv_$ENT.txt)"
  echo "  标量（以 .0 结尾，通常是全局指标/传感器）:"
  grep -E '\.0 = ' "/tmp/priv_$ENT.txt" | sed 's/^/    /' | head -50
  echo "  原始 dump: /tmp/priv_$ENT.txt"
fi

echo
printf "══════ 汇总: 可用 %d / 不可用 %d ══════\n" "$ok" "$bad"
echo "提示:"
echo "  1. ifSpeed 若返回 4294967295 是 32 位溢出，带宽一律改用 ifHighSpeed"
echo "  2. hrProcessorLoad / entSensorValue 不可用时，CPU/温度要去私有树找"
echo "  3. 表型 OID 不带索引查询会返回 No Such Instance，不等于不支持"
