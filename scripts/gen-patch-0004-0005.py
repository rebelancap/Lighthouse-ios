#!/usr/bin/env python3
"""Overlay patches:
0004 — root CMakeLists.txt iOS app target (bundle, shell sources, SDL2_net for
       co-op, static audio deps, resource bundling, port versioning, console
       gate) + NEW FILE ios/Info.plist.in.
0005 — LUS Context.cpp: GetAppBundlePath on iOS must be the REAL .app bundle
       (read-only resources: lighthouse.o2r, the extractor's assets/ yaml
       tree), not $HOME/Documents. GetAppDirectoryPath (writable: bk.o2r,
       config, saves, mods) stays Documents.

Note on upstream's iOS branch: it referenced
libultraship/ios/{Launch.storyboard,PoweredBy.png,Icon.png,plist.in} — NONE of
which exist in this fork (`ls libultraship/ios` -> no such directory). So the
branch could never have configured; it is aspirational scaffolding. We keep its
shape and replace the dead file references.

All edits are match-count asserted against the PRISTINE vendor state. Patch
files are OUTPUTS (program rule D9) — regenerate from a clean vendor checkout,
then run scripts/apply-overlay.sh.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/Lighthouse"


def replace_once(text, old, new, tag):
    n = text.count(old)
    assert n == 1, f"[{tag}] expected exactly 1 match, got {n}: {old[:70]!r}"
    return text.replace(old, new)


def unified(a, b, rel):
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(a); fb.write(b); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True, text=True)
    assert r.returncode == 1, f"no diff for {rel}"
    return r.stdout


def new_file_diff(content, rel):
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(""); fb.write(content); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", "/dev/null", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True, text=True)
    assert r.returncode == 1
    out = r.stdout.replace("--- /dev/null", "--- /dev/null\t1970-01-01 00:00:00", 1)
    return "new file mode 100644\n" + out


INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>CFBundleDevelopmentRegion</key>
\t<string>en</string>
\t<key>CFBundleDisplayName</key>
\t<string>Lighthouse</string>
\t<key>CFBundleExecutable</key>
\t<string>Lighthouse</string>
\t<key>CFBundleIdentifier</key>
\t<string>@LIGHTHOUSE_IOS_BUNDLE_IDENTIFIER@</string>
\t<key>CFBundleInfoDictionaryVersion</key>
\t<string>6.0</string>
\t<key>CFBundleName</key>
\t<string>Lighthouse</string>
\t<key>CFBundlePackageType</key>
\t<string>APPL</string>
\t<key>CFBundleIconName</key>
\t<string>AppIcon</string>
\t<key>CFBundleShortVersionString</key>
\t<string>@LIGHTHOUSE_IOS_VERSION@</string>
\t<key>CFBundleVersion</key>
\t<string>@LIGHTHOUSE_IOS_BUILD@</string>
\t<key>LSRequiresIPhoneOS</key>
\t<true/>
\t<key>UIFileSharingEnabled</key>
\t<true/>
\t<key>LSSupportsOpeningDocumentsInPlace</key>
\t<true/>
\t<key>UIStatusBarHidden</key>
\t<true/>
\t<key>UIRequiresFullScreen</key>
\t<true/>
\t<key>UILaunchScreen</key>
\t<dict/>
\t<key>UIRequiredDeviceCapabilities</key>
\t<array>
\t\t<string>arm64</string>
\t</array>
\t<key>UISupportedInterfaceOrientations</key>
\t<array>
\t\t<string>UIInterfaceOrientationLandscapeRight</string>
\t\t<string>UIInterfaceOrientationLandscapeLeft</string>
\t</array>
\t<key>CADisableMinimumFrameDurationOnPhone</key>
\t<true/>
\t<key>UIApplicationSupportsIndirectInputEvents</key>
\t<true/>
\t<key>ITSAppUsesNonExemptEncryption</key>
\t<false/>
\t<key>NSLocalNetworkUsageDescription</key>
\t<string>Lighthouse uses the local network to host or join co-op (Anchor) sessions with players on your network, and for the developer console when you enable it.</string>
\t<key>CFBundleURLTypes</key>
\t<array>
\t\t<dict>
\t\t\t<key>CFBundleURLName</key>
\t\t\t<string>@LIGHTHOUSE_IOS_BUNDLE_IDENTIFIER@</string>
\t\t\t<key>CFBundleURLSchemes</key>
\t\t\t<array>
\t\t\t\t<string>lighthouse</string>
\t\t\t</array>
\t\t</dict>
\t</array>@LIGHTHOUSE_PLIST_EXTRA@
</dict>
</plist>
"""

