#!/usr/bin/env bash
# Install persistent SocketCAN naming and link configuration for SCOUT Mini.

set -euo pipefail

INTERFACE="can_scout"
BITRATE="500000"
RESTART_MS="100"
USB_VENDOR="1d50"
USB_PRODUCT="606f"
ADAPTER_SERIAL=""
TARGET_ROOT="/"
SKIP_MODULE_CHECK="false"

usage() {
  echo "Usage: $0 [--serial SERIAL] [--interface NAME] [--output-dir DIR]" >&2
  echo "          [--bitrate BPS] [--restart-ms MS] [--skip-module-check]" >&2
}

die() {
  echo "ERROR: $*" >&2
  exit 2
}

while (($#)); do
  case "$1" in
    --serial)
      (($# >= 2)) || die "--serial requires a value"
      ADAPTER_SERIAL="$2"
      shift 2
      ;;
    --interface)
      (($# >= 2)) || die "--interface requires a value"
      INTERFACE="$2"
      shift 2
      ;;
    --bitrate)
      (($# >= 2)) || die "--bitrate requires a value"
      BITRATE="$2"
      shift 2
      ;;
    --restart-ms)
      (($# >= 2)) || die "--restart-ms requires a value"
      RESTART_MS="$2"
      shift 2
      ;;
    --output-dir)
      (($# >= 2)) || die "--output-dir requires a value"
      TARGET_ROOT="$2"
      shift 2
      ;;
    --skip-module-check)
      SKIP_MODULE_CHECK="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "$TARGET_ROOT" ]] || die "output directory must not be empty"
command -v realpath >/dev/null || die "realpath is required"
TARGET_ROOT="$(realpath -m -- "$TARGET_ROOT")"

[[ "$INTERFACE" =~ ^[A-Za-z0-9_.-]+$ ]] || die "invalid interface name: $INTERFACE"
[[ "$BITRATE" =~ ^[0-9]+$ ]] && ((BITRATE > 0)) || die "invalid bitrate: $BITRATE"
[[ "$RESTART_MS" =~ ^[0-9]+$ ]] || die "invalid restart-ms: $RESTART_MS"

detect_adapter_serial() {
  local interface_path driver serial
  local -a serials=()
  command -v udevadm >/dev/null || die "udevadm is required to detect the adapter serial"
  for interface_path in /sys/class/net/*; do
    [[ -e "$interface_path/device/driver" ]] || continue
    driver="$(basename "$(readlink -f "$interface_path/device/driver")")"
    [[ "$driver" == "gs_usb" ]] || continue
    serial="$(
      udevadm info --query=property --path="$interface_path" |
        sed -n 's/^ID_SERIAL_SHORT=//p' |
        head -n 1
    )"
    [[ -n "$serial" ]] && serials+=("$serial")
  done
  ((${#serials[@]} == 1)) || die \
    "expected exactly one connected gs_usb adapter; pass --serial explicitly"
  echo "${serials[0]}"
}

if [[ "$SKIP_MODULE_CHECK" != "true" ]]; then
  command -v modinfo >/dev/null || die "modinfo is required for the gs_usb kernel check"
  RUNNING_KERNEL="$(uname -r)"
  MODULE_KERNEL="$(modinfo -F vermagic gs_usb 2>/dev/null | awk 'NR == 1 {print $1}')"
  [[ -n "$MODULE_KERNEL" ]] || die "gs_usb is not installed for kernel $RUNNING_KERNEL"
  [[ "$MODULE_KERNEL" == "$RUNNING_KERNEL" ]] || die \
    "gs_usb was built for $MODULE_KERNEL but the running kernel is $RUNNING_KERNEL"
fi

if [[ -z "$ADAPTER_SERIAL" ]]; then
  ADAPTER_SERIAL="$(detect_adapter_serial)"
fi
[[ "$ADAPTER_SERIAL" =~ ^[A-Za-z0-9._:-]+$ ]] || die \
  "invalid adapter serial: $ADAPTER_SERIAL"

if [[ "$TARGET_ROOT" == "/" && "$(id -u)" -ne 0 ]]; then
  die "installing under /etc requires root; rerun with sudo"
fi

TARGET_PREFIX="${TARGET_ROOT%/}"
RULE_DIR="$TARGET_PREFIX/etc/udev/rules.d"
SERVICE_DIR="$TARGET_PREFIX/etc/systemd/system"
MODULES_DIR="$TARGET_PREFIX/etc/modules-load.d"
install -d -m 0755 "$RULE_DIR" "$SERVICE_DIR" "$MODULES_DIR"

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEMP_DIR"' EXIT

cat >"$TEMP_DIR/99-scout-can.rules" <<EOF
# Managed by scout_mini_ws/scripts/install_scout_can.sh
# Pull the service in from the USB device event, which occurs before its network
# child is created and renamed. Attaching SYSTEMD_WANTS to the net event races
# the rename: systemd may consider the net device active before seeing the want.
SUBSYSTEM=="usb", ACTION=="add", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="$USB_VENDOR", ATTR{idProduct}=="$USB_PRODUCT", ATTR{serial}=="$ADAPTER_SERIAL", TAG+="systemd", ENV{SYSTEMD_WANTS}+="scout-can@$INTERFACE.service"
SUBSYSTEM=="net", ACTION=="add", ENV{ID_NET_DRIVER}=="gs_usb", ENV{ID_VENDOR_ID}=="$USB_VENDOR", ENV{ID_MODEL_ID}=="$USB_PRODUCT", ENV{ID_SERIAL_SHORT}=="$ADAPTER_SERIAL", NAME="$INTERFACE"
EOF

cat >"$TEMP_DIR/scout-can@.service" <<EOF
[Unit]
Description=Configure SCOUT SocketCAN interface %I
BindsTo=sys-subsystem-net-devices-%i.device
After=sys-subsystem-net-devices-%i.device

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/ip link set dev %i down
ExecStart=/usr/sbin/ip link set dev %i type can bitrate $BITRATE restart-ms $RESTART_MS
ExecStart=/usr/sbin/ip link set dev %i up
ExecStop=/usr/sbin/ip link set dev %i down
EOF

printf '%s\n' 'gs_usb' >"$TEMP_DIR/scout-can.conf"

install -m 0644 "$TEMP_DIR/99-scout-can.rules" "$RULE_DIR/99-scout-can.rules"
install -m 0644 "$TEMP_DIR/scout-can@.service" "$SERVICE_DIR/scout-can@.service"
install -m 0644 "$TEMP_DIR/scout-can.conf" "$MODULES_DIR/scout-can.conf"

echo "Installed SCOUT CAN assets under ${TARGET_ROOT}:"
echo "  serial=$ADAPTER_SERIAL interface=$INTERFACE bitrate=$BITRATE restart_ms=$RESTART_MS"

if [[ "$TARGET_ROOT" != "/" ]]; then
  exit 0
fi

systemctl daemon-reload
udevadm control --reload-rules
modprobe gs_usb

if ip link show dev "$INTERFACE" >/dev/null 2>&1; then
  systemctl restart "scout-can@$INTERFACE.service"
  ip -details link show dev "$INTERFACE"
else
  echo "Reconnect only the USB-CAN adapter, then verify:"
  echo "  ip -details link show $INTERFACE"
fi
