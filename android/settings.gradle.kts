pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // LiveKit Android SDK의 audioswitch 의존성만 jitpack에서 받는다.
        maven("https://jitpack.io") { content { includeGroup("com.github.davidliu") } }
    }
}

rootProject.name = "Hyojason"
include(":app", ":contract", ":observer", ":overlay", ":rtc", ":child")