# ---- the iOS executable/bundle branch (replaces upstream's dead-file body) ----
IOS_BRANCH_OLD = '''if (CMAKE_SYSTEM_NAME STREQUAL "iOS")
    set(IOS_DIR ${CMAKE_CURRENT_SOURCE_DIR}/libultraship/ios)

    set(STORYBOARD_FILE ${IOS_DIR}/Launch.storyboard)
    set(IMAGE_FILES ${IOS_DIR}/PoweredBy.png)
    set(ICON_FILES ${IOS_DIR}/Icon.png)

    list(APPEND ALL_FILES ${STORYBOARD_FILE} ${IMAGE_FILES} ${ICON_FILES})

    add_executable(${PROJECT_NAME} ${ALL_FILES})
    set_xcode_property(${PROJECT_NAME} PRODUCT_BUNDLE_IDENTIFIER ${PROJECT_ID} All)
    set_target_properties(
        ${PROJECT_NAME}
        PROPERTIES
        MACOSX_BUNDLE TRUE
        MACOSX_BUNDLE_INFO_PLIST ${IOS_DIR}/plist.in
        RESOURCE "${IMAGE_FILES};${STORYBOARD_FILE};${ICON_FILES}"
    )
else()
    add_executable(${PROJECT_NAME} ${ALL_FILES})
endif()
'''

