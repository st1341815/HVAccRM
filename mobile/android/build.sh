#!/bin/bash
# ---------------------------------------------------------------
# HVAccRM 移动端 APK 构建脚本（无 Gradle / 无 AndroidX / 无网络依赖）
#
#   ./build.sh            打一个 debug 签名的可安装 APK
#   ./build.sh release    用 keystore 正式签名（需先按提示生成 keystore）
#
# 依赖：JDK 17 + Android SDK build-tools(34) + platforms/android-35
#       SDK 路径可用环境变量 ANDROID_SDK 覆盖
# ---------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

SDK="${ANDROID_SDK:-/opt/android-sdk}"
BT="$SDK/bt/android-14"                 # build-tools 34 解压后内部目录名是 android-14
PLATFORM="$SDK/platforms/android-35/android.jar"

VERSION_NAME="0.1.2"
VERSION_CODE="3"
APP_ID="cn.ii2.hvaccrm"
OUT="build"
DIST="dist"

for f in "$BT/aapt2" "$BT/d8" "$BT/apksigner" "$BT/zipalign" "$PLATFORM"; do
  [ -e "$f" ] || { echo "缺少构建工具: $f"; exit 1; }
done

echo "==> 清理"
rm -rf "$OUT"
mkdir -p "$OUT/res" "$OUT/gen" "$OUT/classes" "$OUT/dex" "$DIST"

echo "==> 1/6 编译资源 (aapt2 compile)"
"$BT/aapt2" compile --dir res -o "$OUT/res/resources.zip"

echo "==> 2/6 链接资源并生成 R.java (aapt2 link)"
"$BT/aapt2" link \
  -o "$OUT/base.apk" \
  -I "$PLATFORM" \
  --manifest AndroidManifest.xml \
  --java "$OUT/gen" \
  -A assets \
  --min-sdk-version 24 \
  --target-sdk-version 35 \
  --version-code "$VERSION_CODE" \
  --version-name "$VERSION_NAME" \
  --auto-add-overlay \
  "$OUT/res/resources.zip"

echo "==> 3/6 编译 Java (javac -> class)"
find src -name '*.java' > "$OUT/sources.txt"
find "$OUT/gen" -name '*.java' >> "$OUT/sources.txt"
javac -nowarn --release 11 \
  -classpath "$PLATFORM" \
  -d "$OUT/classes" \
  @"$OUT/sources.txt"

echo "==> 4/6 转 dex (d8)"
find "$OUT/classes" -name '*.class' > "$OUT/classes.txt"
"$BT/d8" --lib "$PLATFORM" --min-api 24 --release \
  --output "$OUT/dex" \
  $(cat "$OUT/classes.txt" | tr '\n' ' ')

echo "==> 5/6 组装 APK"
cp "$OUT/base.apk" "$OUT/unaligned.apk"
( cd "$OUT/dex" && zip -q -X "../unaligned.apk" classes.dex )

echo "==> 6/6 对齐 + 签名"
"$BT/zipalign" -p -f 4 "$OUT/unaligned.apk" "$OUT/aligned.apk"

MODE="${1:-debug}"
if [ "$MODE" = "release" ]; then
  KS="release.keystore"
  if [ ! -f "$KS" ]; then
    echo "未找到 $KS，正在生成（请牢记口令，发布更新必须用同一个 keystore）"
    keytool -genkeypair -v -keystore "$KS" -alias hvaccrm \
      -keyalg RSA -keysize 2048 -validity 10950 \
      -storepass "${KS_PASS:-hvaccrm2026}" -keypass "${KS_PASS:-hvaccrm2026}" \
      -dname "CN=HVAccRM Mobile, OU=Internal, O=HVAccRM, L=Hefei, ST=Anhui, C=CN"
  fi
  "$BT/apksigner" sign \
    --ks "$KS" --ks-key-alias hvaccrm \
    --ks-pass "pass:${KS_PASS:-hvaccrm2026}" \
    --key-pass "pass:${KS_PASS:-hvaccrm2026}" \
    --out "$DIST/hvaccrm-mobile-$VERSION_NAME-release.apk" \
    "$OUT/aligned.apk"
  APK="$DIST/hvaccrm-mobile-$VERSION_NAME-release.apk"
else
  # keystore 必须放在 build/ 之外：build/ 每次都被 rm -rf，
  # 一旦密钥跟着重建，签名就变了，用户手机上会 INSTALL_FAILED_UPDATE_INCOMPATIBLE，
  # 只能卸载重装（配置和登录态全丢）。这条踩过，别再改回去。
  KS=".debug.keystore"
  if [ ! -f "$KS" ]; then
    keytool -genkeypair -v -keystore "$KS" -alias androiddebugkey \
      -keyalg RSA -keysize 2048 -validity 10950 \
      -storepass android -keypass android \
      -dname "CN=Android Debug, O=Android, C=US" >/dev/null 2>&1
  fi
  "$BT/apksigner" sign \
    --ks "$KS" --ks-key-alias androiddebugkey \
    --ks-pass pass:android --key-pass pass:android \
    --out "$DIST/hvaccrm-mobile-$VERSION_NAME-debug.apk" \
    "$OUT/aligned.apk"
  APK="$DIST/hvaccrm-mobile-$VERSION_NAME-debug.apk"
fi

"$BT/apksigner" verify --print-certs "$APK" | head -5
echo
echo "✅ 构建完成: $APK ($(du -h "$APK" | cut -f1))"
echo "   安装: adb install -r \"$APK\"   或把 APK 传到手机点击安装"
