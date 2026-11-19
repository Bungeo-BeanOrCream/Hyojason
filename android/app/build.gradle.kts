plugins {
    id("com.android.application")
    kotlin("android")
}

android {
    namespace = "com.hyojason.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.hyojason.app"
        minSdk = 35
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions.jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
}

dependencies {
    implementation(project(":observer"))
    implementation(project(":overlay"))
    implementation(project(":rtc"))
    implementation(project(":child"))
    implementation(project(":contract"))
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    debugImplementation("com.google.code.gson:gson:2.11.0")
    testImplementation("junit:junit:4.13.2")
}