IOS_BRANCH_NEW = '''if (CMAKE_SYSTEM_NAME STREQUAL "iOS")
    # LIGHTHOUSE_IOS: upstream's branch here referenced
    # libultraship/ios/{Launch.storyboard,PoweredBy.png,Icon.png,plist.in},
    # none of which exist in this fork — the branch could never configure.
    # Replaced with this port's own plist, shell and resources.
    add_executable(${PROJECT_NAME} ${ALL_FILES})

    # LUS defines __IOS__ only PRIVATE to itself; the game's TUs (and the LUS
    # headers they include) need it too.
    target_compile_definitions(${PROJECT_NAME} PRIVATE __IOS__)

    # The app shell (scene graft, touch controls, console bridge, crash
    # handler) lives in the Lighthouse-ios repo, passed in by the build script.
    if(NOT EXISTS "${LIGHTHOUSE_IOS_SHELL_DIR}/SohIosShell.m")
        message(FATAL_ERROR "iOS build requires -DLIGHTHOUSE_IOS_SHELL_DIR=<dir with SohIosShell.m>")
    endif()
    target_sources(${PROJECT_NAME} PRIVATE "${LIGHTHOUSE_IOS_SHELL_DIR}/SohIosShell.m")
    target_include_directories(${PROJECT_NAME} PRIVATE "${LIGHTHOUSE_IOS_SHELL_DIR}")

    # Remote console bridge: an UNAUTHENTICATED TCP command server that injects
    # input, writes CVars and reads crash.txt/logs — and lighthouse://console
    # can switch it on from a tapped link. Compiled OUT unless asked for, so
    # public releases cannot expose it at all. Dev/OTA builds opt in.
    option(LIGHTHOUSE_REMOTE_CONSOLE "Compile in the remote console bridge (dev/OTA builds only)" OFF)
    if(LIGHTHOUSE_REMOTE_CONSOLE)
        target_compile_definitions(${PROJECT_NAME} PRIVATE SOH_REMOTE_CONSOLE=1)
        message(STATUS "lighthouse: REMOTE CONSOLE COMPILED IN — do not publish this build")
    else()
        message(STATUS "lighthouse: remote console compiled out (public-safe)")
    endif()

    # UTType (document-picker ROM onboarding) + GameController (physical pads).
    target_link_libraries(${PROJECT_NAME} PRIVATE "-framework UniformTypeIdentifiers"
                                                  "-framework GameController")

    # App icon catalog (compiled by actool under the Xcode generator).
    if(EXISTS "${LIGHTHOUSE_IOS_SHELL_DIR}/Assets.xcassets")
        target_sources(${PROJECT_NAME} PRIVATE "${LIGHTHOUSE_IOS_SHELL_DIR}/Assets.xcassets")
        set_source_files_properties("${LIGHTHOUSE_IOS_SHELL_DIR}/Assets.xcassets"
            PROPERTIES MACOSX_PACKAGE_LOCATION Resources)
    endif()

    # --- visionOS: SwiftUI app entry + compositor (playbook §1.2, §1.3) -----
    # LIGHTHOUSE_VISIONOS comes from overlay 0022 (it flips CMAKE_SYSTEM_NAME
    # back to "iOS" so this whole branch runs, and leaves that marker behind).
    if(LIGHTHOUSE_VISIONOS)
        # The SwiftUI entry lives in its OWN static lib. The Xcode generator
        # ignores COMPILE_LANGUAGE genexes when composing OTHER_SWIFT_FLAGS,
        # so any .swift file added to the app target inherits its C warning
        # flags and swiftc hard-errors on them. A separate flag-clean target
        # sidesteps the entire class of problem. Swift's @main emits main();
        # the linker extracts it from the archive exactly the way it would
        # from SDL2main's — which is NOT linked on visionOS (see below).
        enable_language(Swift)
        add_library(lighthousevisionswift STATIC "${LIGHTHOUSE_IOS_SHELL_DIR}/SohVisionApp.swift")
        set_target_properties(lighthousevisionswift PROPERTIES
            XCODE_ATTRIBUTE_SWIFT_VERSION "5.0"
            XCODE_ATTRIBUTE_SWIFT_OBJC_BRIDGING_HEADER "${LIGHTHOUSE_IOS_SHELL_DIR}/SohVision-Bridging-Header.h")
        target_sources(${PROJECT_NAME} PRIVATE
            "${LIGHTHOUSE_IOS_SHELL_DIR}/SohHostViewController.m"
            "${LIGHTHOUSE_IOS_SHELL_DIR}/SohImmersive.m")
        # force_load the archive: main() lives there and nothing else in the
        # link references the Swift objects, so without it they are dropped.
        # The SDK swift dir has to be added by hand because the app target
        # itself has no Swift sources for Xcode to infer it from.
        target_link_libraries(${PROJECT_NAME} PRIVATE
            "-Wl,-force_load,$<TARGET_FILE:lighthousevisionswift>"
            "-L$(SDKROOT)/usr/lib/swift"
            "-framework CompositorServices" "-framework ARKit" "-framework AVFAudio")
        add_dependencies(${PROJECT_NAME} lighthousevisionswift)
    endif()

    # lighthouse.o2r is a ROM-INDEPENDENT build artifact produced by a HOST
    # build (GeneratePortO2R) and bundled read-only. It carries the port assets
    # AND the LUS shaders — the renderer does not come up without it.
    set(LIGHTHOUSE_O2R_PATH "" CACHE FILEPATH "Path to a host-built lighthouse.o2r to bundle")
    if(NOT EXISTS "${LIGHTHOUSE_O2R_PATH}")
        message(FATAL_ERROR "iOS build requires -DLIGHTHOUSE_O2R_PATH=<host-built lighthouse.o2r>")
    endif()
    target_sources(${PROJECT_NAME} PRIVATE "${LIGHTHOUSE_O2R_PATH}")
    set_source_files_properties("${LIGHTHOUSE_O2R_PATH}" PROPERTIES MACOSX_PACKAGE_LOCATION Resources)

    # The extractor's asset metadata (assets/yaml/** + config.yml) must ship in
    # the bundle: Torch runs IN-PROCESS on device and reads these to turn the
    # user's ROM into bk.o2r. Without them the first-run extraction cannot start.
    if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/assets/yaml")
        message(FATAL_ERROR "iOS build requires the extractor assets at assets/yaml")
    endif()
    target_sources(${PROJECT_NAME} PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/assets"
        "${CMAKE_CURRENT_SOURCE_DIR}/config.yml")
    set_source_files_properties(
        "${CMAKE_CURRENT_SOURCE_DIR}/assets"
        "${CMAKE_CURRENT_SOURCE_DIR}/config.yml"
        PROPERTIES MACOSX_PACKAGE_LOCATION Resources)

    # Port versioning — deliberately NOT upstream's CMAKE_PROJECT_VERSION.
    # LIGHTHOUSE_IOS_VERSION (CFBundleShortVersionString) is the PUBLIC version
    # and the string SideStore compares to decide "is there an update": it moves
    # only when a release is cut and stays put across OTA test iterations.
    # LIGHTHOUSE_IOS_BUILD (CFBundleVersion) increments every build instead —
    # that is what distinguishes those iterations and what makes iOS treat each
    # OTA install as genuinely new. Both come from the build scripts (VERSION
    # file + UTC timestamp).
    set(LIGHTHOUSE_IOS_BUNDLE_IDENTIFIER "com.rebelancap.lighthouse" CACHE STRING "iOS bundle identifier")
    set(LIGHTHOUSE_IOS_DEVELOPMENT_TEAM "" CACHE STRING "Development team for iOS code signing")
    set(LIGHTHOUSE_IOS_VERSION "0.0.0" CACHE STRING "Public (marketing) version of this port")
    set(LIGHTHOUSE_IOS_BUILD "0" CACHE STRING "Build number; must increase every build")
    # The SwiftUI entry's ImmersiveSpace (stereo 3D) needs multiple scenes.
    # visionOS ONLY: the key changes UIKit scene behaviour on iPhone, and
    # UIApplicationSupportsMultipleScenes must sit INSIDE the scene manifest
    # dict, not beside it (playbook §1 trap list).
    set(LIGHTHOUSE_PLIST_EXTRA "")
    set(LIGHTHOUSE_DEVICE_FAMILY "1,2")
    if(LIGHTHOUSE_VISIONOS)
        set(LIGHTHOUSE_DEVICE_FAMILY "7")
        set(LIGHTHOUSE_PLIST_EXTRA "
\t<key>UIApplicationSceneManifest</key>
\t<dict>
\t\t<key>UIApplicationSupportsMultipleScenes</key>
\t\t<true/>
\t</dict>")
    endif()
    configure_file(${CMAKE_CURRENT_SOURCE_DIR}/ios/Info.plist.in ${CMAKE_BINARY_DIR}/ios/Info.plist @ONLY)

    set_target_properties(
        ${PROJECT_NAME}
        PROPERTIES
        XCODE_ATTRIBUTE_CLANG_ENABLE_OBJC_ARC YES
        OUTPUT_NAME "Lighthouse"
        MACOSX_BUNDLE TRUE
        MACOSX_BUNDLE_INFO_PLIST "${CMAKE_BINARY_DIR}/ios/Info.plist"
        XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER "${LIGHTHOUSE_IOS_BUNDLE_IDENTIFIER}"
        XCODE_ATTRIBUTE_DEVELOPMENT_TEAM "${LIGHTHOUSE_IOS_DEVELOPMENT_TEAM}"
        XCODE_ATTRIBUTE_CODE_SIGN_STYLE "Automatic"
        XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY "${LIGHTHOUSE_DEVICE_FAMILY}"
        XCODE_ATTRIBUTE_ASSETCATALOG_COMPILER_APPICON_NAME "AppIcon"
        # Required for xcodebuild archive to produce an APP archive (not a
        # generic one) from a CMake-generated project.
        XCODE_ATTRIBUTE_INSTALL_PATH "$(LOCAL_APPS_DIR)"
        XCODE_ATTRIBUTE_SKIP_INSTALL "NO"
    )
else()
    add_executable(${PROJECT_NAME} ${ALL_FILES})
endif()
'''

