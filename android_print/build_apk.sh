#!/bin/bash
set -e
# Terry Jar Print (patok OTK plansheti uchun chek printeri ilovasi) APK yig'ish skripti — Gradle'siz, android_control kabi.
# Agent tokeni token.txt da (git'ga kirmaydi): superadmin → Chek printerlari sahifasidagi token.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

[ -f token.txt ] || { echo "Xato: token.txt yo'q — /printers/ sahifasidagi tokenni shu faylga yozing"; exit 1; }
TOKEN="$(tr -d '[:space:]' < token.txt)"
SERVER="${SERVER:-https://terryjar.uz}"
VERSION_CODE="${VERSION_CODE:-1}"

if [ -d "/Applications/Android Studio.app/Contents/jbr/Contents/Home" ]; then
    export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
else
    export JAVA_HOME="$(/usr/libexec/java_home 2>/dev/null || echo '')"
fi
SDK_ROOT="/opt/homebrew/share/android-commandlinetools"
BUILD_TOOLS="$SDK_ROOT/build-tools/34.0.0"
PLATFORM="$SDK_ROOT/platforms/android-34/android.jar"

BUILD_DIR="$DIR/build_temp"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/gen/uz/terryjar/print" "$BUILD_DIR/classes" "$BUILD_DIR/dex"
APP_DIR="$DIR/app/src/main"

cat > "$BUILD_DIR/gen/uz/terryjar/print/Config.java" <<JAVA
package uz.terryjar.print;
final class Config {
    static final String SERVER = "$SERVER";
    static final String TOKEN = "$TOKEN";
    private Config() {}
}
JAVA

echo "1. Resurslar..."
"$BUILD_TOOLS/aapt2" compile --dir "$APP_DIR/res" -o "$BUILD_DIR/compiled_res.zip"
"$BUILD_TOOLS/aapt2" link -I "$PLATFORM" --manifest "$APP_DIR/AndroidManifest.xml" --java "$BUILD_DIR/gen" \
    --min-sdk-version 30 --target-sdk-version 34 --version-code "$VERSION_CODE" --version-name "1.$VERSION_CODE" \
    -o "$BUILD_DIR/unaligned_res.apk" "$BUILD_DIR/compiled_res.zip" --auto-add-overlay

echo "2. Java..."
"$JAVA_HOME/bin/javac" --release 11 -cp "$PLATFORM" -d "$BUILD_DIR/classes" -nowarn -Xlint:none \
    $(find "$BUILD_DIR/gen" -name "*.java") $(find "$APP_DIR/java" -name "*.java") 2>&1 | grep -v "^Note:" || true
[ -n "$(find "$BUILD_DIR/classes" -name '*.class')" ] || { echo "Xato: kompilyatsiya bo'lmadi"; exit 1; }

echo "3. DEX..."
"$BUILD_TOOLS/d8" --output "$BUILD_DIR/dex" --lib "$PLATFORM" --min-api 30 $(find "$BUILD_DIR/classes" -name "*.class")
cp "$BUILD_DIR/unaligned_res.apk" "$BUILD_DIR/app_unsigned.apk"
(cd "$BUILD_DIR/dex" && "$JAVA_HOME/bin/jar" -uf "$BUILD_DIR/app_unsigned.apk" classes.dex)
"$BUILD_TOOLS/zipalign" -p -f 4 "$BUILD_DIR/app_unsigned.apk" "$BUILD_DIR/app_aligned.apk"

echo "4. Imzo..."
KEYSTORE="$DIR/release.keystore"   # yangilanishlar shu kalit bilan imzolanadi — fayl yo'qolsa ilovani qayta o'rnatish kerak
if [ ! -f "$KEYSTORE" ]; then
    "$JAVA_HOME/bin/keytool" -genkey -keystore "$KEYSTORE" -alias terryjar -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass terryjar -keypass terryjar -dname "CN=TerryJar Print, O=TerryJar, C=UZ" >/dev/null 2>&1
fi
OUTPUT_APK="$DIR/terryjar_print.apk"
"$BUILD_TOOLS/apksigner" sign --ks "$KEYSTORE" --ks-pass pass:terryjar --key-pass pass:terryjar --ks-key-alias terryjar \
    --out "$OUTPUT_APK" "$BUILD_DIR/app_aligned.apk"
"$BUILD_TOOLS/apksigner" verify "$OUTPUT_APK"
rm -rf "$BUILD_DIR" "$OUTPUT_APK.idsig"
echo "✅ Tayyor: $OUTPUT_APK ($(ls -lh "$OUTPUT_APK" | awk '{print $5}'))"
