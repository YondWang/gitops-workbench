#!/usr/bin/env bash
# 发布后 Nextcloud reconcile：保证刚发布到 /public/Versions 的目录立即可被用户移动/重命名/编辑。
#
# 背景：CI 发布器直接往 Nextcloud groupfolder 物理目录写文件，若索引(groupfolders:scan)
# 不完整、或属主不是 www-data，WebDAV 移动/重命名会被拒。本脚本做两件幂等的事：
#   1) 若以 root 运行，则把目标日期目录整体 chown 给 www-data:www-data；
#   2) 以 www-data 身份对 整个日期目录(而非单个 category) 跑 groupfolders:scan 建索引。
#
# 用法:
#   nextcloud-publish-reconcile.sh --date YYYY-MM-DD --version V [--category CAT]
#     可选: --container envs-app-1 --group-folder 1 --nc-root /home/data/nextcloud/app/data/__groupfolders
#   两者至少提供其一，或传 --date_root "YYYY-MM-DD_VERSION" 直接定位日期目录。
#
# 依赖: docker 组权限(允许运行 docker exec)、且有名为 $NEXTCLOUD_CONTAINER 的 nextcloud 容器。
# 幂等：重复执行安全；失败由调用方决定是否致命(建议非致命)。
set -Eeuo pipefail

DATE_VALUE=""
VERSION=""
CATEGORY=""
DATE_ROOT=""
NEXTCLOUD_CONTAINER="${SIMOS_PUBLISH_NEXTCLOUD_CONTAINER:-envs-app-1}"
NEXTCLOUD_GROUPFOLDER_ID="${SIMOS_PUBLISH_NEXTCLOUD_GROUPFOLDER_ID:-1}"
NEXTCLOUD_VERSIONS_ROOT="${SIMOS_PUBLISH_NEXTCLOUD_VERSIONS_ROOT:-/home/data/nextcloud/app/data/__groupfolders/1/files/Versions}"

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --date) DATE_VALUE="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --category) CATEGORY="${2:-}"; shift 2 ;;
    --date_root) DATE_ROOT="${2:-}"; shift 2 ;;
    --container) NEXTCLOUD_CONTAINER="${2:-}"; shift 2 ;;
    --group-folder) NEXTCLOUD_GROUPFOLDER_ID="${2:-}"; shift 2 ;;
    --nc-root) NEXTCLOUD_VERSIONS_ROOT="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "[reconcile] unknown argument: $1" >&2; exit 2 ;;
  esac
done

safe_date() { [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; }
safe_version() { [[ "$1" =~ ^[VvFfTt]?[0-9]+([.][0-9]+)+$ ]]; }
safe_root() { [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[A-Za-z0-9._-]+$ ]]; }

if [[ -n "$DATE_ROOT" ]]; then
  safe_root "$DATE_ROOT" || { echo "[reconcile] invalid date_root: $DATE_ROOT" >&2; exit 2; }
  PUBLISH_ROOT_NAME="$DATE_ROOT"
else
  [[ -n "$DATE_VALUE" && -n "$VERSION" ]] || { echo "[reconcile] need --date/--version or --date_root" >&2; exit 2; }
  safe_date "$DATE_VALUE" || { echo "[reconcile] invalid date: $DATE_VALUE" >&2; exit 2; }
  safe_version "$VERSION" || { echo "[reconcile] invalid version: $VERSION" >&2; exit 2; }
  PUBLISH_ROOT_NAME="${DATE_VALUE}_${VERSION}"
fi

VERSION_TARGET="$NEXTCLOUD_VERSIONS_ROOT/$PUBLISH_ROOT_NAME"
# chown 只能在 root 且能访问到目录时执行；CI(Runner,非root) 跳过，沿用发布器已做的 chown。
if [[ "$(id -u)" -eq 0 && -d "$VERSION_TARGET" ]]; then
  chown -R www-data:www-data "$VERSION_TARGET" || :
fi
# 索引以容器内 occ(groupfolders:scan, www-data 身份) 为准，走 Nextcloud 存储抽象，
# 不受宿主目录遍历权限影响；对不存在的路径扫描会静默跳过，因此幂等安全。
if docker exec --user www-data "$NEXTCLOUD_CONTAINER" \
     php occ groupfolders:scan "$NEXTCLOUD_GROUPFOLDER_ID" --path="/Versions/${PUBLISH_ROOT_NAME}" \
     >/dev/null 2>&1; then
  echo "[reconcile] OK ${PUBLISH_ROOT_NAME}"
else
  echo "[reconcile] groupfolders:scan skipped/failed for ${PUBLISH_ROOT_NAME}" >&2
fi