# ---- iOS dependency branch, inserted before the generic else() ----
DEPS_OLD = '''else()
    find_package(Ogg REQUIRED)
    find_package(Vorbis REQUIRED)
'''

DEPS_NEW = '''elseif(CMAKE_SYSTEM_NAME STREQUAL "iOS")
    # SDL2 comes statically from libultraship's FetchContent (OVERRIDE_FIND_PACKAGE).
    find_package(SDL2 REQUIRED)
    set(THREADS_PREFER_PTHREAD_FLAG ON)
    find_package(Threads REQUIRED)
    # Static audio codecs are prebuilt for iOS and injected by prefix
    # (scripts/build-audio-deps-ios.sh) — homebrew's are macOS-only.
    set(LIGHTHOUSE_IOS_DEPS_PREFIX "" CACHE PATH "Prefix with static ogg/vorbis/png for iOS")
    foreach(_l ogg vorbis vorbisenc vorbisfile)
        if(NOT EXISTS "${LIGHTHOUSE_IOS_DEPS_PREFIX}/lib/lib${_l}.a")
            message(FATAL_ERROR "iOS build requires -DLIGHTHOUSE_IOS_DEPS_PREFIX with lib/lib${_l}.a (got '${LIGHTHOUSE_IOS_DEPS_PREFIX}')")
        endif()
    endforeach()
    add_library(Ogg::ogg STATIC IMPORTED)
    set_target_properties(Ogg::ogg PROPERTIES IMPORTED_LOCATION "${LIGHTHOUSE_IOS_DEPS_PREFIX}/lib/libogg.a"
        INTERFACE_INCLUDE_DIRECTORIES "${LIGHTHOUSE_IOS_DEPS_PREFIX}/include")
    add_library(Vorbis::vorbis STATIC IMPORTED)
    set_target_properties(Vorbis::vorbis PROPERTIES IMPORTED_LOCATION "${LIGHTHOUSE_IOS_DEPS_PREFIX}/lib/libvorbis.a"
        INTERFACE_INCLUDE_DIRECTORIES "${LIGHTHOUSE_IOS_DEPS_PREFIX}/include")
    add_library(Vorbis::vorbisenc STATIC IMPORTED)
    set_target_properties(Vorbis::vorbisenc PROPERTIES IMPORTED_LOCATION "${LIGHTHOUSE_IOS_DEPS_PREFIX}/lib/libvorbisenc.a"
        INTERFACE_INCLUDE_DIRECTORIES "${LIGHTHOUSE_IOS_DEPS_PREFIX}/include")
    add_library(Vorbis::vorbisfile STATIC IMPORTED)
    set_target_properties(Vorbis::vorbisfile PROPERTIES IMPORTED_LOCATION "${LIGHTHOUSE_IOS_DEPS_PREFIX}/lib/libvorbisfile.a"
        INTERFACE_INCLUDE_DIRECTORIES "${LIGHTHOUSE_IOS_DEPS_PREFIX}/include")
    set(ADDITIONAL_LIBRARY_DEPENDENCIES
        SDL2::SDL2-static
        # SDL2main's UIKit shim supplies main() on iPhone. On visionOS the
        # Swift @main App is the entry instead, and linking both would be two
        # definitions of main — so it is dropped there (playbook §1 trap).
        "$<$<NOT:$<BOOL:${LIGHTHOUSE_VISIONOS}>>:SDL2main>"
        "$<$<BOOL:${USE_NETWORKING}>:SDL2_net>"
        "Ogg::ogg"
        "Vorbis::vorbis"
        "Vorbis::vorbisenc"
        "Vorbis::vorbisfile"
        ${CMAKE_DL_LIBS}
        Threads::Threads
    )
else()
    find_package(Ogg REQUIRED)
    find_package(Vorbis REQUIRED)
'''

