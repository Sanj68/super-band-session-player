cmake_minimum_required(VERSION 3.22)

project(SessionPlayerListenerAU VERSION 0.1.0 LANGUAGES C CXX)

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

juce_add_plugin(SessionPlayerListener
    COMPANY_NAME "Sub One"
    COMPANY_WEBSITE "https://localhost"
    COMPANY_EMAIL "suboneproductions@gmail.com"
    BUNDLE_ID "com.subone.sessionplayer.listener"
    PLUGIN_MANUFACTURER_CODE SOne
    PLUGIN_CODE SpLi
    FORMATS AU
    PRODUCT_NAME "Session Player Listener"
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT FALSE
    NEEDS_MIDI_OUTPUT FALSE
    IS_MIDI_EFFECT FALSE
    EDITOR_WANTS_KEYBOARD_FOCUS FALSE
    COPY_PLUGIN_AFTER_BUILD FALSE
)

target_sources(SessionPlayerListener
    PRIVATE
        Source/PluginProcessor.cpp
        Source/PluginEditor.cpp
)

target_compile_definitions(SessionPlayerListener
    PUBLIC
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_DISPLAY_SPLASH_SCREEN=0
        JUCE_REPORT_APP_USAGE=0
)

target_link_libraries(SessionPlayerListener
    PRIVATE
        juce::juce_audio_utils
        juce::juce_dsp
        juce::juce_gui_extra
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags
)

if(APPLE)
    add_custom_command(TARGET SessionPlayerListener_AU POST_BUILD
        COMMAND /usr/bin/codesign --force --sign - "$<TARGET_BUNDLE_DIR:SessionPlayerListener_AU>"
        COMMENT "Ad-hoc signing Session Player Listener AU"
    )
endif()
