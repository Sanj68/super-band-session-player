cmake_minimum_required(VERSION 3.22)

project(SessionPlayerBridgeAU VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "Minimum macOS version" FORCE)

set(JUCE_DIR "$ENV{JUCE_DIR}" CACHE PATH "Path to JUCE checkout")
if(NOT JUCE_DIR)
    set(JUCE_DIR "/Users/sub/Work/Code/Projects/JUCE")
endif()

add_subdirectory("${JUCE_DIR}" JUCE)

juce_add_plugin(SessionPlayerBridge
    COMPANY_NAME "Sub One"
    COMPANY_WEBSITE "https://localhost"
    COMPANY_EMAIL "suboneproductions@gmail.com"
    BUNDLE_ID "com.subone.sessionplayer.bridge"
    PLUGIN_MANUFACTURER_CODE SOne
    PLUGIN_CODE SpBr
    FORMATS AU
    PRODUCT_NAME "Session Player Bridge"
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT FALSE
    NEEDS_MIDI_OUTPUT FALSE
    IS_MIDI_EFFECT FALSE
    EDITOR_WANTS_KEYBOARD_FOCUS FALSE
    COPY_PLUGIN_AFTER_BUILD FALSE
)

target_sources(SessionPlayerBridge
    PRIVATE
        Source/PluginProcessor.cpp
)

target_compile_definitions(SessionPlayerBridge
    PUBLIC
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_DISPLAY_SPLASH_SCREEN=0
        JUCE_REPORT_APP_USAGE=0
)

target_link_libraries(SessionPlayerBridge
    PRIVATE
        juce::juce_audio_utils
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags
)

if(APPLE)
    add_custom_command(TARGET SessionPlayerBridge_AU POST_BUILD
        COMMAND /usr/bin/codesign --force --sign - "$<TARGET_BUNDLE_DIR:SessionPlayerBridge_AU>"
        COMMENT "Ad-hoc signing Session Player Bridge AU"
    )
endif()