# ---- SDL2_net: find_package fails on iOS, fetch it instead ----
NET_OLD = '''    find_package(SDL2_net REQUIRED)
    include_directories(${SDL2_NET_INCLUDE_DIRS})
'''

NET_NEW = '''    if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
        # No system SDL2_net when cross-compiling; fetch it static. Its
        # cmake_minimum_required predates CMake 4, hence the policy floor.
        # Network.h includes <SDL2/SDL_net.h> unconditionally, but the fetched
        # tree exports SDL_net.h at its root — shim the expected layout.
        include(FetchContent)
        set(CMAKE_POLICY_VERSION_MINIMUM 3.5)
        FetchContent_Declare(
            SDL2_net
            GIT_REPOSITORY https://github.com/libsdl-org/SDL_net.git
            GIT_TAG release-2.2.0
        )
        # SDL_net's BUILD_SHARED_LIBS defaults ON, and it picks its SDL2 target
        # from that: shared -> SDL2::SDL2, static -> SDL2::SDL2-static. LUS
        # builds SDL2 STATIC, so leaving the default on makes SDL2's own
        # consistency check fail at generate time with
        #   "The INTERFACE_SDL2_SHARED property of SDL2_net does not agree
        #    with the value of SDL2_SHARED already determined for Lighthouse"
        # which reads like a bug in our code and is not. Force static, then
        # restore the caller's value so nothing downstream inherits it.
        set(_lighthouse_saved_shared ${BUILD_SHARED_LIBS})
        set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
        FetchContent_MakeAvailable(SDL2_net)
        set(BUILD_SHARED_LIBS ${_lighthouse_saved_shared} CACHE BOOL "" FORCE)
        file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/sdl2net-shim/SDL2")
        file(COPY_FILE "${sdl2_net_SOURCE_DIR}/SDL_net.h"
                       "${CMAKE_BINARY_DIR}/sdl2net-shim/SDL2/SDL_net.h" ONLY_IF_DIFFERENT)
        target_include_directories(${PROJECT_NAME} PRIVATE "${CMAKE_BINARY_DIR}/sdl2net-shim")
    else()
        find_package(SDL2_net REQUIRED)
        include_directories(${SDL2_NET_INCLUDE_DIRS})
    endif()
'''


