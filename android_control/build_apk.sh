#!/bin/bash
set -e

# Sifat Nazorati (OTK / Control) Plansheti uchun APK yig'ish skripti

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=== Terry Jar Control APK Yig'ish Boshlandi ==="

# Java SDK yo'li (Android Studio JBR)
if [ -d "/Applications/Android Studio.app/Contents/jbr/Contents/Home" ]; then
    export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
else
    export JAVA_HOME="$(/usr/libexec/java_home 2>/dev/null || echo '')"
fi

export PATH="$JAVA_HOME/bin:$PATH"

# Android SDK va vositalar yo'li
SDK_ROOT="/opt/homebrew/share/android-commandlinetools"
BUILD_TOOLS="$SDK_ROOT/build-tools/34.0.0"
PLATFORM="$SDK_ROOT/platforms/android-34/android.jar"
AAPT2="$BUILD_TOOLS/aapt2"
D8="$BUILD_TOOLS/d8"
ZIPALIGN="$BUILD_TOOLS/zipalign"
APKSIGNER="$BUILD_TOOLS/apksigner"

JAVAC="$JAVA_HOME/bin/javac"
KEYTOOL="$JAVA_HOME/bin/keytool"
JAR="$JAVA_HOME/bin/jar"

if [ ! -f "$AAPT2" ]; then
    echo "Xato: aapt2 topilmadi: $AAPT2"
    exit 1
fi
if [ ! -f "$PLATFORM" ]; then
    echo "Xato: android.jar topilmadi: $PLATFORM"
    exit 1
fi
if [ ! -f "$JAVAC" ]; then
    echo "Xato: javac topilmadi: $JAVAC"
    exit 1
fi

BUILD_DIR="$DIR/build_temp"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/compiled_res"
mkdir -p "$BUILD_DIR/gen"
mkdir -p "$BUILD_DIR/classes"
mkdir -p "$BUILD_DIR/dex"

APP_DIR="$DIR/app/src/main"
MANIFEST="$APP_DIR/AndroidManifest.xml"
RES_DIR="$APP_DIR/res"
JAVA_SRC_DIR="$APP_DIR/java"

echo "1. Resurslar kompilyatsiya qilinmoqda (aapt2 compile)..."
"$AAPT2" compile --dir "$RES_DIR" -o "$BUILD_DIR/compiled_res.zip"

echo "2. Resurslar bog'lanmoqda va R.java yaratilmoqda (aapt2 link)..."
"$AAPT2" link -I "$PLATFORM" \
    --manifest "$MANIFEST" \
    --java "$BUILD_DIR/gen" \
    -o "$BUILD_DIR/unaligned_res.apk" \
    "$BUILD_DIR/compiled_res.zip" \
    --auto-add-overlay

echo "3. Java fayllar kompilyatsiya qilinmoqda (javac)..."
"$JAVAC" -source 8 -target 8 \
    -bootclasspath "$PLATFORM" \
    -cp "$PLATFORM" \
    -d "$BUILD_DIR/classes" \
    $(find "$BUILD_DIR/gen" -name "*.java") \
    $(find "$JAVA_SRC_DIR" -name "*.java")

echo "4. Bytecode DEX formatiga o'tkazilmoqda (d8)..."
"$D8" --output "$BUILD_DIR/dex" \
    --lib "$PLATFORM" \
    --min-api 21 \
    $(find "$BUILD_DIR/classes" -name "*.class")

echo "5. classes.dex APK ga joylashtirilmoqda..."
cp "$BUILD_DIR/unaligned_res.apk" "$BUILD_DIR/app_unsigned.apk"
cd "$BUILD_DIR/dex"
"$JAR" -uf "$BUILD_DIR/app_unsigned.apk" classes.dex
cd "$DIR"

echo "6. Zipalign qilinmoqda..."
"$ZIPALIGN" -p -f 4 "$BUILD_DIR/app_unsigned.apk" "$BUILD_DIR/app_aligned.apk"

echo "7. Keystore tekshirilmoqda / yaratilmoqda..."
KEYSTORE="$DIR/debug.keystore"
if [ ! -f "$KEYSTORE" ]; then
    "$KEYTOOL" -genkey -v -keystore "$KEYSTORE" \
        -alias androiddebugkey \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass android -keypass android \
        -dname "CN=TerryJar, OU=Factory, O=TerryJar, L=Tashkent, C=UZ"
fi

echo "8. APK imzolanmoqda (apksigner)..."
OUTPUT_APK="$DIR/terryjar_control.apk"
"$APKSIGNER" sign --ks "$KEYSTORE" \
    --ks-pass pass:android \
    --key-pass pass:android \
    --ks-key-alias androiddebugkey \
    --out "$OUTPUT_APK" \
    "$BUILD_DIR/app_aligned.apk"

echo "9. APK imzosi tekshirilmoqda..."
"$APKSIGNER" verify "$OUTPUT_APK"

# Tozalash
rm -rf "$BUILD_DIR"

echo ""
echo "=========================================================="
echo "✅ TAYYOR! APK muvaffaqiyatli yig'ildi:"
echo "   Fayl manzili: $OUTPUT_APK"
echo "   Hajmi: $(ls -lh "$OUTPUT_APK" | awk '{print $5}')"
echo "=========================================================="
