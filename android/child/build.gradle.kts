plugins {
    id("com.android.library")
    kotlin("android")
}

android {
    namespace = "com.hyojason.app.child"
    compileSdk = 35

    defaultConfig {
        minSdk = 35
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
    implementation(project(":contract"))
    implementation(project(":rtc"))
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    testImplementation("junit:junit:4.13.2")
}