def gen_0004():
    src = VENDOR / "CMakeLists.txt"
    rel = "CMakeLists.txt"
    orig = src.read_text()
    t = orig
    t = replace_once(t, IOS_BRANCH_OLD, IOS_BRANCH_NEW, "ios-branch")
    t = replace_once(t, DEPS_OLD, DEPS_NEW, "ios-deps")
    t = replace_once(t, NET_OLD, NET_NEW, "sdl2net")
    body = unified(orig, t, rel)
    body += new_file_diff(INFO_PLIST, "ios/Info.plist.in")
    out = ROOT / "overlay/patches/0004-lighthouse-cmake-ios-app-target.patch"
    out.write_text(__doc__ + "\n\n" + body)
    print(f"wrote {out}")


def gen_0005():
    src = VENDOR / "libultraship/src/ship/Context.cpp"
    rel = "libultraship/src/ship/Context.cpp"
    orig = src.read_text()
    # NOTE: this exact #ifdef __IOS__ body appears TWICE — in GetAppBundlePath()
    # and in GetAppDirectoryPath(). Only the FIRST is wrong; GetAppDirectoryPath
    # must keep returning Documents (it is the writable dir: bk.o2r, config,
    # saves, mods). The match-count assert caught this; anchor on the enclosing
    # function signature so the writable path can never be patched by accident.
    PREFIX = ('std::string Context::GetAppBundlePath() {\n'
              '#if defined(__ANDROID__)\n'
              '    const char* externaldir = SDL_AndroidGetExternalStoragePath();\n'
              '    if (externaldir != NULL) {\n'
              '        return externaldir;\n'
              '    }\n'
              '#endif\n'
              '\n')
    old = PREFIX + ('#ifdef __IOS__\n'
           '    const char* home = getenv("HOME");\n'
           '    return std::string(home) + "/Documents";\n'
           '#endif\n')
    new = PREFIX + ('#ifdef __IOS__\n'
           '    // LIGHTHOUSE_IOS: the BUNDLE path must be the real read-only .app bundle —\n'
           '    // it is where lighthouse.o2r and the extractor\'s assets/ yaml tree ship.\n'
           '    // Returning Documents here made every bundled resource unfindable while\n'
           '    // also hiding the writable dir\'s distinct role. GetAppDirectoryPath()\n'
           '    // (writable: bk.o2r, config, saves, mods) still resolves to Documents.\n'
           '    {\n'
           '        CFBundleRef bundle = CFBundleGetMainBundle();\n'
           '        if (bundle != nullptr) {\n'
           '            CFURLRef url = CFBundleCopyBundleURL(bundle);\n'
           '            if (url != nullptr) {\n'
           '                char path[PATH_MAX] = { 0 };\n'
           '                const bool ok = CFURLGetFileSystemRepresentation(\n'
           '                    url, TRUE, reinterpret_cast<UInt8*>(path), sizeof(path));\n'
           '                CFRelease(url);\n'
           '                if (ok && path[0] != \'\\0\') {\n'
           '                    return std::string(path);\n'
           '                }\n'
           '            }\n'
           '        }\n'
           '    }\n'
           '    // Fall back to the old behaviour rather than returning empty.\n'
           '    {\n'
           '        const char* home = getenv("HOME");\n'
           '        return home != nullptr ? std::string(home) + "/Documents" : std::string(".");\n'
           '    }\n'
           '#endif\n')
    t = replace_once(orig, old, new, "bundle-path")
    # CoreFoundation for CFBundle* — the file already includes <SDL2/SDL.h> etc.
    inc_old = '#include "ship/Context.h"\n'
    inc_new = ('#include "ship/Context.h"\n'
               '#ifdef __IOS__\n'
               '#include <CoreFoundation/CoreFoundation.h>\n'
               '#include <climits>\n'
               '#endif\n')
    t = replace_once(t, inc_old, inc_new, "cf-include")
    out = ROOT / "overlay/patches/0005-lus-context-ios-real-bundle-path.patch"
    out.write_text(__doc__ + "\n\n" + unified(orig, t, rel))
    print(f"wrote {out}")


if __name__ == "__main__":
    gen_0004()
    gen_0005()
