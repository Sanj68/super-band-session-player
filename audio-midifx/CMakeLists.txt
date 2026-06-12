cmake_minimum_required(VERSION 3.22)

project(SessionPlayerMidiFX VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "Minimum macOS version" FORCE)

include(FetchContent)

set(JUCE_DIR "$ENV{JUCE_DIR}" CACHE PATH "Path to JUCE checkout")
if(NOT JUCE_DIR)
    set(JUCE_DIR "/Users/sub/Work/Code/Projects/JUCE")
endif()

FetchContent_Declare(
    JUCE
    SOURCE_DIR "${JUCE_DIR}"
)
FetchContent_MakeAvailable(JUCE)

# Logic MIDI FX slot plugin (AU type aumi): generates the bass MIDI that
# drives whatever instrument sits below it (BUILD_NOTES §6b).
juce_add_plugin(SessionPlayerMidiFX
    COMPANY_NAME "Sub One"
    COMPANY_WEBSITE "https://localhost"
    COMPANY_EMAIL "suboneproductions@gmail.com"
    BUNDLE_ID "com.subone.sessionplayer.midifx"
    PLUGIN_MANUFACTURER_CODE SOne
    PLUGIN_CODE SpMx
    FORMATS AU
    PRODUCT_NAME "Session Player Bass"
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT TRUE
    NEEDS_MIDI_OUTPUT TRUE
    IS_MIDI_EFFECT TRUE
    EDITOR_WANTS_KEYBOARD_FOCUS FALSE
    COPY_PLUGIN_AFTER_BUILD TRUE
)

target_sources(SessionPlayerMidiFX
    PRIVATE
        Source/PluginProcessor.cpp
        Source/PluginEditor.cpp
)

target_compile_definitions(SessionPlayerMidiFX
    PUBLIC
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_VST3_CAN_REPLACE_VST2=0
)

target_link_libraries(SessionPlayerMidiFX
    PRIVATE
        juce::juce_audio_utils
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags
)
