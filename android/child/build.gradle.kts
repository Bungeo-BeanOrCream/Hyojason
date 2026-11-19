import java.net.URI

plugins {
    id("com.android.library")
    kotlin("android")
}

val backendUrl = providers.gradleProperty("hyojasonBackendUrl").map { value ->
    val url = runCatching { URI(value.trim()) }.getOrNull()
    require(
        url != null && url.scheme == "https" && url.host != null &&
            (url.port == -1 || url.port in 1..65535) &&
            url.rawUserInfo == null && url.rawQuery == null && url.rawFragment == null &&
            url.rawPath in listOf("", "/")
    ) {
        "hyojasonBackendUrl must be an HTTPS origin, e.g. https://demo.example.com"
    }
    url.toASCIIString().trimEnd('/')
}.getOrElse("http://10.0.2.2:8000")

android {
    namespace = "com.hyojason.app.child"
    compileSdk = 35

    defaultConfig {
        minSdk = 35
        buildConfigField("String", "BACKEND_URL", "\"$backendUrl\"")
    }
    buildFeatures {
        buildConfig = true
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
